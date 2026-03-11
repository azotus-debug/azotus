#!/usr/bin/env python3
"""CLI tool to manage Omega editor users.

Usage:
    python3 scripts/create_user.py add <username> <password> [--role admin|reviewer]
    python3 scripts/create_user.py list
    python3 scripts/create_user.py remove <username>
"""

import argparse
import json
import sys
from pathlib import Path

import bcrypt

USERS_FILE = Path(__file__).resolve().parent.parent / ".omega_users.json"


def _load() -> list:
    if not USERS_FILE.exists():
        return []
    with open(USERS_FILE, "r") as f:
        return json.load(f)


def _save(users: list):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)
    print(f"Saved to {USERS_FILE}")


def add_user(username: str, password: str, role: str = "admin"):
    users = _load()
    for u in users:
        if u["username"] == username:
            print(f"Error: User '{username}' already exists. Remove first.", file=sys.stderr)
            sys.exit(1)

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    users.append({"username": username, "password_hash": hashed, "role": role})
    _save(users)
    print(f"Added user '{username}' with role '{role}'")


def list_users():
    users = _load()
    if not users:
        print("No users configured.")
        return
    for u in users:
        print(f"  {u['username']:20s}  role={u.get('role', 'admin')}")


def remove_user(username: str):
    users = _load()
    before = len(users)
    users = [u for u in users if u["username"] != username]
    if len(users) == before:
        print(f"User '{username}' not found.", file=sys.stderr)
        sys.exit(1)
    _save(users)
    print(f"Removed user '{username}'")


def main():
    parser = argparse.ArgumentParser(description="Manage Omega editor users")
    sub = parser.add_subparsers(dest="command", required=True)

    add_p = sub.add_parser("add", help="Add a user")
    add_p.add_argument("username")
    add_p.add_argument("password")
    add_p.add_argument("--role", default="admin", choices=["admin", "reviewer"])

    sub.add_parser("list", help="List all users")

    rm_p = sub.add_parser("remove", help="Remove a user")
    rm_p.add_argument("username")

    args = parser.parse_args()

    if args.command == "add":
        add_user(args.username, args.password, args.role)
    elif args.command == "list":
        list_users()
    elif args.command == "remove":
        remove_user(args.username)


if __name__ == "__main__":
    main()
