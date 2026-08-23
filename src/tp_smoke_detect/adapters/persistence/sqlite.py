"""Small SQLite adapter for the deterministic CPU/reference profile.

The adapter stores JSON payloads alongside indexed fields.  This keeps the
append-only audit facts lossless while allowing the API's common filters to use
indexes.  Production uses the same repository port with PostgreSQL migrations.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("payload", "reason_codes", "evidence_channels", "model_revisions", "result"):
        if key in item and item[key] is not None:
            item[key] = json.loads(item[key])
    return item


class SQLiteAuditRepository:
    """Thread-safe-enough single-process repository for tests and local runs."""

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.database = str(database)
        self.connection = sqlite3.connect(self.database, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                decision_id TEXT PRIMARY KEY,
                camera_id TEXT NOT NULL,
                track_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                reason_codes TEXT NOT NULL,
                evidence_channels TEXT NOT NULL,
                model_revisions TEXT NOT NULL,
                policy_revision TEXT NOT NULL,
                latency_ms REAL NOT NULL,
                mode TEXT NOT NULL,
                audio_eligibility INTEGER NOT NULL,
                audio_outcome TEXT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_decisions_camera_created
                ON decisions(camera_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_decisions_outcome_created
                ON decisions(outcome, created_at);
            CREATE TABLE IF NOT EXISTS reviews (
                review_id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
                label TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reviews_decision ON reviews(decision_id, created_at);
            CREATE TABLE IF NOT EXISTS mode_changes (
                change_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                reason TEXT NOT NULL,
                actor TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cameras (
                camera_id TEXT PRIMARY KEY,
                revision TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evaluations (
                evaluation_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                idempotency_key TEXT UNIQUE,
                result TEXT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mutes (
                mute_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                scope_id TEXT,
                expires_at TEXT,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_releases (
                model_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def health(self) -> bool:
        try:
            self.connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def close(self) -> None:
        self.connection.close()

    def put_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        item = dict(decision)
        item.setdefault("created_at", _now())
        self.connection.execute(
            """INSERT INTO decisions
            (decision_id,camera_id,track_id,outcome,reason_codes,evidence_channels,
             model_revisions,policy_revision,latency_ms,mode,audio_eligibility,
             audio_outcome,payload,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item["decision_id"],
                item["camera_id"],
                item["track_id"],
                item["outcome"],
                _json(item["reason_codes"]),
                _json(item.get("evidence_channels", [])),
                _json(item.get("model_revisions", {})),
                item["policy_revision"],
                item["latency_ms"],
                item["mode"],
                int(item["audio_eligibility"]),
                item.get("audio_outcome"),
                _json(item),
                item["created_at"],
            ),
        )
        self.connection.commit()
        return item

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        return _decode(row) if row else None

    def list_decisions(
        self,
        *,
        camera_id: str | None = None,
        outcome: str | None = None,
        reason: str | None = None,
        before: datetime | None = None,
        after: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if camera_id:
            clauses.append("camera_id = ?")
            values.append(camera_id)
        if outcome:
            clauses.append("outcome = ?")
            values.append(outcome)
        if reason:
            clauses.append("EXISTS (SELECT 1 FROM json_each(reason_codes) WHERE value = ?)")
            values.append(reason)
        if before:
            clauses.append("created_at < ?")
            values.append(before.isoformat())
        if after:
            clauses.append("created_at >= ?")
            values.append(after.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"SELECT * FROM decisions{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*values, limit, offset),
        ).fetchall()
        return [_decode(row) for row in rows]

    def append_review(self, review: dict[str, Any]) -> dict[str, Any]:
        item = dict(review)
        item.setdefault("created_at", _now())
        self.connection.execute(
            """INSERT INTO reviews
            (review_id,decision_id,label,actor,reason,created_at,payload)
            VALUES (?,?,?,?,?,?,?)""",
            (
                item["review_id"],
                item["decision_id"],
                item["label"],
                item["actor"],
                item.get("reason"),
                item["created_at"],
                _json(item),
            ),
        )
        self.connection.commit()
        return item

    def append_mode_change(self, change: dict[str, Any]) -> dict[str, Any]:
        item = dict(change)
        item.setdefault("created_at", _now())
        self.connection.execute(
            """INSERT INTO mode_changes
            (change_id,mode,reason,actor,created_at,payload)
            VALUES (?,?,?,?,?,?)""",
            (
                item["change_id"],
                item["mode"],
                item["reason"],
                item["actor"],
                item["created_at"],
                _json(item),
            ),
        )
        self.connection.commit()
        return item

    def current_mode(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT payload FROM mode_changes ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return dict(json.loads(row[0])) if row else None

    def upsert_camera(self, camera: dict[str, Any]) -> dict[str, Any]:
        item = dict(camera)
        item.setdefault("updated_at", _now())
        self.connection.execute(
            """INSERT INTO cameras(camera_id,revision,payload,updated_at) VALUES(?,?,?,?)
            ON CONFLICT(camera_id) DO UPDATE SET revision=excluded.revision,
            payload=excluded.payload, updated_at=excluded.updated_at""",
            (item["camera_id"], item["revision"], _json(item), item["updated_at"]),
        )
        self.connection.commit()
        return item

    def list_cameras(self) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.connection.execute("SELECT payload FROM cameras ORDER BY camera_id")
        ]

    def put_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        item = dict(artifact)
        item.setdefault("created_at", _now())
        item.setdefault("deleted", False)
        self.connection.execute(
            """INSERT INTO artifacts
            (artifact_id,path,media_type,size_bytes,deleted,created_at,payload)
            VALUES(?,?,?,?,?,?,?)""",
            (
                item["artifact_id"],
                item["path"],
                item["media_type"],
                item["size_bytes"],
                int(item["deleted"]),
                item["created_at"],
                _json(item),
            ),
        )
        self.connection.commit()
        return item

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT payload FROM artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put_evaluation(self, evaluation: dict[str, Any]) -> dict[str, Any]:
        item = dict(evaluation)
        item.setdefault("created_at", _now())
        item.setdefault("updated_at", item["created_at"])
        self.connection.execute(
            """INSERT INTO evaluations
            (evaluation_id,status,idempotency_key,result,payload,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)""",
            (
                item["evaluation_id"],
                item["status"],
                item.get("idempotency_key"),
                _json(item["result"]) if item.get("result") is not None else None,
                _json(item),
                item["created_at"],
                item["updated_at"],
            ),
        )
        self.connection.commit()
        return item

    def get_evaluation(self, evaluation_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM evaluations WHERE evaluation_id=?", (evaluation_id,)
        ).fetchone()
        if not row:
            return None
        item = _decode(row)
        payload = cast(dict[str, Any], item.pop("payload", {}))
        payload.update(
            {
                "evaluation_id": item["evaluation_id"],
                "status": item["status"],
                "result": item.get("result"),
            }
        )
        return payload

    def get_evaluation_by_idempotency(self, key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT evaluation_id FROM evaluations WHERE idempotency_key=?", (key,)
        ).fetchone()
        return self.get_evaluation(row[0]) if row else None

    def put_mute(self, mute: dict[str, Any]) -> dict[str, Any]:
        item = dict(mute)
        item.setdefault("created_at", _now())
        self.connection.execute(
            """INSERT INTO mutes
            (mute_id,scope,scope_id,expires_at,actor,reason,created_at,payload)
            VALUES(?,?,?,?,?,?,?,?)""",
            (
                item["mute_id"],
                item["scope"],
                item.get("scope_id"),
                item.get("expires_at"),
                item["actor"],
                item["reason"],
                item["created_at"],
                _json(item),
            ),
        )
        self.connection.commit()
        return item

    def list_models(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT payload FROM model_releases ORDER BY created_at DESC"
        ).fetchall()
        return [json.loads(row[0]) for row in rows]
