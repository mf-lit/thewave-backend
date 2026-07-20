"""GRU seq2seq forecaster on CPU.

Encoder GRU consumes the most recent ``history_hours`` of weather + water_temp;
decoder GRU walks forward over the next 168 hours of *forecast* weather and
emits a residual on top of the persistence baseline. Loss = L1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch
from torch import nn

from lake_forecast.config import data_config, train_config
from lake_forecast.features.weather import rolling_weather_features
from lake_forecast.models.base import Forecaster

WEATHER_VARS = list(data_config()["openmeteo"]["variables"])


@dataclass
class GRUConfig:
    history_hours: int = 72
    horizon_hours: int = 168
    hidden: int = 64
    num_layers: int = 1
    dropout: float = 0.2
    lr: float = 1e-3
    batch_size: int = 64
    max_epochs: int = 50
    patience: int = 6
    weight_decay: float = 1e-5
    seed: int = 42


class _Seq2Seq(nn.Module):
    def __init__(self, n_enc_feats: int, n_dec_feats: int, cfg: GRUConfig):
        super().__init__()
        self.encoder = nn.GRU(
            input_size=n_enc_feats,
            hidden_size=cfg.hidden,
            num_layers=cfg.num_layers,
            dropout=cfg.dropout if cfg.num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.decoder = nn.GRU(
            input_size=n_dec_feats,
            hidden_size=cfg.hidden,
            num_layers=cfg.num_layers,
            dropout=cfg.dropout if cfg.num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.head = nn.Sequential(
            nn.Linear(cfg.hidden, cfg.hidden),
            nn.ReLU(),
            nn.Dropout(cfg.dropout),
            nn.Linear(cfg.hidden, 1),
        )

    def forward(self, enc_x: torch.Tensor, dec_x: torch.Tensor) -> torch.Tensor:
        _, h = self.encoder(enc_x)
        dec_out, _ = self.decoder(dec_x, h)
        # Residual on top of constant t0 (passed through dec_x channel 0).
        residual = self.head(dec_out).squeeze(-1)
        t0 = dec_x[..., 0]  # convention: channel 0 of decoder = water_temp_t0 broadcast
        return t0 + residual


class GRUForecaster(Forecaster):
    name = "gru"

    def __init__(self, cfg: GRUConfig | None = None) -> None:
        tcfg = train_config()["models"]["gru"]
        self.cfg = cfg or GRUConfig(
            history_hours=int(tcfg["history_hours"]),
            horizon_hours=int(train_config()["forecast"]["horizon_hours"]),
            hidden=int(tcfg["hidden"]),
            num_layers=int(tcfg["num_layers"]),
            dropout=float(tcfg["dropout"]),
            lr=float(tcfg["lr"]),
            batch_size=int(tcfg["batch_size"]),
            max_epochs=int(tcfg["max_epochs"]),
            patience=int(tcfg["patience"]),
        )
        self.model: _Seq2Seq | None = None
        self.enc_mean: np.ndarray | None = None
        self.enc_std: np.ndarray | None = None
        self.dec_mean: np.ndarray | None = None
        self.dec_std: np.ndarray | None = None
        self.enc_feat_names: list[str] = []
        self.dec_feat_names: list[str] = []

    @classmethod
    def from_checkpoint(cls, ckpt_path) -> GRUForecaster:
        """Reconstruct a ready-to-predict forecaster from a saved gru.pt checkpoint."""
        ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
        self = cls(cfg=GRUConfig(**ckpt["cfg"]))
        self.enc_mean = np.asarray(ckpt["enc_mean"])
        self.enc_std = np.asarray(ckpt["enc_std"])
        self.dec_mean = np.asarray(ckpt["dec_mean"])
        self.dec_std = np.asarray(ckpt["dec_std"])
        self.enc_feat_names = list(ckpt.get("enc_feat_names", []))
        self.dec_feat_names = list(ckpt.get("dec_feat_names", []))
        model = _Seq2Seq(
            n_enc_feats=self.enc_mean.shape[0],
            n_dec_feats=self.dec_mean.shape[0],
            cfg=self.cfg,
        )
        model.load_state_dict(ckpt["state_dict"])
        model.eval()
        self.model = model
        return self

    # ----- sample assembly -----
    def _assemble(
        self,
        master: pd.DataFrame,
        issue_times: pd.DatetimeIndex,
        anchor: pd.Series,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[pd.DatetimeIndex], np.ndarray]:
        """Build per-issue tensors. Returns (enc, dec, y, target_idx_list, valid_mask).

        ``y`` is shape (n_issues, horizon). NaN entries permitted; loss masks them.
        """
        H = self.cfg.history_hours
        T = self.cfg.horizon_hours
        weather = master[WEATHER_VARS].copy()
        roll = rolling_weather_features(weather)
        weather_features = pd.concat([weather, roll], axis=1)

        # Encoder input: 72 hours of weather + rolling features (no past water_temp;
        # matches the "single most-recent observation" anchor design). The t0 anchor
        # is prepended as a constant channel per-window below.
        enc_mat_full = weather_features.to_numpy(dtype=np.float32)
        enc_cols = ["water_temp_t0"] + list(weather_features.columns)

        cal = pd.DataFrame(index=weather.index)
        cal["hour_sin"] = np.sin(2 * np.pi * weather.index.hour / 24.0)
        cal["hour_cos"] = np.cos(2 * np.pi * weather.index.hour / 24.0)
        cal["doy_sin"] = np.sin(2 * np.pi * weather.index.dayofyear / 365.25)
        cal["doy_cos"] = np.cos(2 * np.pi * weather.index.dayofyear / 365.25)

        dec_features = pd.concat([weather_features, cal], axis=1)
        dec_mat_full = dec_features.to_numpy(dtype=np.float32)

        self.enc_feat_names = list(enc_cols)
        self.dec_feat_names = ["water_temp_t0"] + list(dec_features.columns)

        time_index = master.index
        target_arr = master["water_temp"].to_numpy(dtype=np.float32)

        time_to_pos = {t: i for i, t in enumerate(time_index)}

        enc_chunks: list[np.ndarray] = []
        dec_chunks: list[np.ndarray] = []
        y_chunks: list[np.ndarray] = []
        target_idx_list: list[pd.DatetimeIndex] = []
        valid: list[bool] = []

        for issue in issue_times:
            pos = time_to_pos.get(issue)
            # encoder window: 72h of weather ending AT issue_time (inclusive)
            # decoder window: horizons 1..T → target times issue_time+1 .. issue_time+T
            if pos is None or pos - H + 1 < 0 or pos + T >= len(time_index):
                valid.append(False)
                continue
            t0_val = anchor.get(issue, np.nan)
            if not np.isfinite(t0_val):
                valid.append(False)
                continue
            # Prepend the t0 anchor as an additional encoder channel held constant
            # across the 72h window — this gives the encoder access to "what is
            # the lake's current state" without leaking historical sensor readings.
            enc_weather = enc_mat_full[pos - H + 1 : pos + 1]
            enc_t0 = np.full((H, 1), t0_val, dtype=np.float32)
            enc = np.concatenate([enc_t0, enc_weather], axis=1)
            future = dec_mat_full[pos + 1 : pos + 1 + T]
            t0_col = np.full((T, 1), t0_val, dtype=np.float32)
            dec = np.concatenate([t0_col, future], axis=1)
            y = target_arr[pos + 1 : pos + 1 + T]
            enc_chunks.append(enc)
            dec_chunks.append(dec)
            y_chunks.append(y)
            target_idx_list.append(time_index[pos + 1 : pos + 1 + T])
            valid.append(True)

        if not enc_chunks:
            raise ValueError("GRU: no valid issue windows in input range.")
        enc_arr = np.stack(enc_chunks, axis=0)
        dec_arr = np.stack(dec_chunks, axis=0)
        y_arr = np.stack(y_chunks, axis=0)
        return enc_arr, dec_arr, y_arr, target_idx_list, np.asarray(valid, dtype=bool)

    def _standardize_fit(self, enc: np.ndarray, dec: np.ndarray) -> None:
        self.enc_mean = enc.reshape(-1, enc.shape[-1]).mean(axis=0)
        self.enc_std = enc.reshape(-1, enc.shape[-1]).std(axis=0) + 1e-6
        # Channel 0 of both enc and dec is water_temp_t0; keep it raw so the
        # decoder head's residual-over-t0 baseline works in degrees C.
        self.enc_mean[0] = 0.0
        self.enc_std[0] = 1.0
        self.dec_mean = dec.reshape(-1, dec.shape[-1]).mean(axis=0)
        self.dec_std = dec.reshape(-1, dec.shape[-1]).std(axis=0) + 1e-6
        self.dec_mean[0] = 0.0
        self.dec_std[0] = 1.0

    def _standardize_apply(self, enc: np.ndarray, dec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        assert self.enc_mean is not None
        return (enc - self.enc_mean) / self.enc_std, (dec - self.dec_mean) / self.dec_std

    # ----- training loop -----
    def fit_from_master(
        self,
        master: pd.DataFrame,
        train_issue: pd.DatetimeIndex,
        val_issue: pd.DatetimeIndex,
        anchor: pd.Series,
        day_mask_fn=None,
    ) -> GRUForecaster:
        torch.manual_seed(self.cfg.seed)
        np.random.seed(self.cfg.seed)

        enc_tr, dec_tr, y_tr, idx_tr, ok_tr = self._assemble(master, train_issue, anchor)
        enc_va, dec_va, y_va, idx_va, ok_va = self._assemble(master, val_issue, anchor)
        self._standardize_fit(enc_tr, dec_tr)
        enc_tr_s, dec_tr_s = self._standardize_apply(enc_tr, dec_tr)
        enc_va_s, dec_va_s = self._standardize_apply(enc_va, dec_va)

        device = torch.device("cpu")
        model = _Seq2Seq(enc_tr_s.shape[-1], dec_tr_s.shape[-1], self.cfg).to(device)
        self.model = model
        opt = torch.optim.AdamW(model.parameters(), lr=self.cfg.lr, weight_decay=self.cfg.weight_decay)

        def _to_tensor(*arrs):
            return [torch.from_numpy(a).to(device) for a in arrs]

        enc_tr_t, dec_tr_t, y_tr_t = _to_tensor(enc_tr_s, dec_tr_s, y_tr)
        enc_va_t, dec_va_t, y_va_t = _to_tensor(enc_va_s, dec_va_s, y_va)

        # Daytime mask weights to focus loss on scored hours
        def _build_weight(idx_list):
            if day_mask_fn is None:
                return None
            m = np.stack([day_mask_fn(pd.Series(ix)) for ix in idx_list], axis=0)
            return torch.from_numpy(m.astype(np.float32))

        w_tr = _build_weight(idx_tr)
        w_va = _build_weight(idx_va)

        n = enc_tr_t.shape[0]
        batch = self.cfg.batch_size
        best_val = math.inf
        best_state = None
        patience = 0

        for epoch in range(self.cfg.max_epochs):
            model.train()
            perm = torch.randperm(n)
            losses: list[float] = []
            for i in range(0, n, batch):
                idx = perm[i : i + batch]
                pred = model(enc_tr_t[idx], dec_tr_t[idx])
                target = y_tr_t[idx]
                mask = torch.isfinite(target)
                if w_tr is not None:
                    mask = mask & (w_tr[idx].to(device) > 0)
                if not mask.any():
                    continue
                loss = (pred[mask] - target[mask]).abs().mean()
                opt.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
                opt.step()
                losses.append(float(loss))

            # validation
            model.eval()
            with torch.no_grad():
                vpred = model(enc_va_t, dec_va_t)
                vmask = torch.isfinite(y_va_t)
                if w_va is not None:
                    vmask = vmask & (w_va.to(device) > 0)
                if vmask.any():
                    vloss = float((vpred[vmask] - y_va_t[vmask]).abs().mean())
                else:
                    vloss = math.inf

            print(f"  [gru] epoch={epoch:02d} train={np.mean(losses):.4f} val={vloss:.4f}")
            if vloss + 1e-4 < best_val:
                best_val = vloss
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
                patience = 0
            else:
                patience += 1
                if patience >= self.cfg.patience:
                    print(f"  [gru] early stop @ epoch {epoch}")
                    break

        if best_state is not None:
            model.load_state_dict(best_state)
        return self

    def fit(self, X, y, *, sample_weight=None, eval_set=None):
        raise NotImplementedError("Use fit_from_master for the GRU model.")

    def predict(self, X):
        raise NotImplementedError("Use predict_from_master for the GRU model.")

    def predict_from_master(
        self,
        master: pd.DataFrame,
        issue_times: pd.DatetimeIndex,
        anchor: pd.Series,
    ) -> tuple[np.ndarray, list[pd.DatetimeIndex]]:
        if self.model is None:
            raise RuntimeError("GRU not fit yet")
        enc, dec, y, idx_list, ok = self._assemble(master, issue_times, anchor)
        enc_s, dec_s = self._standardize_apply(enc, dec)
        enc_t = torch.from_numpy(enc_s)
        dec_t = torch.from_numpy(dec_s)
        self.model.eval()
        with torch.no_grad():
            pred = self.model(enc_t, dec_t).cpu().numpy()
        return pred, idx_list
