"""Screenshoty SPA: mobil (390 px), desktop (1280 px), dark mode. Použitie:
   BASE=http://127.0.0.1:8120 PASS=heslo-heslo-123 .venv/bin/python scripts/screenshots.py"""
import os
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "http://127.0.0.1:8120")
USER = os.environ.get("USER_", "jano")
PASS = os.environ.get("PASS", "heslo-heslo-123")
OUT = os.environ.get("OUT", "screenshots")
os.makedirs(OUT, exist_ok=True)
EXE = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=EXE) if EXE else p.chromium.launch()
    for name, vp, dark in (("mobile", (390, 844), False), ("desktop", (1280, 900), False), ("mobile-dark", (390, 844), True)):
        ctx = b.new_context(viewport={"width": vp[0], "height": vp[1]}, color_scheme="dark" if dark else "light",
                            device_scale_factor=2)
        pg = ctx.new_page()
        pg.goto(BASE + "/login")
        pg.screenshot(path=f"{OUT}/{name}-login.png")
        pg.fill("input[name=username]", USER)
        pg.fill("input[name=password]", PASS)
        pg.click("button[type=submit]")
        pg.wait_for_selector(".tiles, .card")
        for route in ("", "obchody", "spravy", "nastavenia"):
            pg.goto(f"{BASE}/#/{route}")
            pg.wait_for_timeout(700)
            pg.screenshot(path=f"{OUT}/{name}-{route or 'prehlad'}.png", full_page=True)
        ctx.close()
    b.close()
print("ok", OUT)
