"""Flask app for The Wave dashboard.

Serves one HTML page plus JSON endpoints backed by read-only SQLite queries.
Run locally with:

    uv run flask --app src.main run --port 5002
"""

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

from . import queries
from .messages_api import MessagesApi, MessagesApiError

app = Flask(__name__)
# Scoped to /api/*, which is read-only. The /admin-api/* routes below create,
# edit and delete broadcast messages, and a cross-origin page must not be able
# to preflight its way into them just because the operator is on the Tailnet.
CORS(app, resources={r"/api/*": {"origins": "*"}})

messages_api = MessagesApi()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/messages")
def messages_page():
    return render_template("messages.html")


@app.route("/healthz")
def healthz():
    return jsonify(status="ok")


def _exclude_cloud() -> bool:
    """Whether to hide known Google/Apple IPs, from the query string."""
    return request.args.get("exclude_cloud", "0") in ("1", "true", "True")


def _client_os():
    """Platforms to filter on, or [] for all. Bound as params, never inlined."""
    return [v for v in request.args.getlist("client_os") if v]


@app.route("/api/client-os")
def api_client_os():
    """The platform values available to the OS filter drop-down."""
    return jsonify(queries.client_os_values())


@app.route("/api/summary")
def api_summary():
    """Headline counts for the top badges."""
    return jsonify(queries.summary_stats(exclude_cloud=_exclude_cloud(), client_os=_client_os()))


@app.route("/api/clients/new")
def api_clients_new():
    """New clients per period, grouped by first_seen."""
    data = queries.clients_by_period(
        "first_seen",
        request.args.get("granularity", "day"),
        request.args.get("from"),
        request.args.get("to"),
        exclude_cloud=_exclude_cloud(),
        client_os=_client_os(),
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
        client_os=_client_os(),
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
        client_os=_client_os(),
    )
    return jsonify(data)


@app.route("/api/notifications")
def api_notifications():
    return jsonify(queries.notifications_with_alias())


# ---- messages admin -------------------------------------------------------
# The dashboard proxies the messages service's admin API so the x-admin-key
# stays server-side. These are under /admin-api/ rather than /api/ because they
# write, and the CORS policy above deliberately does not cover them.


@app.errorhandler(MessagesApiError)
def _messages_api_error(exc):
    """Relay the messages service's own message and status.

    Its validation strings are written for the person composing the message;
    rewording them here would put a second vocabulary in front of one set of
    rules.
    """
    return jsonify(error=exc.message), exc.status


def _body() -> dict:
    return request.get_json(silent=True) or {}


@app.route("/admin-api/messages", methods=["GET", "POST"])
def admin_api_messages():
    if request.method == "POST":
        return jsonify(messages_api.create(_body())), 201
    return jsonify(messages=messages_api.list_messages())


@app.route("/admin-api/messages/<message_id>", methods=["GET", "PUT", "DELETE"])
def admin_api_message(message_id):
    if request.method == "PUT":
        return jsonify(messages_api.update(message_id, _body()))
    if request.method == "DELETE":
        return jsonify(messages_api.delete(message_id))
    return jsonify(messages_api.get(message_id))


@app.route("/admin-api/messages/<message_id>/enabled", methods=["POST"])
def admin_api_message_enabled(message_id):
    return jsonify(messages_api.set_enabled(message_id, bool(_body().get("enabled"))))


@app.route("/admin-api/audience", methods=["POST"])
def admin_api_audience():
    return jsonify(messages_api.audience(_body()))


@app.route("/admin-api/preview", methods=["POST"])
def admin_api_preview():
    return jsonify(messages_api.preview(_body().get("body", "")))
