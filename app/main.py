"""The ASGI app: wiring, lifespan, and the pages.

The browser is a dumb client of this API - no logic, no secrets. Every /api route
is guarded by an explicit dependency rather than by a middleware that matches
paths, so a route added later is not accidentally public.
"""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config as config_mod
from . import storage as storage_mod
from .orchestrator import Orchestrator
from .routes import admin, archive
from .routes import auth as auth_routes
from .routes import jobs as job_routes
from .routes import media, status
from .settings import Settings, get_settings
from .sinks import make_sink
from .store import close_pool, connection, kv, migrate, open_pool, users

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

# Stamped onto the asset URLs so a browser cannot keep a cached app.js after a
# deploy and render new markup while running old code.
ASSETS = ("app.js", "style.css", "auth.js", "archive.js", "admin.js",
          "loginpage.js")

log = logging.getLogger("h3studio")


def make_backend(cfg: config_mod.Config):
    if cfg.mock:
        from .backends.mock import MockBackend
        return MockBackend(cfg)
    if not cfg.runpod.api_key:
        raise SystemExit(
            "No RunPod API key. Set RUNPOD_API_KEY, or set one from the admin "
            "page and redeploy, or set MOCK=true to run the whole pipeline "
            "without renting anything.")
    from .backends.runpod_pod import RunpodBackend
    return RunpodBackend(cfg)


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
        if stored_budget := await kv.get("budget_session_limit_usd"):
            cfg.budget.session_limit_usd = float(stored_budget)
        if measured := await kv.get("measured_minutes_per_clip"):
            cfg.measured_minutes_per_clip = float(measured)
        cfg.measured_on_gpu = await kv.get("measured_on_gpu")

        store = storage_mod.get_storage()
        await store.ensure_bucket()
        app.state.storage = store

        orch = Orchestrator(cfg, make_backend(cfg), make_sink(s), store)
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
    app.include_router(media.router)
    app.include_router(archive.router)
    app.include_router(admin.router)

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")

    def _page(name: str):
        async def render():
            page = WEB / name
            if not page.exists():
                return JSONResponse({"error": f"web/{name} missing"}, status_code=500)
            html = page.read_text(encoding="utf-8")
            for asset in ASSETS:
                path = WEB / asset
                if path.exists():
                    stamp = f"{int(path.stat().st_mtime)}-{path.stat().st_size}"
                    html = html.replace(f"/static/{asset}",
                                        f"/static/{asset}?v={stamp}")
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})
        return render

    # The pages are served without a session check; each one's first API call
    # returns 401 and its script redirects to /login. Gating the HTML too would
    # only mean maintaining the same rule in two places.
    app.get("/")(_page("index.html"))
    app.get("/login")(_page("login.html"))
    app.get("/archive")(_page("archive.html"))
    app.get("/admin")(_page("admin.html"))

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    s = get_settings()
    log.info("H3 Studio on %s:%d [%s]", s.host, s.port,
             "MOCK (no GPU, no cost)" if s.mock else "RunPod")
    # workers=1 is not a tuning choice: the orchestrator owns a rented GPU and a
    # second worker would start a second pod. The advisory lock backs this up.
    #
    # proxy_headers matters behind Dokploy's reverse proxy: without it
    # request.client.host is the proxy for every request, and the login rate
    # limiter would throttle all users as one.
    uvicorn.run(create_app(s), host=s.host, port=s.port, workers=1,
                log_level="info", proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
