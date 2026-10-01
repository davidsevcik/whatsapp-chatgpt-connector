import hashlib
import json
from pathlib import Path
import sqlite3
from collections import Counter

from .parser import Message


class Store:
    def __init__(self, path: str):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, chat TEXT NOT NULL, fingerprint TEXT NOT NULL,
                timestamp TEXT NOT NULL, sender TEXT, text TEXT NOT NULL,
                UNIQUE(chat, fingerprint)
            );
            CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat, timestamp, id);
            CREATE TABLE IF NOT EXISTS analyses (
                chat TEXT PRIMARY KEY, snapshot TEXT NOT NULL, result TEXT NOT NULL,
                analyzed_at TEXT NOT NULL, dismissed INTEGER NOT NULL DEFAULT 0
            );
        """)

    def close(self):
        self.db.close()

    def ingest(self, chat: str, messages: list[Message]) -> int:
        occurrences: Counter = Counter()
        count = 0
        with self.db:
            for message in messages:
                identity = json.dumps([message.timestamp, message.sender, message.text], ensure_ascii=False)
                occurrences[identity] += 1
                fingerprint = hashlib.sha256(f"{identity}:{occurrences[identity]}".encode()).hexdigest()
                cursor = self.db.execute(
                    "INSERT OR IGNORE INTO messages(chat,fingerprint,timestamp,sender,text) VALUES (?,?,?,?,?)",
                    (chat, fingerprint, message.timestamp, message.sender, message.text),
                )
                count += cursor.rowcount
            if count:
                # Invalidate even for an older backfill: stale results never enter the digest.
                self.db.execute("DELETE FROM analyses WHERE chat=?", (chat,))
        return count

    def chats(self):
        return [row[0] for row in self.db.execute("SELECT DISTINCT chat FROM messages ORDER BY chat")]

    def messages(self, chat: str, limit: int):
        rows = self.db.execute(
            "SELECT id,timestamp,sender,text FROM messages WHERE chat=? ORDER BY timestamp DESC,id DESC LIMIT ?",
            (chat, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def cached(self, chat: str, snapshot: str) -> bool:
        return self.db.execute("SELECT 1 FROM analyses WHERE chat=? AND snapshot=?", (chat, snapshot)).fetchone() is not None

    def save(self, chat: str, snapshot: str, result: dict, analyzed_at: str):
        with self.db:
            self.db.execute(
                "INSERT INTO analyses(chat,snapshot,result,analyzed_at) VALUES (?,?,?,?) "
                "ON CONFLICT(chat) DO UPDATE SET snapshot=excluded.snapshot,result=excluded.result,"
                "analyzed_at=excluded.analyzed_at,dismissed=0",
                (chat, snapshot, json.dumps(result, ensure_ascii=False), analyzed_at),
            )

    def digest(self):
        items = []
        for row in self.db.execute("SELECT * FROM analyses WHERE dismissed=0"):
            result = json.loads(row["result"])
            if result["needs_attention"]:
                items.append({"chat": row["chat"], "analyzed_at": row["analyzed_at"], **result})
        rank = {"high": 0, "medium": 1, "low": 2, "none": 3}
        return sorted(items, key=lambda item: (rank[item["urgency"]], item["chat"]))

    def dismiss(self, chat: str):
        with self.db:
            return self.db.execute("UPDATE analyses SET dismissed=1 WHERE chat=?", (chat,)).rowcount

    def search(self, query: str):
        return [dict(row) for row in self.db.execute(
            "SELECT id,chat,timestamp,sender,text FROM messages WHERE instr(lower(text),lower(?))>0 "
            "ORDER BY timestamp DESC,id DESC LIMIT 50", (query,)
        )]
