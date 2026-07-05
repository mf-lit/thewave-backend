"""Flask app for The Wave dashboard.

Serves one HTML page plus JSON endpoints backed by read-only SQLite queries.
Run locally with:

    uv run flask --app src.main run --port 5002
"""

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

from . import queries

app = Flask(__name__)
CORS(app)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    return jsonify(status="ok")


def _exclude_cloud() -> bool:
    """Whether to hide known Google/Apple IPs, from the query string."""
    return request.args.get("exclude_cloud", "0") in ("1", "true", "True")


@app.route("/api/summary")
def api_summary():
    """Headline counts for the top badges."""
    return jsonify(queries.summary_stats(exclude_cloud=_exclude_cloud()))


@app.route("/api/clients/new")
def api_clients_new():
    """New clients per period, grouped by first_seen."""
    data = queries.clients_by_period(
        "first_seen",
        request.args.get("granularity", "day"),
        request.args.get("from"),
        request.args.get("to"),
        exclude_cloud=_exclude_cloud(),
    )
    return jsonify(data)


@app.route("/api/clients/active")
def api_clients_active():
    """Active clients per period: end-of-day snapshots for past days, live for today."""
    data = queries.active_clients_by_period(
        request.args.get("granularity", "day"),
        request.args.get("from"),
        request.args.get("to"),
        exclude_cloud=_exclude_cloud(),
    )
    return jsonify(data)


@app.route("/api/clients")
def api_clients():
    """A page of client detail rows for the clients table."""
    # Single clients table: the date range filters on last_seen ("active in window").
    data = queries.client_rows(
        "last_seen",
        sort=request.args.get("sort", "last_seen"),
        direction=request.args.get("dir", "desc"),
        date_from=request.args.get("from"),
        date_to=request.args.get("to"),
        limit=request.args.get("limit", default=40, type=int),
        offset=request.args.get("offset", default=0, type=int),
        exclude_cloud=_exclude_cloud(),
    )
    return jsonify(data)


@app.route("/api/notifications")
def api_notifications():
    return jsonify(queries.notifications_with_alias())
