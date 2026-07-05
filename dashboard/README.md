# The Wave — Dashboard

A small Flask + Chart.js web dashboard over The Wave's SQLite data.

It shows:
- **New clients per day** (by `first_seen`) and **active clients per day** (by `last_seen`*) as charts
- **Configurable detail tables** for both (date range, day/week/month granularity, click-to-sort, column toggles)
- A **notifications list** with each client's alias

\* The active-clients chart is approximate: `last_seen` only records each client's *most recent*
day, so it skews toward recent dates and is not a true daily-active count.

## Data sources (read-only)

| DB | Table | Used for |
|----|-------|----------|
| `/docker_vols/thewave/upstream-api/data/water_temperature.db` | `clients` | charts, detail tables, aliases |
| `/docker_vols/thewave/notifications/data/notifications.db` | `notifications` | notifications list |

Aliases are matched on `notifications.client_id = clients.uuid`. Both databases are opened
read-only (`mode=ro`); the dashboard never writes to them.

## Run locally

```bash
uv run flask --app src.main run --port 5002
```

Then open <http://localhost:5002/>. No environment setup is needed — `src/config.py` defaults to the
real DB paths above. Override with `UPSTREAM_DB_PATH` / `NOTIFICATIONS_DB_PATH` if needed.

## Layout

```
src/
├── main.py       # Flask app + routes
├── config.py     # DB path config
├── db.py         # read-only sqlite connections (+ ATTACH for cross-db joins)
├── queries.py    # all SQL
├── templates/    # base.html, index.html
└── static/       # dashboard.js, style.css
```

To extend the dashboard, use the `wave-dashboard` Claude Code skill (`.claude/skills/wave-dashboard/`).
