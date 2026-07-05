"""Typer CLI entry points: forecast-lake, train-lake, eval-lake."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from lake_forecast.config import repo_path

forecast_app = typer.Typer(add_completion=False, no_args_is_help=False)
train_app = typer.Typer(add_completion=False, no_args_is_help=False)
eval_app = typer.Typer(add_completion=False, no_args_is_help=False)
retrain_app = typer.Typer(add_completion=False, no_args_is_help=False)


# ----- retrain -----
@retrain_app.callback(invoke_without_command=True)
def retrain_main(
    test_days: int | None = typer.Option(None, help="Rolling test window length (default: config)"),
    val_days: int | None = typer.Option(None, help="Rolling val window length (default: config)"),
) -> None:
    from lake_forecast.retrain import retrain_and_maybe_promote

    decision = retrain_and_maybe_promote(test_days=test_days, val_days=val_days)
    verdict = "PROMOTED new model" if decision["promoted"] else "KEPT incumbent"
    typer.echo(f"retrain decision: {verdict} — {decision.get('reason', '')}")


# ----- train -----
@train_app.callback(invoke_without_command=True)
def train_main(
    models: str = typer.Option("all", help="'all', 'tabular' (everything except GRU), or 'gru'"),
    optuna_trials: int = typer.Option(80, help="Optuna trials for LightGBM"),
    skip: str = typer.Option("", help="Comma-separated model names to skip"),
) -> None:
    from lake_forecast.train import train_all, train_gru

    skip_set = tuple(s for s in skip.split(",") if s)
    if models in ("all", "tabular"):
        train_all(optuna_trials=optuna_trials, skip=skip_set)
    if models in ("all", "gru"):
        train_gru()


# ----- eval -----
@eval_app.callback(invoke_without_command=True)
def eval_main(
    split: str = typer.Option("test", help="Which split to print metrics for"),
) -> None:
    metrics_path = repo_path("reports/metrics/all_models.json")
    if not metrics_path.exists():
        typer.echo("No metrics found; run `uv run train-lake` first.")
        raise typer.Exit(code=1)
    payload = json.loads(metrics_path.read_text())
    typer.echo(f"split sizes: {payload['split']}")
    typer.echo("")
    typer.echo(f"{'model':<20}  {'val':>8}  {'test':>8}  {'audit':>8}  {'test_skill':>10}")
    for name, r in payload["results"].items():
        val = "       —" if r["val_mae"] is None else f"{r['val_mae']:.4f}"
        sk = "       —" if r["test_skill"] is None else f"{r['test_skill']:.3f}"
        typer.echo(f"{name:<20}  {val:>8}  {r['test_mae']:.4f}    {r['audit_mae']:.4f}    {sk:>10}")


# ----- forecast -----
@forecast_app.callback(invoke_without_command=True)
def forecast_main(
    issue_time: str | None = typer.Option(None, help="ISO timestamp, default = now (UTC, hourly floor)"),
    out_dir: str = typer.Option("forecast", help="Root dir for <Y>/<M>/<D>/<H>.csv and latest.csv"),
    latest_path: str | None = typer.Option(
        None, help="Full path for the stable latest CSV (default: <out-dir>/latest.csv)"
    ),
    refresh_anchor: bool = typer.Option(
        True, help="Refresh the water_temp anchor from the source CSV before forecasting"
    ),
    observed: float | None = typer.Option(
        None, help="Manual water_temp anchor override (skips --refresh-anchor)"
    ),
    update_state: bool = typer.Option(False, help="Only persist --observed to state.json, then exit"),
    state_path: str = typer.Option("models/state.json", help="State file path"),
) -> None:
    import pandas as pd

    from lake_forecast.infer import (
        forecast,
        refresh_state_from_source,
        update_state_file,
        write_forecast_csv,
    )

    state_file = Path(repo_path(state_path))

    # Anchor handling: explicit observation wins; otherwise pull from the live CSV.
    if observed is not None:
        update_state_file(observed=observed, state_path=state_file)
        typer.echo(f"state updated from --observed: {observed}")
        if update_state:
            return
    elif update_state:
        typer.echo("--update-state requires --observed")
        raise typer.Exit(code=2)
    elif refresh_anchor:
        st = refresh_state_from_source(state_file)
        typer.echo(f"anchor refreshed from source CSV: {st['last_water_temp']} @ {st['last_obs_time']}")

    df = forecast(issue_time=issue_time, state_path=state_file)
    issue_ts = pd.to_datetime(df["time"].min(), utc=True) - pd.Timedelta(hours=1)
    partition, latest = write_forecast_csv(
        df,
        issue_ts,
        out_root=Path(repo_path(out_dir)),
        latest_path=Path(latest_path) if latest_path else None,
    )
    typer.echo(f"wrote {len(df)} rows → {partition}")
    typer.echo(f"             and → {latest}")
    typer.echo(df[["time", "water_temp_pred"]].head(5).to_string(index=False))
