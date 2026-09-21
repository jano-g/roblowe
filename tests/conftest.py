import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="roblowe-test-")
os.environ["DATA_DIR"] = _tmp
os.environ["ADMIN_USERNAME"] = "jano"
os.environ["ADMIN_PASSWORD"] = "heslo-heslo-123"
os.environ["TRADING_MODE"] = "dry"
for k in ("ALPACA_PAPER_KEY_ID", "ALPACA_PAPER_SECRET_KEY", "ALPACA_LIVE_KEY_ID", "ALPACA_LIVE_SECRET_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth, db, settings  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    db.migrate()
    auth.bootstrap_admin()
    settings.invalidate()
    auth._attempts.clear()
    yield
    for t in ("settings", "days", "decisions", "orders", "news_seen", "analyses", "equity", "history"):
        db.run(f"DELETE FROM {t}")
    settings.invalidate()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def logged(client):
    r = client.post("/login", data={"username": "jano", "password": "heslo-heslo-123"}, follow_redirects=False)
    assert r.status_code == 303
    client.headers["X-Requested-With"] = "roblowe"
    return client
