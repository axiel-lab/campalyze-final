"""Multi-user account store (SQLite) — ported from the Campalyze login system.

The schema and password hashing (werkzeug) are identical to Campalyze, so an
existing ``campalyze.db`` can be reused by pointing ``DATABASE_PATH`` at it.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

from config import PROJECT_DIR
from core.logger import app_logger

DB_PATH = Path(os.getenv("DATABASE_PATH", PROJECT_DIR / "campalyze.db"))

MIN_USERNAME = 3
MAX_USERNAME = 32
MIN_PASSWORD = 12

# Used to burn the same CPU time when a username does not exist, so response
# timing does not reveal which usernames are valid.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "is_admin": bool(row["is_admin"]),
        "active": bool(row["active"]),
        "created_at": row["created_at"],
    }


def init_db() -> None:
    """Create the users table and seed initial accounts from the environment."""
    with _connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        seeds = [
            (os.getenv("ADMIN_USERNAME", "").strip(), os.getenv("ADMIN_PASSWORD", ""), 1),
            (os.getenv("USER_2_USERNAME", "").strip(), os.getenv("USER_2_PASSWORD", ""), 0),
            (os.getenv("USER_3_USERNAME", "").strip(), os.getenv("USER_3_PASSWORD", ""), 0),
        ]
        # Backward compatibility with the old single-password dashboard.
        legacy = os.getenv("DASHBOARD_PASSWORD", "")
        if legacy and not seeds[0][0]:
            seeds[0] = ("admin", legacy, 1)

        for username, password, is_admin in seeds:
            if not username or not password:
                continue
            exists = db.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            if not exists:
                db.execute(
                    "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
                    (username, generate_password_hash(password), is_admin),
                )
                app_logger.info("Đã tạo tài khoản ban đầu: %s", username)


def has_admin() -> bool:
    with _connect() as db:
        row = db.execute(
            "SELECT 1 FROM users WHERE is_admin = 1 AND active = 1 LIMIT 1"
        ).fetchone()
    return row is not None


def create_user(username: str, password: str, is_admin: bool = False) -> dict[str, Any]:
    """Create an account. Raises ``ValueError`` with a user-facing message."""
    username = username.strip()
    if not (MIN_USERNAME <= len(username) <= MAX_USERNAME):
        raise ValueError(f"Username must be {MIN_USERNAME}-{MAX_USERNAME} characters.")
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Password must be at least {MIN_PASSWORD} characters.")
    try:
        with _connect() as db:
            cur = db.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), 1 if is_admin else 0),
            )
            row = db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    except sqlite3.IntegrityError:
        raise ValueError("That username already exists.") from None
    return _row_to_dict(row)  # type: ignore[return-value]


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Return the user dict when credentials are valid and the account is active."""
    with _connect() as db:
        row = db.execute(
            "SELECT * FROM users WHERE username = ? AND active = 1", (username.strip(),)
        ).fetchone()
    if row is None:
        check_password_hash(_DUMMY_HASH, password)
        return None
    if not check_password_hash(row["password_hash"], password):
        return None
    return _row_to_dict(row)


def get_active_user(user_id: int) -> dict[str, Any] | None:
    """Look up a user by id; ``None`` if missing or disabled."""
    with _connect() as db:
        row = db.execute(
            "SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)
        ).fetchone()
    return _row_to_dict(row)


def list_users() -> list[dict[str, Any]]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
    return [_row_to_dict(r) for r in rows]  # type: ignore[misc]


def toggle_user(user_id: int, acting_user_id: int) -> dict[str, Any]:
    """Enable/disable a non-admin account. Raises ``ValueError`` on invalid input."""
    if user_id == acting_user_id:
        raise ValueError("You cannot disable your own account.")
    with _connect() as db:
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("User not found.")
        if row["is_admin"]:
            raise ValueError("Administrator accounts are protected.")
        db.execute(
            "UPDATE users SET active = ? WHERE id = ?", (0 if row["active"] else 1, user_id)
        )
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_dict(row)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Brute-force protection (in-memory, per client IP + username)
# ---------------------------------------------------------------------------
_MAX_FAILURES = 5
_WINDOW_SECONDS = 300
_failures: dict[str, list[float]] = {}


def _key(ip: str, username: str) -> str:
    return f"{ip}|{username.strip().lower()}"


def is_locked_out(ip: str, username: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(_key(ip, username), []) if now - t < _WINDOW_SECONDS]
    _failures[_key(ip, username)] = recent
    return len(recent) >= _MAX_FAILURES


def record_failure(ip: str, username: str) -> None:
    _failures.setdefault(_key(ip, username), []).append(time.time())


def clear_failures(ip: str, username: str) -> None:
    _failures.pop(_key(ip, username), None)
