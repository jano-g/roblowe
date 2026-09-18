"""Denná online záloha SQLite (.backup API) + upload do Backblaze B2 natívnym API.
Cloud zlyhanie nikdy nestratí lokálnu kópiu. Názvy záloh unikátne aj v tej istej sekunde."""
from __future__ import annotations

import base64
import hashlib
from urllib.parse import quote
import logging
import os
import sqlite3
from datetime import datetime

import httpx

from .. import config, db, settings

log = logging.getLogger("roblowe.backup")


def _unique_name(base: str) -> str:
    path = config.BACKUP_DIR / base
    if not path.exists():
        return base
    stem, ext = os.path.splitext(base)
    i = 2
    while (config.BACKUP_DIR / f"{stem}-{i}{ext}").exists():
        i += 1
    return f"{stem}-{i}{ext}"


def make_local(tag: str = "auto") -> str:
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = _unique_name(f"roblowe-{datetime.now(config.TZ):%Y%m%d-%H%M%S}-{tag}.db")
    dest = config.BACKUP_DIR / name
    src = db.conn()
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()
    os.chmod(dest, 0o600)
    return name


def prune_local(keep: int) -> None:
    files = sorted(config.BACKUP_DIR.glob("roblowe-*.db"), key=lambda p: p.stat().st_mtime)
    for p in files[:-keep] if keep > 0 else []:
        if "-auto" in p.name:  # ručné a pre-restore kópie nemaž automaticky
            p.unlink(missing_ok=True)


def list_local() -> list[dict]:
    if not config.BACKUP_DIR.exists():
        return []
    out = []
    for p in sorted(config.BACKUP_DIR.glob("roblowe-*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size,
                    "at": datetime.fromtimestamp(st.st_mtime, config.TZ).isoformat()})
    return out


# -- Backblaze B2 (native API v2) ----------------------------------------------------------------
class B2Error(Exception):
    pass


def _b2_auth(key_id: str, app_key: str) -> dict:
    tok = base64.b64encode(f"{key_id}:{app_key}".encode()).decode()
    r = httpx.get("https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
                  headers={"Authorization": f"Basic {tok}"}, timeout=20)
    if r.status_code != 200:
        raise B2Error(f"B2 autorizácia zlyhala ({r.status_code})")
    return r.json()


def _b2_bucket_id(auth: dict, bucket: str) -> str:
    r = httpx.post(auth["apiUrl"] + "/b2api/v2/b2_list_buckets",
                   headers={"Authorization": auth["authorizationToken"]},
                   json={"accountId": auth["accountId"], "bucketName": bucket}, timeout=20)
    if r.status_code != 200:
        raise B2Error(f"B2 bucket nedostupný ({r.status_code}) – kľúč musí mať listBuckets")
    buckets = r.json().get("buckets") or []
    if not buckets:
        raise B2Error("B2 bucket sa nenašiel")
    return buckets[0]["bucketId"]


def _b2_creds() -> tuple[str, str, str, str]:
    return (settings.get("b2_key_id"), settings.get("b2_app_key"), settings.get("b2_bucket"), settings.get("b2_prefix"))


def upload_b2(name: str) -> str:
    key_id, app_key, bucket, prefix = _b2_creds()
    if not (key_id and app_key and bucket):
        raise B2Error("B2 nie je nakonfigurované")
    auth = _b2_auth(key_id, app_key)
    bucket_id = _b2_bucket_id(auth, bucket)
    r = httpx.post(auth["apiUrl"] + "/b2api/v2/b2_get_upload_url",
                   headers={"Authorization": auth["authorizationToken"]}, json={"bucketId": bucket_id}, timeout=20)
    if r.status_code != 200:
        raise B2Error(f"B2 upload URL zlyhalo ({r.status_code})")
    up = r.json()
    data = (config.BACKUP_DIR / os.path.basename(name)).read_bytes()
    remote = f"{prefix}{os.path.basename(name)}"
    r = httpx.post(up["uploadUrl"], content=data, timeout=120, headers={
        "Authorization": up["authorizationToken"], "X-Bz-File-Name": quote(remote, safe="/"),
        "Content-Type": "application/octet-stream", "X-Bz-Content-Sha1": hashlib.sha1(data).hexdigest(),
    })
    if r.status_code != 200:
        raise B2Error(f"B2 upload zlyhal ({r.status_code})")
    return remote


def test_b2() -> tuple[bool, str]:
    key_id, app_key, bucket, _ = _b2_creds()
    if not (key_id and app_key and bucket):
        return False, "Vyplň B2 keyID, applicationKey a bucket."
    try:
        auth = _b2_auth(key_id, app_key)
        _b2_bucket_id(auth, bucket)
        return True, "B2 pripojenie funguje."
    except (B2Error, httpx.HTTPError) as e:
        log.warning("B2 test: %s", e)
        return False, str(e) if isinstance(e, B2Error) else "B2 nedostupné (sieť)."


def run_backup(tag: str = "auto") -> dict:
    name = make_local(tag)
    result = {"local": name, "b2": None, "error": None}
    try:
        if settings.get("b2_key_id") and settings.get("b2_app_key") and settings.get("b2_bucket"):
            result["b2"] = upload_b2(name)
    except (B2Error, httpx.HTTPError) as e:
        result["error"] = str(e) if isinstance(e, B2Error) else "B2 upload: sieťová chyba"
        log.warning("B2 upload zlyhal: %s", e)
    prune_local(settings.get("backup_keep"))
    db.log_history("system", "backup", result)
    return result
