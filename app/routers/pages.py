from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .. import auth, config, db
from ..scheduler import scheduler

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
ASSET_VERSION = "5"


def _ctx(request: Request, **kw):
    return {"request": request, "app_name": config.APP_NAME, "mode": scheduler.mode, "v": ASSET_VERSION, **kw}


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", _ctx(request, user=request.state.user))


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    if request.state.user:
        return RedirectResponse(auth.safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", _ctx(request, error=None, next=auth.safe_next(next)))


@router.post("/login", response_class=HTMLResponse)
def login_post(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/")):
    ip = auth.client_ip(request)
    if auth.throttled(ip):
        return templates.TemplateResponse(request, "login.html",
                                          _ctx(request, error="Priveľa pokusov. Skús o 15 minút.", next=auth.safe_next(next)),
                                          status_code=429)
    u = auth.login(username.strip()[:64], password[:256])
    if not u:
        auth.record_failure(ip)
        db.log_history("anon", "login_failed", {"ip": ip, "username": username[:64]})
        return templates.TemplateResponse(request, "login.html",
                                          _ctx(request, error="Nesprávne meno alebo heslo.", next=auth.safe_next(next)),
                                          status_code=401)
    auth.clear_failures(ip)
    resp = RedirectResponse(auth.safe_next(next), status_code=303)
    resp.set_cookie(auth.COOKIE, auth.make_cookie(u["id"], u["session_version"]), max_age=auth.COOKIE_MAX_AGE,
                    httponly=True, samesite="lax", secure=request.headers.get("x-forwarded-proto") == "https",
                    path="/")
    db.log_history(u["username"], "login", {"ip": ip})
    return resp


@router.post("/logout")
def logout(request: Request):
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@router.get("/manifest.webmanifest")
def manifest():
    return Response(
        '{"name":"Roblowe","short_name":"Roblowe","start_url":"/","display":"standalone","background_color":"#0f1115",'
        '"theme_color":"#0f1115","icons":[{"src":"/static/img/icon.svg","sizes":"any","type":"image/svg+xml"}]}',
        media_type="application/manifest+json")
