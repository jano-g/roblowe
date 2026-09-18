"""CLI: python -m app.cli set-password <user> | users | backup | cycle"""
from __future__ import annotations

import getpass
import sys

from . import auth, db


def main(argv: list[str]) -> int:
    db.migrate()
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "set-password" and len(argv) > 2:
        pw = getpass.getpass("Nové heslo (aspoň 8 znakov): ")
        if len(pw) < 8:
            print("Heslo musí mať aspoň 8 znakov.")
            return 1
        print("OK" if auth.set_password(argv[2], pw) else "Používateľ neexistuje.")
        return 0
    if cmd == "users":
        for r in db.rows("SELECT username, created_at FROM users"):
            print(r["username"], r["created_at"])
        return 0
    if cmd == "backup":
        from .services import backup

        print(backup.run_backup("manual"))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
