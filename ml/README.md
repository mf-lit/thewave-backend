# Lake Water Temperature Forecast

Hourly water temperature forecasting for a small shallow lake near Bristol, UK
(51.5394°N, -2.6185°W), 168-hour horizon, using Open-Meteo weather as the only
inference-time input. Target: masked MAE < 0.4 °C over local-time 06:00–21:59.

## Quickstart
```bash
uv sync
uv run python scripts/fetch_archive.py --start 2023-09-24 --end 2026-05-19
uv run python scripts/build_features.py
uv run train-lake --models all
uv run python scripts/make_report.py
uv run forecast-lake --issue-time 2026-05-19T12:00 --out /tmp/forecast.parquet
uv run pytest -q
```

See `report.md` for methodology, results, and limitations.
