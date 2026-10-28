"""Password authentication, revocable sessions and shared database rate limits."""

import hashlib
import re
import secrets
import time
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy.exc import IntegrityError

from .storage import Store, timestamp

SESSION_SECONDS = 12 * 60 * 60
PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
USERNAME = re.compile(r"^[a-z0-9_-]{3,32}$")
# A real hash also makes nonexistent-user password checks run through Argon2.
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))


class AuthError(ValueError):
    pass


class InvalidCredentials(AuthError):
    pass


class UsernameTaken(AuthError):
    pass


class RateLimited(AuthError):
    def __init__(self, retry_after: int):
        super().__init__("Too many attempts. Please try again later.")
        self.retry_after = retry_after


def public_user(row) -> dict:
    return {key: row[key] for key in ("id", "username", "role")}


def normalize_username(username: str) -> str:
    username = username.strip().lower()
    if not USERNAME.fullmatch(username):
        raise AuthError("Username must be 3–32 letters, numbers, underscores or hyphens")
    return username


def validate_password(password: str):
    if not 5 <= len(password) <= 20:
        raise AuthError("Password must contain 5–20 characters")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, store: Store):
        self.store = store

    def _limit(self, namespace: str, value: str, *, limit: int, window: int):
        now = int(time.time())
        expires = (now // window + 1) * window
        key = token_hash(f"{namespace}:{now // window}:{value}")
        # Atomic increments work across multiple server processes.
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM auth_attempts WHERE expires_at <= ?", (now,))
            conn.execute(
                "INSERT INTO auth_attempts(key, attempts, expires_at) VALUES (?, 1, ?) "
                "ON CONFLICT(key) DO UPDATE SET attempts = auth_attempts.attempts + 1",
                (key, expires),
            )
            row = conn.execute(
                "SELECT attempts FROM auth_attempts WHERE key = ?", (key,)
            ).fetchone()
        if row["attempts"] > limit:
            raise RateLimited(max(1, expires - now))

    def _create_user(self, username: str, password: str, role: str) -> dict:
        username = normalize_username(username)
        validate_password(password)
        user = {"id": str(uuid4()), "username": username, "role": role}
        encoded = PASSWORDS.hash(password)
        try:
            with self.store.transaction() as conn:
                conn.execute(
                    "INSERT INTO users(id, username, password_hash, role, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user["id"], username, encoded, role, timestamp()),
                )
        except IntegrityError as exc:
            raise UsernameTaken("Username is already in use") from exc
        return user

    def register(self, username: str, password: str, *, remote_key: str = "local") -> dict:
        self._limit("register-source", remote_key, limit=5, window=600)
        # Public registration cannot choose a role, including for the first user.
        return self._create_user(username, password, "user")

    def create_admin(self, username: str, password: str) -> dict:
        """Local operator command only; never expose this through registration."""
        return self._create_user(username, password, "admin")

    def promote(self, username: str) -> dict:
        """Local operator command; invalidate old sessions when the role changes."""
        username = normalize_username(username)
        with self.store.transaction() as conn:
            lock = " FOR UPDATE" if conn.dialect == "postgresql" else ""
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?" + lock, (username,)
            ).fetchone()
            if row is None:
                raise AuthError("User does not exist")
            conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (row["id"],))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
            return {"id": row["id"], "username": username, "role": "admin"}

    def login(self, username: str, password: str, *, remote_key: str = "local") -> dict:
        self._limit("login-source", remote_key, limit=30, window=300)
        username = normalize_username(username)
        self._limit("login-user", username, limit=10, window=300)
        # Verify existing hashes even if their passwords predate the current creation policy.
        if not 1 <= len(password) <= 128:
            raise InvalidCredentials("Invalid username or password")
        with self.store.reader() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        try:
            PASSWORDS.verify(row["password_hash"] if row else DUMMY_HASH, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            raise InvalidCredentials("Invalid username or password") from None
        if row is None:
            raise InvalidCredentials("Invalid username or password")
        user = public_user(row)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        expires = now + SESSION_SECONDS
        rehash = (
            PASSWORDS.hash(password) if PASSWORDS.check_needs_rehash(row["password_hash"]) else None
        )
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            if rehash:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?", (rehash, user["id"])
                )
            conn.execute(
                "INSERT INTO sessions(token_hash, user_id, csrf_token, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (token_hash(token), user["id"], csrf, expires),
            )
        return {"user": user, "token": token, "csrf_token": csrf, "expires_at": expires}

    def authenticate(self, token: str | None) -> dict | None:
        if not token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        with self.store.reader() as conn:
            row = conn.execute(
                "SELECT users.id, users.username, users.role, sessions.csrf_token, "
                "sessions.expires_at FROM sessions JOIN users ON users.id = sessions.user_id "
                "WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
                (token_hash(token), int(time.time())),
            ).fetchone()
        if row is None:
            return None
        return {
            "user": public_user(row),
            "csrf_token": row["csrf_token"],
            "expires_at": row["expires_at"],
        }

    def logout(self, token: str | None):
        if token:
            with self.store.transaction() as conn:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))

    def list_users(self) -> list[dict]:
        with self.store.reader() as conn:
            return [
                public_user(row) for row in conn.execute("SELECT * FROM users ORDER BY username")
            ]
