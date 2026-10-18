import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from .auth import LoginLimiter, log_security_event, password_matches
from .config import Settings
from .csv_export import build_csv_export, iter_csv_export
from .posthog_client import (
    POSTHOG_UNAVAILABLE_MESSAGE,
    PostHogClient,
    PostHogUnavailable,
    QueryResult,
)
from .queries import (
    AnalyticsFilters,
    EventCursor,
    InvalidFilter,
    detail_query,
    ranking_query,
    summary_query,
    trend_query,
)


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()
    app = Flask(__name__)
    app.config.update(
        APPLICATION_ROOT=settings.url_prefix,
        SECRET_KEY=settings.session_secret,
        SESSION_COOKIE_NAME="site_admin_session",
        SESSION_COOKIE_PATH="/site-admin",
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
        SESSION_REFRESH_EACH_REQUEST=True,
    )
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_prefix=1,
    )
    limiter = LoginLimiter()
    posthog = PostHogClient(settings)
    app.extensions["site_admin_login_limiter"] = limiter
    app.extensions["site_admin_posthog_client"] = posthog

    @app.after_request
    def disable_response_storage(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def dashboard():
        if not session.get("site_admin_authenticated"):
            return redirect(url_for("login"))
        return render_template("dashboard.html")

    @app.route("/login", methods=("GET", "POST"))
    def login():
        if request.method == "GET":
            return render_template("login.html", error=None)

        ip = request.remote_addr or "unknown"
        now = time.time()
        allowed, remaining = limiter.allow(ip, now)
        if not allowed:
            log_security_event("rate_limit", ip)
            return (
                render_template(
                    "login.html",
                    error=f"失败次数过多，请在 {remaining} 秒后重试。",
                ),
                429,
            )

        submitted = request.form.get("password", "")
        if password_matches(submitted, settings.admin_password):
            limiter.clear(ip)
            session.clear()
            session.permanent = True
            session["site_admin_authenticated"] = True
            log_security_event("login_success", ip)
            return redirect(url_for("dashboard"))

        limiter.record_failure(ip, now)
        log_security_event("login_failure", ip)
        return render_template("login.html", error="密码错误。"), 401

    @app.post("/logout")
    def logout():
        ip = request.remote_addr or "unknown"
        session.clear()
        log_security_event("logout", ip)
        return redirect(url_for("login"))

    def authenticated_api(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("site_admin_authenticated"):
                return jsonify(error="Authentication required."), 401
            return view(*args, **kwargs)

        return wrapped

    def report_response(spec):
        result = app.extensions["site_admin_posthog_client"].run_hogql(
            spec.query,
            spec.name,
            use_cache=spec.use_cache,
        )
        return jsonify(_result_payload(result))

    @app.get("/export.csv")
    def export_csv():
        if not session.get("site_admin_authenticated"):
            return redirect(url_for("login"))
        try:
            filters = AnalyticsFilters.from_request(request.args)
            csv_stream = build_csv_export(
                app.extensions["site_admin_posthog_client"],
                filters,
            )
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            filename = f"site-admin-events-{timestamp}.csv"
            return Response(
                iter_csv_export(csv_stream),
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
        except InvalidFilter as error:
            return _invalid_filter_response(error)
        except PostHogUnavailable:
            return _posthog_unavailable_response()

    @app.get("/budget")
    def budget_page():
        if not session.get("site_admin_authenticated"):
            return redirect(url_for("login"))
        from .budget_report import budget_report
        import sqlite3
        try:
            return render_template("budget.html", report=budget_report(request.args.get("day")), error=None)
        except ValueError:
            return render_template("budget.html", report=None, error="日期格式无效，请选择日期后重试。"), 400
        except (sqlite3.Error, OSError):
            app.logger.exception("Budget ledger unavailable")
            return render_template("budget.html", report=None, error="额度账本暂时不可用，请稍后重试。"), 503

    @app.get("/api/summary")
    @authenticated_api
    def api_summary():
        try:
            filters = AnalyticsFilters.from_request(request.args)
            return report_response(summary_query(filters))
        except InvalidFilter as error:
            return _invalid_filter_response(error)
        except PostHogUnavailable:
            return _posthog_unavailable_response()

    @app.get("/api/trend")
    @authenticated_api
    def api_trend():
        try:
            filters = AnalyticsFilters.from_request(request.args)
            return report_response(trend_query(filters))
        except InvalidFilter as error:
            return _invalid_filter_response(error)
        except PostHogUnavailable:
            return _posthog_unavailable_response()

    @app.get("/api/rankings")
    @authenticated_api
    def api_rankings():
        try:
            filters = AnalyticsFilters.from_request(request.args)
            kind = request.args.get("kind", "")
            return report_response(ranking_query(kind, filters))
        except InvalidFilter as error:
            return _invalid_filter_response(error)
        except PostHogUnavailable:
            return _posthog_unavailable_response()

    @app.get("/api/events")
    @authenticated_api
    def api_events():
        try:
            filters = AnalyticsFilters.from_request(request.args)
            cursor_value = request.args.get("cursor")
            cursor = (
                None if cursor_value is None else EventCursor.decode(cursor_value)
            )
            spec = detail_query(filters, cursor)
            result = app.extensions["site_admin_posthog_client"].run_hogql(
                spec.query,
                spec.name,
                use_cache=spec.use_cache,
            )
            payload = _result_payload(result)
            payload["next_cursor"] = _next_event_cursor(result)
            return jsonify(payload)
        except InvalidFilter as error:
            return _invalid_filter_response(error)
        except PostHogUnavailable:
            return _posthog_unavailable_response()

    return app


def _result_payload(result: QueryResult) -> dict[str, object]:
    return {
        "columns": list(result.columns),
        "results": [list(row) for row in result.results],
    }


def _next_event_cursor(result: QueryResult) -> str | None:
    if len(result.results) > 100:
        raise PostHogUnavailable(POSTHOG_UNAVAILABLE_MESSAGE) from None
    if len(result.results) < 100:
        return None
    try:
        timestamp_index = result.columns.index("timestamp")
        uuid_index = result.columns.index("uuid")
        final_row = result.results[-1]
        timestamp_value = final_row[timestamp_index]
        uuid_value = final_row[uuid_index]
        if not isinstance(timestamp_value, str) or not isinstance(uuid_value, str):
            raise ValueError
        timestamp = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
        return EventCursor(timestamp=timestamp, uuid=uuid_value).encode()
    except (ValueError, OverflowError, IndexError):
        raise PostHogUnavailable(POSTHOG_UNAVAILABLE_MESSAGE) from None


def _invalid_filter_response(error: InvalidFilter):
    return jsonify(error=error.message, field=error.field), 400


def _posthog_unavailable_response():
    return jsonify(error=POSTHOG_UNAVAILABLE_MESSAGE), 503
