"""Desktop-only session establishment and setup around the retained Flask UI."""
from __future__ import annotations

import secrets
import threading
import time
from pathlib import Path

from flask import Blueprint, Response, jsonify, redirect, render_template, request


class LocalBoundary:
    """Reject rebinding/cross-origin requests before any app or proxy middleware."""

    def __init__(self, application):
        self.application = application
        self.origin = None

    def __call__(self, environ, start_response):
        origin = self.origin
        host = environ.get("HTTP_HOST", "")
        supplied_origin = environ.get("HTTP_ORIGIN")
        # A native WebKit POST can originate from its initial opaque document.
        # Only this one-use, capability-protected endpoint accepts that origin;
        # it still checks the exact Host/peer and rejects forwarding headers.
        native_bootstrap = (environ.get("PATH_INFO") == "/desktop/session"
                            and environ.get("REQUEST_METHOD") == "POST"
                            and supplied_origin in (None, "null"))
        forwarded = any(key.startswith("HTTP_X_FORWARDED_") or key == "HTTP_FORWARDED"
                        for key in environ)
        denied = (not origin or host != origin.removeprefix("http://")
                  or environ.get("REMOTE_ADDR") != "127.0.0.1"
                  or forwarded or (supplied_origin is not None and supplied_origin != origin and not native_bootstrap)
                  or (environ.get("HTTP_SEC_FETCH_SITE") == "cross-site" and not native_bootstrap))
        if denied:
            body = b"Local application request refused."
            start_response("403 Forbidden", [("Content-Type", "text/plain"),
                                             ("Content-Length", str(len(body)))])
            return [body]
        return self.application(environ, start_response)


