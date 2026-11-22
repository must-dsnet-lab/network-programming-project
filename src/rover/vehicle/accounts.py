"""Persist vehicle accounts and assign their roles from the vehicle host."""

import argparse
from contextlib import closing
from hashlib import pbkdf2_hmac
import os
from pathlib import Path
import re
from secrets import compare_digest, token_bytes
import sqlite3
import sys


_ACCOUNT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_ROLES = ("observer", "operator")
_MIN_PASSWORD_CHARS = 12
_MAX_PASSWORD_BYTES = 1024
_KDF_ITERATIONS = 600_000
_SALT_BYTES = 16
_DIGEST_BYTES = 32
_DUMMY_SALT = bytes(_SALT_BYTES)
_DUMMY_DIGEST = bytes(_DIGEST_BYTES)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    client_id TEXT NOT NULL PRIMARY KEY,
    role TEXT NOT NULL DEFAULT 'observer' CHECK (role IN ('observer', 'operator')),
    salt BLOB NOT NULL,
    password_hash BLOB NOT NULL,
    iterations INTEGER NOT NULL CHECK (iterations > 0)
)
"""


def _valid_account(client_id: object) -> bool:
    return isinstance(client_id, str) and _ACCOUNT_ID.fullmatch(client_id) is not None


def _password_bytes(credential: object) -> bytes | None:
    if not isinstance(credential, str) or not 1 <= len(credential) <= _MAX_PASSWORD_BYTES:
        return None
    try:
        encoded = credential.encode("utf-8")
    except UnicodeError:
        return None
    return encoded if len(encoded) <= _MAX_PASSWORD_BYTES else None


def _password_digest(password: bytes, salt: bytes, iterations: int) -> bytes:
    return pbkdf2_hmac("sha256", password, salt, iterations, dklen=_DIGEST_BYTES)


class AccountStore:
    """SQLite account store with separate connections for concurrent callers."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Create privately on platforms that implement Unix modes. Windows
        # normally inherits the account-data directory's ACL.
        try:
            descriptor = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with closing(self._connect()) as database, database:
            database.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def register(self, client_id: str, credential: str) -> str | None:
        """Register an observer and return a refusal reason when registration fails."""
        if not _valid_account(client_id):
            return "invalid_account"
        password = _password_bytes(credential)
        if password is None or len(credential) < _MIN_PASSWORD_CHARS or not credential.strip():
            return "invalid_credential"

        salt = token_bytes(_SALT_BYTES)
        digest = _password_digest(password, salt, _KDF_ITERATIONS)
        with closing(self._connect()) as database, database:
            row = database.execute(
                "INSERT INTO accounts (client_id, salt, password_hash, iterations) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(client_id) DO NOTHING",
                (client_id, salt, digest, _KDF_ITERATIONS),
            )
            return None if row.rowcount == 1 else "account_exists"

    def authenticate(self, client_id: str, credential: str) -> str | None:
        """Return the account's role after successful password verification."""
        password = _password_bytes(credential)
        if not _valid_account(client_id) or password is None:
            return None
        with closing(self._connect()) as database:
            row = database.execute(
                "SELECT role, salt, password_hash, iterations FROM accounts WHERE client_id = ?",
                (client_id,),
            ).fetchone()

        # Use the same KDF path for absent accounts and incorrect passwords.
        role, salt, expected, iterations = (
            row if row is not None else (None, _DUMMY_SALT, _DUMMY_DIGEST, _KDF_ITERATIONS)
        )
        digest = _password_digest(password, salt, iterations)
        return role if compare_digest(digest, expected) else None

    def role(self, client_id: str) -> str | None:
        if not _valid_account(client_id):
            return None
        with closing(self._connect()) as database:
            row = database.execute("SELECT role FROM accounts WHERE client_id = ?", (client_id,)).fetchone()
        return row[0] if row is not None else None

    def set_role(self, client_id: str, role: str) -> bool:
        if not _valid_account(client_id) or role not in _ROLES:
            return False
        with closing(self._connect()) as database, database:
            row = database.execute("UPDATE accounts SET role = ? WHERE client_id = ?", (role, client_id))
            return row.rowcount == 1

    def list_accounts(self) -> list[tuple[str, str]]:
        with closing(self._connect()) as database:
            return database.execute("SELECT client_id, role FROM accounts ORDER BY client_id").fetchall()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage Rover accounts from the vehicle host")
    parser.add_argument("--db", required=True, type=Path, help="path to the vehicle account database")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list account names and roles")
    set_role = commands.add_parser("set-role", help="assign a role to an existing account")
    set_role.add_argument("client_id")
    set_role.add_argument("role", choices=_ROLES)
    args = parser.parse_args(argv)

    try:
        store = AccountStore(args.db)
        if args.command == "list":
            for client_id, role in store.list_accounts():
                print(f"{client_id}\t{role}")
        elif not store.set_role(args.client_id, args.role):
            print("Account not found or invalid account name.", file=sys.stderr)
            return 1
    except (OSError, sqlite3.Error) as error:
        print(f"Account database unavailable: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
