"""Atomic SQLite snapshots and a durable command journal, on the host's machine."""

import json
import os
import secrets
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        key_path = directory / "host-key.txt"
        if not key_path.exists():
            fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(32))
        self.host_key = key_path.read_text().strip()
        self.db = sqlite3.connect(directory / "session.sqlite3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY, body TEXT)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY, revision INTEGER, "
            "actor TEXT, action TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )

    def load(self) -> dict | None:
        row = self.db.execute("SELECT body FROM snapshot WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def commit(self, state: dict, actor: str, action: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO snapshot VALUES (1, ?)",
                (json.dumps(state, separators=(",", ":")),),
            )
            self.db.execute(
                "INSERT INTO journal(revision,actor,action) VALUES (?,?,?)",
                (state["revision"], actor, action),
            )
            self.db.execute("DELETE FROM journal WHERE id < (SELECT MAX(id)-10000 FROM journal)")

    def close(self) -> None:
        self.db.close()

    def checkpoint(self, name: str, data: bytes) -> None:
        temporary = self.directory / (name + ".tmp")
        fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, self.directory / name)
