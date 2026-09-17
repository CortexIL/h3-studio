"""The ASGI app: wiring, lifespan, and the pages.

The browser is a client of this API and holds no secrets. Every /api route
is guarded by an explicit dependency rather than by a middleware that matches
paths, so a route added later is not accidentally public.
"""
from __future__ import annotations

import contextlib
import logging
import re
from html import escape
from pathlib import Path
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from . import config as config_mod
from . import storage as storage_mod
from .orchestrator import Orchestrator
from .routes import admin, archive, avatar
from .routes import auth as auth_routes
from .routes import jobs as job_routes
from .routes import media, status
from .routes import prompt as prompt_routes
from .settings import Settings, get_settings
from .sinks import make_sink
from .store import close_pool, connection, kv, migrate, open_pool, users

ROOT = Path(__file__).resolve().parent.parent
# The React client, built by `npm run build` in frontend/ (the Dockerfile does it).
DIST = ROOT / "frontend" / "dist"
# The previous client. No page links to it any more; it stays mounted for one
# release so a tab left open across the deploy can still load its scripts.
WEB = ROOT / "web"
# Home-screen icons and the web app manifest. Not Vite's, deliberately: a phone
# that has saved an icon keeps asking for the URL it saved, and Vite renames
# everything it builds on every deploy. See app/pwa/README.md.
PWA = Path(__file__).resolve().parent / "pwa"

# Sent with every page. The HTML names content-hashed asset files, so it must
# never be cached: a stale copy would point at files the next deploy removed.
PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}


class ImmutableStatic(StaticFiles):
    """Vite's build output. Every file name carries a hash of its content, so the
    file at a given URL never changes and a browser may keep it for a year."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

log = logging.getLogger("h3studio")


def make_backend_factory(cfg: config_mod.Config):
    """A maker of backends, one per pod the orchestrator decides to run."""
    if cfg.mock:
        from .backends.mock import MockBackend
        return lambda: MockBackend(cfg)
    if not cfg.runpod.api_key:
        raise SystemExit(
            "No RunPod API key. Set RUNPOD_API_KEY, or set one from the admin "
            "page and redeploy, or set MOCK=true to run the whole pipeline "
            "without renting anything.")
    from .backends.runpod_pod import RunpodBackend
    # One set for every backend made here, so no two ever adopt the same pod.
    claimed: set[str] = set()
    return lambda: RunpodBackend(cfg, claimed=claimed)


def make_backend(cfg: config_mod.Config):
    return make_backend_factory(cfg)()


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    cfg = config_mod.Config.from_settings(s)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await open_pool(s.database_url)
        async with connection() as conn:
            applied = await migrate(conn)
        if applied:
            log.info("applied migrations: %s", ", ".join(applied))
        await users.ensure_bootstrap_admin(s.admin_email, s.admin_password)

        # kv wins over the environment: it is what an admin set from the UI, and
        # it is the copy that survives a redeploy.
        if stored_key := await kv.get("runpod_api_key"):
            cfg.runpod.api_key = stored_key
        if helper_key := await kv.get("anthropic_api_key"):
            cfg.anthropic_api_key = helper_key
        if stored_budget := await kv.get("budget_session_limit_usd"):
            cfg.budget.session_limit_usd = float(stored_budget)
        if measured := await kv.get("measured_minutes_per_clip"):
            cfg.measured_minutes_per_clip = float(measured)
        cfg.measured_on_gpu = await kv.get("measured_on_gpu")

        store = storage_mod.get_storage()
        await store.ensure_bucket()
        app.state.storage = store

        new_backend = make_backend_factory(cfg)
        orch = Orchestrator(cfg, new_backend(), make_sink(s, cfg.generation), store,
                            backend_factory=new_backend)
        app.state.orch = orch
        await orch.start()
        try:
            yield
        finally:
            # Runs on shutdown of any kind. A pod outliving the process is the
            # one failure mode here that costs real money.
            await orch.stop()
            await close_pool()

    app = FastAPI(title="H3 Studio", docs_url=None, redoc_url=None,
                  lifespan=lifespan)
    app.state.cfg = cfg
    app.state.settings = s

    app.include_router(status.health_router)
    app.include_router(auth_routes.router)
    app.include_router(status.router)
    app.include_router(job_routes.router)
    app.include_router(prompt_routes.router)
    app.include_router(media.router)
    app.include_router(archive.router)
    app.include_router(admin.router)
    app.include_router(avatar.router)

    if (DIST / "assets").exists():
        app.mount("/assets", ImmutableStatic(directory=DIST / "assets"), name="assets")
    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")
    # No session here on purpose: a phone fetches the icon and the manifest
    # before anyone has signed in, and a 401 is what leaves a blank tile.
    app.mount("/pwa", StaticFiles(directory=PWA), name="pwa")

    def _redirect(to: str) -> RedirectResponse:
        # no-store so a browser never replays a cached redirect after sign-in.
        return RedirectResponse(to, status_code=303,
                                headers={"Cache-Control": "no-store"})

    def _page(access: str, title: str):
        """Serve the app for one route, deciding on the server who may see it.

        Checked here rather than left to the client: a page that renders first
        and redirects on its first 401 shows the studio for a moment to someone
        who is not signed in. The rule itself is auth.session_user, the same one
        the API uses. Every route gets the same index.html - the client routes
        from there - with its own title, so the tab reads right before any
        script has run.
        """
        async def render(request: Request):
            if access != "public":
                user = await auth.session_user(request)
                if access == "signed-out" and user:
                    return _redirect("/")
                if access in ("user", "admin") and not user:
                    path = request.url.path
                    # Where to come back to after signing in. The login page
                    # only honours same-origin paths, so this cannot become an
                    # open redirect.
                    return _redirect("/login" if path == "/"
                                     else f"/login?next={quote(path)}")
                if access == "admin" and user["role"] != "admin":
                    return _redirect("/")
            index = DIST / "index.html"
            if not index.exists():
                return JSONResponse(
                    {"error": "frontend/dist/index.html is missing - "
                              "run `npm run build` in frontend/"},
                    status_code=500)
            page = index.read_text(encoding="utf-8")
            page = re.sub(r"<title>.*?</title>", f"<title>{escape(title)}</title>",
                          page, count=1, flags=re.S)
            return HTMLResponse(page, headers=PAGE_HEADERS)
        return render

    # No catch-all: an unknown path is a plain 404, not the app.
    app.get("/", include_in_schema=False)(_page("user", "H3 Studio"))
    app.get("/login", include_in_schema=False)(_page("signed-out", "Sign in · H3 Studio"))
    app.get("/archive", include_in_schema=False)(_page("user", "Archive · H3 Studio"))
    app.get("/account", include_in_schema=False)(_page("user", "Account · H3 Studio"))
    app.get("/beta", include_in_schema=False)(_page("user", "Beta · H3 Studio"))
    app.get("/admin", include_in_schema=False)(_page("admin", "Admin · H3 Studio"))

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    s = get_settings()
    log.info("H3 Studio on %s:%d [%s]", s.host, s.port,
             "MOCK (no GPU, no cost)" if s.mock else "RunPod")
    # workers=1 is not a tuning choice: the orchestrator owns the rented GPUs and
    # a second worker would start a second set of pods. The advisory lock backs this up.
    #
    # proxy_headers matters behind Dokploy's reverse proxy: without it
    # request.client.host is the proxy for every request, and the login rate
    # limiter would throttle all users as one.
    uvicorn.run(create_app(s), host=s.host, port=s.port, workers=1,
                log_level="info", proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
