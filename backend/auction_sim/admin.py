"""Explicit, local administrator provisioning. Passwords never appear in argv."""

import argparse
import getpass
import os

from .auth import AuthError, AuthService
from .storage import Store


def main():
    parser = argparse.ArgumentParser(description="Provision an Auction Lab administrator locally")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create a new admin (prompts for its password)")
    create.add_argument("username")
    promote = commands.add_parser(
        "promote", help="Promote an existing user and revoke its sessions"
    )
    promote.add_argument("username")
    args = parser.parse_args()
    location = os.environ.get("AUCTION_DATABASE_URL") or os.environ.get(
        "AUCTION_DB_PATH", "data/auction.sqlite3"
    )
    store = Store(location)
    try:
        store.initialize()
        auth = AuthService(store)
        if args.command == "create":
            password = getpass.getpass("Admin password (10–128 characters): ")
            confirmation = getpass.getpass("Repeat password: ")
            if password != confirmation:
                parser.error("Passwords do not match")
            user = auth.create_admin(args.username, password)
        else:
            user = auth.promote(args.username)
        print(f"Administrator ready: {user['username']}")
    except AuthError as exc:
        parser.error(str(exc))
    finally:
        store.close()


if __name__ == "__main__":
    main()