def build_app(manager, workspace, socket_path, launch_token, restart_event, code_version,
              quiesce=lambda: None, startup_message=None):
    # Imports happen only after launcher configures the selected workspace.
    from app import create_app, csrf
    from app import auth
    from werkzeug.middleware.proxy_fix import ProxyFix

    root = Path(manager.data_root)
    app = create_app({
        "SECRET_KEY": secrets.token_hex(32),
        "PANEL_DB": str(workspace.panel_db if workspace else root / "setup-panel.db"),
        "HEALTH_DB": str(workspace.health_db if workspace else root / "unselected.db"),
        "BRIDGE_SOCKET": str(socket_path),
        "PANEL_COOKIE_SECURE": False,
        "SESSION_COOKIE_SECURE": False,
        "SESSION_COOKIE_NAME": "oha_desktop_csrf",
        # Native sessions have a fixed lifetime; an idle form should not fail
        # earlier while that same session remains authorized. This does not
        # extend or refresh authentication and leaves the web deployment alone.
        "WTF_CSRF_TIME_LIMIT": auth.SESSION_TTL_SECONDS,
        "MAX_CONTENT_LENGTH": 131072,
        "HERMES_DISPLAY_NAME": "Fictional sample" if workspace and workspace.kind == "demo" else "Your workspace",
        "HERMES_TIMEZONE": workspace.timezone if workspace else "UTC",
    })
    @app.context_processor
    def desktop_context():
        return {"desktop_mode": True, "desktop_workspace": workspace}
    # The loopback desktop server has no reverse proxy.
    if isinstance(app.wsgi_app, ProxyFix):
        app.wsgi_app = app.wsgi_app.app
    bp = Blueprint("desktop", __name__, url_prefix="/desktop",
                   template_folder="templates", static_folder="static")
    token_lock = threading.Lock()
    pending = {"token": launch_token, "expires": time.monotonic() + 120}
    mutation_lock = threading.Lock()

    def session_ended():
        if request.accept_mimetypes.best == "text/html":
            return redirect("/desktop/signed-out")
        return jsonify(error="Your session ended. Quit and reopen Open Health Atlas to continue."), 401

    def desktop_gate():
        if request.endpoint in {"desktop.session", "desktop.signed_out"}:
            return None
        if request.path in ("/login", "/enroll"):
            return session_ended()
        if request.path.startswith("/api/auth/"):
            return jsonify(error="Use the app icon to start a desktop session."), 403
        if request.endpoint != "static" and not request.path.startswith("/desktop/static/"):
            if auth.current_session() is None:
                if request.path.startswith(("/api/", "/desktop/api/")):
                    return jsonify(error="Your session ended. Quit and reopen Open Health Atlas to continue."), 401
                return session_ended()
        if workspace is None and request.endpoint != "static" and not request.path.startswith("/desktop/"):
            return redirect("/desktop/")

    # Before the original session gate; bootstrap only exists in this adapter.
    app.before_request_funcs[None].insert(0, desktop_gate)

    @bp.post("/session")
    @csrf.exempt
    def session():
        with token_lock:
            candidate = request.headers.get("X-OHA-Launch-Token", "")
            valid = (pending["token"] is not None and time.monotonic() < pending["expires"]
                     and secrets.compare_digest(candidate, pending["token"]))
            if not valid:
                return jsonify(error="Launch session expired. Reopen the app."), 401
            pending["token"] = None
        token = auth.create_session()
        response = auth.set_session_cookie(redirect("/desktop/", code=303), token)
        return response

    # Public recovery UI and static assets contain no workspace data. The
    # capability-protected bootstrap is the only route that creates a session.
    original_gate = app.before_request_funcs[None][-1]
    def retained_gate():
        if request.endpoint in {"desktop.session", "desktop.signed_out", "desktop.static"}:
            return None
        return original_gate()
    app.before_request_funcs[None][-1] = retained_gate

    original_logout = app.view_functions["auth.logout"]
    def desktop_logout():
        response = original_logout()
        response.headers["Location"] = "/desktop/signed-out"
        return response
    app.view_functions["auth.logout"] = desktop_logout

    @bp.get("/signed-out")
    def signed_out():
        if auth.current_session() is not None:
            return redirect("/desktop/")
        return render_template("desktop/signed-out.html")

    def return_path():
        allowed = {"/", "/training", "/consistency", "/mind", "/nutrition",
                   "/recovery", "/care", "/labs", "/data", "/insights",
                   "/models", "/export", "/vault-notes", "/desktop/setup"}
        candidate = request.args.get("return_to", "/")
        if not workspace:
            return "/desktop/setup"
        return candidate if candidate in allowed else "/"

    @bp.get("/")
    @bp.get("/setup")
    def setup():
        if workspace and request.path != "/desktop/setup" and request.args.get("setup") != "1":
            return redirect("/")
        from desktop.preferences import read
        return render_template("desktop/setup.html", setup_theme=read().get("panel-theme", "paper"), workspace=workspace,
                               workspaces=manager.list_workspaces(), startup_message=startup_message,
                               return_path=return_path())

    @bp.get("/help")
    def help_page():
        from desktop.preferences import read
        return render_template("desktop/help.html", workspace=workspace,
                               help_theme=read().get("panel-theme", "paper"),
                               help_section="full" if request.args.get("section") == "full" else "ai",
                               return_path=return_path(),
                               code_version=code_version)

    @bp.get("/api/workspaces")
    def list_workspaces():
        return jsonify(manager.public_status())

    def mutate(operation):
        if not mutation_lock.acquire(blocking=False):
            return jsonify(error="A workspace is already opening."), 409
        restart_delay = None
        try:
            if restart_event.is_set():
                return jsonify(error="The application is restarting."), 409
            restart_delay = 3
            quiesce()
            operation()
            # Let the response reach WebKit before the launcher's exec restart.
            restart_delay = 0.5
            return jsonify(ok=True, restarting=True)
        except (ValueError, OSError, RuntimeError) as error:
            from desktop.workspaces import WorkspaceError
            message = str(error) if isinstance(error, WorkspaceError) else "The workspace could not be opened. Your existing data was kept. Check the file and timezone, then try again."
            return jsonify(error=message), 400
        finally:
            try:
                # Unexpected errors must also recover a stopped helper.
                if restart_delay is not None:
                    timer = threading.Timer(restart_delay, restart_event.set)
                    timer.daemon = True
                    timer.start()
            finally:
                mutation_lock.release()

    @bp.post("/api/workspaces")
    def create_workspace():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) - {"kind", "timezone", "source_database"}:
            return jsonify(error="Choose a workspace and timezone."), 400
        kind = body.get("kind")
        if not isinstance(kind, str) or kind not in {"demo", "personal", "import"}:
            return jsonify(error="Choose a workspace type."), 400
        from desktop.workspaces import WorkspaceError, validate_timezone
        try:
            timezone = validate_timezone(body.get("timezone"))
        except WorkspaceError as error:
            return jsonify(error=str(error)), 400
        source = body.get("source_database")
        if ((source is not None and not isinstance(source, str))
                or (kind == "import" and not source)):
            return jsonify(error="Choose a database file to import."), 400
        return mutate(lambda: manager.create(kind=kind, timezone=timezone, source_database=source))

    @bp.post("/api/select")
    def select_workspace():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"id"} or not isinstance(body["id"], str):
            return jsonify(error="Choose a saved workspace."), 400
        return mutate(lambda: manager.select(body["id"]))

    @bp.get("/api/mcp-config")
    def mcp_config():
        if workspace is None:
            return jsonify(error="Open a workspace first."), 409
        from desktop.mcp_config import bundled_executable, client_configuration
        try:
            config = client_configuration(bundled_executable(), workspace.health_db, workspace.timezone,
                                          workspace=workspace.directory)
        except (ValueError, FileNotFoundError):
            return jsonify(error="AI connection configuration is available in the installed desktop app."), 409
        response = jsonify(config)
        if request.args.get("download") == "1":
            response.headers["Content-Disposition"] = 'attachment; filename="openhealthatlas-mcp.json"'
        return response

    @bp.get("/api/diagnostics")
    def diagnostics():
        import platform
        return jsonify(product="Open Health Atlas", code_version=code_version,
                       system=platform.system(), architecture=platform.machine(),
                       workspace_kind=workspace.kind if workspace else "unselected",
                       selected=workspace is not None)

    @bp.get("/preferences.js")
    def browser_preferences():
        from desktop.preferences import bootstrap_script
        return Response(bootstrap_script(), mimetype="application/javascript")

    @bp.post("/api/preferences")
    def save_preference():
        from desktop.preferences import write
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"key", "value"}:
            return jsonify(error="Unsupported display preference"), 400
        try:
            write(body["key"], body["value"])
        except (ValueError, TypeError):
            return jsonify(error="Unsupported display preference"), 400
        return jsonify(ok=True)

    @app.after_request
    def desktop_headers(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    app.register_blueprint(bp)
    return app
