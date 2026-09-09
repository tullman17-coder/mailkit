"""SQLite index for messages, events, subscriptions, cursors, and rules."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from mailkit.models import Event, EventFilter, Message, apply_system_flags, utcnow
from mailkit.paths import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  provider_id TEXT NOT NULL,
  mailbox TEXT NOT NULL,
  uid INTEGER,
  uidvalidity INTEGER,
  native_id TEXT,
  message_id TEXT,
  thread_id TEXT,
  date TEXT,
  subject TEXT,
  from_json TEXT,
  to_json TEXT,
  cc_json TEXT,
  flags_json TEXT,
  labels_json TEXT,
  tags_json TEXT,
  unread INTEGER,
  flagged INTEGER,
  draft INTEGER,
  answered INTEGER,
  has_attachments INTEGER,
  attachment_types_json TEXT,
  snippet TEXT,
  size INTEGER,
  payload_json TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_account_mailbox ON messages(account_id, mailbox);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_native ON messages(account_id, native_id);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  account_id TEXT,
  provider_id TEXT,
  mailbox TEXT,
  type TEXT NOT NULL,
  thread_id TEXT,
  idempotency_key TEXT UNIQUE,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_account ON events(account_id, ts);

CREATE TABLE IF NOT EXISTS subscriptions (
  id TEXT PRIMARY KEY,
  name TEXT,
  filter_json TEXT NOT NULL,
  cursor TEXT,
  durable INTEGER NOT NULL DEFAULT 1,
  ack_required INTEGER NOT NULL DEFAULT 1,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS acks (
  subscription_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  next_retry TEXT,
  updated_at TEXT,
  PRIMARY KEY (subscription_id, event_id)
);

CREATE TABLE IF NOT EXISTS webhooks (
  id TEXT PRIMARY KEY,
  name TEXT,
  url TEXT NOT NULL,
  filter_json TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS cursors (
  account_id TEXT NOT NULL,
  mailbox TEXT NOT NULL,
  kind TEXT NOT NULL,
  value TEXT,
  PRIMARY KEY (account_id, mailbox, kind)
);

CREATE TABLE IF NOT EXISTS rules (
  id TEXT PRIMARY KEY,
  name TEXT,
  priority INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  stop INTEGER NOT NULL DEFAULT 0,
  match_json TEXT NOT NULL,
  actions_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "answered" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN answered INTEGER")
        conn.commit()


def connect(root: Path | None = None) -> sqlite3.Connection:
    path = db_path(root)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _ensure_columns(conn)
    return conn


class Store:
    def __init__(self, root: Path | None = None):
        self.conn = connect(root)

    def close(self) -> None:
        self.conn.close()

    # --- messages ---
    def upsert_message(self, message: Message) -> None:
        payload = message.to_dict(include_body=True)
        self.conn.execute(
            """
            INSERT INTO messages (
              id, account_id, provider_id, mailbox, uid, uidvalidity, native_id,
              message_id, thread_id, date, subject, from_json, to_json, cc_json,
              flags_json, labels_json, tags_json, unread, flagged, draft, answered,
              has_attachments, attachment_types_json, snippet, size, payload_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              mailbox=excluded.mailbox, uid=excluded.uid, flags_json=excluded.flags_json,
              labels_json=excluded.labels_json, tags_json=excluded.tags_json,
              unread=excluded.unread, flagged=excluded.flagged, draft=excluded.draft,
              answered=excluded.answered,
              snippet=excluded.snippet, payload_json=excluded.payload_json, updated_at=excluded.updated_at
            """,
            (
                message.id,
                message.account_id,
                message.provider_id,
                message.mailbox,
                message.uid,
                message.uidvalidity,
                message.native_id,
                message.message_id,
                message.thread_id,
                message.date,
                message.subject,
                json.dumps([a.__dict__ if hasattr(a, "__dict__") else a for a in message.from_]),
                json.dumps([a.__dict__ if hasattr(a, "__dict__") else a for a in message.to]),
                json.dumps([a.__dict__ if hasattr(a, "__dict__") else a for a in message.cc]),
                json.dumps(message.flags),
                json.dumps(message.labels),
                json.dumps(message.tags),
                int(message.unread),
                int(message.flagged),
                int(message.draft),
                int(message.answered),
                int(message.has_attachments),
                json.dumps(message.attachment_types),
                message.snippet,
                message.size,
                json.dumps(payload),
                utcnow(),
            ),
        )
        self.conn.commit()

    def get_message(self, message_id: str) -> dict | None:
        row = self.conn.execute("SELECT payload_json, tags_json FROM messages WHERE id=?", (message_id,)).fetchone()
        if not row:
            return None
        data = json.loads(row["payload_json"])
        data["tags"] = json.loads(row["tags_json"] or "[]")
        return data

    def delete_message(self, message_id: str) -> None:
        self.conn.execute("DELETE FROM messages WHERE id=?", (message_id,))
        self.conn.commit()

    def set_tags(self, message_id: str, tags: list[str]) -> dict | None:
        row = self.get_message(message_id)
        if not row:
            return None
        unique = sorted(set(tags))
        row["tags"] = unique
        self.conn.execute(
            "UPDATE messages SET tags_json=?, payload_json=?, updated_at=? WHERE id=?",
            (json.dumps(unique), json.dumps(row), utcnow(), message_id),
        )
        self.conn.commit()
        return row

    def patch_message_flags(
        self,
        message_id: str,
        *,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> dict | None:
        """Patch cached flags including answered after a successful IMAP STORE."""
        row = self.get_message(message_id)
        if not row:
            return None
        updated = apply_system_flags(row, add=add, remove=remove)
        self.conn.execute(
            """
            UPDATE messages SET flagged=?, unread=?, draft=?, answered=?, flags_json=?, payload_json=?, updated_at=?
            WHERE id=?
            """,
            (
                int(bool(updated.get("flagged"))),
                int(bool(updated.get("unread"))),
                int(bool(updated.get("draft"))),
                int(bool(updated.get("answered"))),
                json.dumps(updated["flags"]),
                json.dumps(updated),
                utcnow(),
                message_id,
            ),
        )
        self.conn.commit()
        return updated

    def list_messages(
        self,
        *,
        account_id: str | None = None,
        mailbox: str | None = None,
        unread: bool | None = None,
        flagged: bool | None = None,
        tagged: str | None = None,
        since: str | None = None,
        before: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        sql = "SELECT payload_json, tags_json FROM messages WHERE 1=1"
        args: list[Any] = []
        if account_id:
            sql += " AND account_id=?"
            args.append(account_id)
        if mailbox:
            sql += " AND mailbox=?"
            args.append(mailbox)
        if unread is not None:
            sql += " AND unread=?"
            args.append(int(unread))
        if flagged is not None:
            sql += " AND flagged=?"
            args.append(int(flagged))
        if tagged:
            sql += " AND tags_json LIKE ?"
            args.append(f"%{tagged}%")
        if since:
            sql += " AND date>=?"
            args.append(since)
        if before:
            sql += " AND date<=?"
            args.append(before)
        if query:
            sql += " AND (subject LIKE ? OR snippet LIKE ? OR from_json LIKE ?)"
            like = f"%{query}%"
            args.extend([like, like, like])
        sql += " ORDER BY date DESC, updated_at DESC LIMIT ? OFFSET ?"
        args.extend([limit, offset])
        rows = self.conn.execute(sql, args).fetchall()
        out = []
        for row in rows:
            data = json.loads(row["payload_json"])
            data["tags"] = json.loads(row["tags_json"] or "[]")
            data.pop("body_text", None)
            data.pop("body_html", None)
            out.append(data)
        return out

    # --- events ---
    def append_event(self, event: Event) -> Event | None:
        try:
            self.conn.execute(
                """
                INSERT INTO events (id, ts, account_id, provider_id, mailbox, type, thread_id, idempotency_key, payload_json)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.id,
                    event.ts,
                    event.account_id,
                    event.provider_id,
                    event.mailbox,
                    event.type,
                    event.thread_id,
                    event.idempotency_key or None,
                    json.dumps(event.to_dict()),
                ),
            )
            self.conn.commit()
            return event
        except sqlite3.IntegrityError:
            self.conn.rollback()
            return None

    def get_event(self, event_id: str) -> dict | None:
        row = self.conn.execute("SELECT payload_json FROM events WHERE id=?", (event_id,)).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def events_after(self, cursor: str | None, limit: int = 100) -> list[dict]:
        if cursor:
            row = self.conn.execute("SELECT rowid FROM events WHERE id=?", (cursor,)).fetchone()
            if row:
                rows = self.conn.execute(
                    "SELECT payload_json FROM events WHERE rowid > ? ORDER BY rowid LIMIT ?",
                    (row["rowid"], limit),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT payload_json FROM events ORDER BY rowid LIMIT ?",
                    (limit,),
                ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT payload_json FROM events ORDER BY rowid LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def last_event_id(self) -> str | None:
        row = self.conn.execute("SELECT id FROM events ORDER BY rowid DESC LIMIT 1").fetchone()
        return row["id"] if row else None

    def trim_events(self, keep: int) -> None:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        if row["n"] <= keep:
            return
        self.conn.execute(
            "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY rowid ASC LIMIT ?)",
            (row["n"] - keep,),
        )
        self.conn.commit()

    def iter_events(self) -> Iterator[dict]:
        for row in self.conn.execute("SELECT payload_json FROM events ORDER BY rowid"):
            yield json.loads(row["payload_json"])

    # --- subscriptions ---
    def save_subscription(self, sub_id: str, name: str, filt: EventFilter, *, durable: bool, ack_required: bool, cursor: str | None) -> None:
        self.conn.execute(
            """
            INSERT INTO subscriptions (id, name, filter_json, cursor, durable, ack_required, created_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, filter_json=excluded.filter_json, durable=excluded.durable, ack_required=excluded.ack_required
            """,
            (sub_id, name, json.dumps(filt.to_dict()), cursor, int(durable), int(ack_required), utcnow()),
        )
        self.conn.commit()

    def list_subscriptions(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM subscriptions ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def get_subscription(self, sub_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        return dict(row) if row else None

    def delete_subscription(self, sub_id: str) -> None:
        self.conn.execute("DELETE FROM acks WHERE subscription_id=?", (sub_id,))
        self.conn.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
        self.conn.commit()

    def set_cursor(self, sub_id: str, cursor: str) -> None:
        self.conn.execute("UPDATE subscriptions SET cursor=? WHERE id=?", (cursor, sub_id))
        self.conn.commit()

    def ack(self, sub_id: str, event_id: str, status: str = "acked", error: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO acks (subscription_id, event_id, status, attempts, last_error, updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(subscription_id, event_id) DO UPDATE SET
              status=excluded.status,
              attempts=acks.attempts + 1,
              last_error=excluded.last_error,
              updated_at=excluded.updated_at
            """,
            (sub_id, event_id, status, 1 if status != "acked" else 0, error, utcnow()),
        )
        self.conn.commit()

    def pending_acks(self, sub_id: str, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT a.event_id, e.payload_json FROM acks a
            JOIN events e ON e.id = a.event_id
            WHERE a.subscription_id=? AND a.status IN ('pending','failed')
            ORDER BY a.updated_at LIMIT ?
            """,
            (sub_id, limit),
        ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    # --- webhooks ---
    def save_webhook(self, hook_id: str, name: str, url: str, filt: EventFilter, enabled: bool = True) -> None:
        self.conn.execute(
            """
            INSERT INTO webhooks (id, name, url, filter_json, enabled, created_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, url=excluded.url, filter_json=excluded.filter_json, enabled=excluded.enabled
            """,
            (hook_id, name, url, json.dumps(filt.to_dict()), int(enabled), utcnow()),
        )
        self.conn.commit()

    def list_webhooks(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM webhooks").fetchall()]

    def delete_webhook(self, hook_id: str) -> None:
        self.conn.execute("DELETE FROM webhooks WHERE id=?", (hook_id,))
        self.conn.commit()

    # --- cursors / rules ---
    def get_sync_cursor(self, account_id: str, mailbox: str, kind: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM cursors WHERE account_id=? AND mailbox=? AND kind=?",
            (account_id, mailbox, kind),
        ).fetchone()
        return row["value"] if row else None

    def set_sync_cursor(self, account_id: str, mailbox: str, kind: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO cursors (account_id, mailbox, kind, value) VALUES (?,?,?,?)
            ON CONFLICT(account_id, mailbox, kind) DO UPDATE SET value=excluded.value
            """,
            (account_id, mailbox, kind, value),
        )
        self.conn.commit()

    def save_rule(self, rule_id: str, name: str, priority: int, enabled: bool, stop: bool, match: dict, actions: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO rules (id, name, priority, enabled, stop, match_json, actions_json)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, priority=excluded.priority,
              enabled=excluded.enabled, stop=excluded.stop, match_json=excluded.match_json, actions_json=excluded.actions_json
            """,
            (rule_id, name, priority, int(enabled), int(stop), json.dumps(match), json.dumps(actions)),
        )
        self.conn.commit()

    def list_rules(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM rules ORDER BY priority DESC, id").fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["match"] = json.loads(item.pop("match_json"))
            item["actions"] = json.loads(item.pop("actions_json"))
            item["enabled"] = bool(item["enabled"])
            item["stop"] = bool(item["stop"])
            out.append(item)
        return out

    def delete_rule(self, rule_id: str) -> None:
        self.conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
        self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def integrity(self) -> str:
        row = self.conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "unknown"

    def checkpoint(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
