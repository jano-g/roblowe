"""FastAPI appka: auth gate v middleware (na scope["path"]), bezpečnostné hlavičky, SPA + JSON API,
scheduler v procese. Jeden uvicorn proces – zámerne."""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config, db
from .routers import api, pages
from .scheduler import scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("roblowe")

PUBLIC_PREFIXES = ("/login", "/healthz", "/static/", "/manifest.webmanifest", "/sw.js")


@asynccontextmanager
async def lifespan(app: FastAPI):
    errors = config.validate()
    if errors:
        for e in errors:
            log.error(e)
        sys.exit(1)
    db.migrate()
    auth.bootstrap_admin()
    scheduler.start()
    log.info("%s started (režim %s, admin %s)", config.APP_NAME, config.TRADING_MODE, config.ADMIN_USERNAME)
    if config.TRADING_MODE == "live":
        log.warning("!!! LIVE REŽIM – skutočné peniaze !!!")
    yield
    scheduler.stop()


app = FastAPI(title=config.APP_NAME, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(pages.router)
app.include_router(api.router, prefix="/api")


@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.scope["path"]
    request.state.user = auth.user_from_cookie(request.cookies.get(auth.COOKIE))
    public = path.startswith(PUBLIC_PREFIXES)
    if not public and request.state.user is None:
        if path.startswith("/api/"):
            return JSONResponse({"error": "Neprihlásený"}, status_code=401)
        return RedirectResponse(f"/login?next={path}", status_code=303)
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/"):
        if request.headers.get("x-requested-with") != "roblowe":
            return JSONResponse({"error": "Chýba CSRF hlavička"}, status_code=403)
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")
        if origin and origin.split("://", 1)[-1] != host:
            return JSONResponse({"error": "Neplatný Origin"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                                   "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                                                   "base-uri 'self'; form-action 'self'; object-src 'none'")
    if request.headers.get("x-forwarded-proto") == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    ctype = response.headers.get("content-type", "")
    if "text/html" in ctype or "javascript" in ctype or "text/css" in ctype:
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(api.ApiError)
async def api_error(request: Request, exc: api.ApiError):
    db.rollback_if_open()
    return JSONResponse({"error": exc.message}, status_code=exc.status)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    db.rollback_if_open()
    log.exception("unhandled: %s", exc)
    if request.scope["path"].startswith("/api/"):
        return JSONResponse({"error": "Vnútorná chyba servera"}, status_code=500)
    return JSONResponse({"error": "Vnútorná chyba servera"}, status_code=500)


@app.get("/healthz")
def healthz():
    return {"ok": True, "mode": config.TRADING_MODE}
