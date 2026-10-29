"""`python -m horus_media`: run Horus.

Host and port come from HORUS_HOST / HORUS_PORT if set, otherwise from the Settings page
(default: this computer only, port 8000).

    python -m horus_media                      run Horus
    python -m horus_media --db PATH            run with the database at PATH
    python -m horus_media startup install      (Windows) start Horus when you sign in
    python -m horus_media startup remove|status
    python -m horus_media create-admin --username NAME   create the first admin account (the
                                                         password is read from standard input)
    python -m horus_media configure --port N [--listen-all]   save the port / network setting
"""

import argparse
import os
import sys

import uvicorn
from pydantic import ValidationError

from . import app_settings, auth, listen, startup
from .config import db_path
from .db import connect, migrate


def serve() -> None:
    conn = connect(db_path())
    try:
        migrate(conn)
        auth.admin_from_environment(conn)  # before resolving: an admin lets Horus open to the network
        where = listen.resolve(conn)
    finally:
        conn.close()
    listen.record_running(where)
    if not listen.wait_until_free(where.host, where.port):
        sys.exit(f"Port {where.port} is already in use. Pick another one with HORUS_PORT, "
                 "or stop whatever is using it.")
    if sys.stderr is None or sys.stdout is None:  # started by pythonw at login: no console
        sys.stdout = sys.stderr = open(os.devnull, "w")  # noqa: SIM115 - lives as long as Horus
    uvicorn.run("horus_media.main:app", host=where.host, port=where.port)


def startup_command(action: str) -> None:
    try:
        if action == "install":
            startup.install(db_path())
            print(f"Horus will start when you sign in to Windows. Data folder: "
                  f"{db_path().resolve().parent}")
        elif action == "remove":
            print("Horus will no longer start at sign-in." if startup.remove()
                  else "Horus was not set to start at sign-in.")
        else:
            current = startup.status()
            print(f"Starts at sign-in: {current}" if current else "Not set to start at sign-in.")
    except startup.StartupError as e:
        sys.exit(str(e))


def create_admin_command(username: str) -> None:
    """Make the first admin account, for installers. The password comes from standard input
    (first line), never from the command line, so it does not show up in a process list or log."""
    password = sys.stdin.readline().rstrip("\r\n")
    conn = connect(db_path())
    try:
        migrate(conn)
        if auth.admin_exists(conn):
            sys.exit("An admin account already exists, so nothing was changed.")
        try:
            user = auth.create_user(conn, username, password, "admin")
        except auth.UserError as e:
            sys.exit(str(e))
    finally:
        conn.close()
    print(f"Admin account created: {user['username']}")


def configure_command(port: int | None, listen_all: bool) -> None:
    """Save the port / network setting from an installer, so it can still be changed later on the
    Settings page (an environment variable would lock it)."""
    changes: dict = {"listen_all": listen_all}
    if port is not None:
        changes["http_port"] = port
    conn = connect(db_path())
    try:
        migrate(conn)
        try:
            app_settings.update(conn, changes)
        except ValidationError as e:
            sys.exit(f"{e.errors()[0]['loc'][0]}: {e.errors()[0]['msg']}")
    finally:
        conn.close()
    print("Saved: " + ", ".join(f"{k}={v}" for k, v in changes.items()))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m horus_media")
    parser.add_argument("--db", help="path of the database file (default ./data/horus.db)")
    sub = parser.add_subparsers(dest="command")
    start = sub.add_parser("startup", help="start Horus when you sign in (Windows)")
    start.add_argument("action", choices=["install", "remove", "status"])
    admin = sub.add_parser("create-admin", help="create the first admin account (password on stdin)")
    admin.add_argument("--username", required=True)
    conf = sub.add_parser("configure", help="save the port and network setting (for installers)")
    conf.add_argument("--port", type=int)
    conf.add_argument("--listen-all", action="store_true", help="let other computers connect")
    args = parser.parse_args(argv or [])
    if args.db:
        os.environ["HORUS_DB_PATH"] = args.db  # also inherited by a restart from Settings
    if args.command == "startup":
        startup_command(args.action)
    elif args.command == "create-admin":
        create_admin_command(args.username)
    elif args.command == "configure":
        configure_command(args.port, args.listen_all)
    else:
        serve()


if __name__ == "__main__":
    main(sys.argv[1:])
