"""Small SQLite adapter for the deterministic CPU/reference profile.

The adapter stores JSON payloads alongside indexed fields.  This keeps the
append-only audit facts lossless while allowing the API's common filters to use
indexes.  Production uses the same repository port with PostgreSQL migrations.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import suppress
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, cast


def _synchronized(method: Any) -> Any:
    """Serialize use of the single SQLite connection across request threads.

    ``check_same_thread=False`` permits a connection to be shared, but does not
    make SQLite cursors or transactions safe to use concurrently.  A reentrant
    lock keeps compound reads/writes and their commits indivisible while still
    allowing repository methods to call one another.
    """

    @wraps(method)
    def guarded(self: SQLiteAuditRepository, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return guarded


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
        self._lock = threading.RLock()
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
            CREATE TABLE IF NOT EXISTS retention_audits (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_id TEXT NOT NULL,
                media_class TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(artifact_id, action, status)
            );
            CREATE INDEX IF NOT EXISTS idx_retention_audits_artifact
                ON retention_audits(artifact_id, created_at);
            CREATE TABLE IF NOT EXISTS evaluations (
                evaluation_id TEXT PRIMARY KEY,
                decision_id TEXT REFERENCES decisions(decision_id) ON DELETE CASCADE,
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
            CREATE TABLE IF NOT EXISTS audio_receipts (
                receipt_id TEXT PRIMARY KEY,
                -- The CPU adapter accepts standalone playback probes; cleanup
                -- removes linked rows explicitly in the metadata transaction.
                decision_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                zone_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                command_id TEXT,
                playback TEXT,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audio_receipt_attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id TEXT NOT NULL REFERENCES audio_receipts(receipt_id) ON DELETE CASCADE,
                attempt_no INTEGER NOT NULL,
                decision_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                command_id TEXT,
                playback TEXT,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE(receipt_id, attempt_no)
            );
            CREATE INDEX IF NOT EXISTS idx_audio_attempts_receipt
                ON audio_receipt_attempts(receipt_id, attempt_no);
            CREATE INDEX IF NOT EXISTS idx_audio_receipts_zone_created
                ON audio_receipts(zone_id, created_at);
            CREATE TABLE IF NOT EXISTS model_releases (
                model_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        # Databases created by the first release need the new nullable column;
        # linked rows are still removed explicitly below for those databases,
        # where SQLite cannot alter an existing foreign-key constraint in place.
        with suppress(sqlite3.OperationalError):
            self.connection.execute("ALTER TABLE evaluations ADD COLUMN decision_id TEXT")
        self.connection.commit()

    @_synchronized
    def health(self) -> bool:
        try:
            self.connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    @_synchronized
    def close(self) -> None:
        self.connection.close()

    @_synchronized
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

    @_synchronized
    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        return _decode(row) if row else None

    @_synchronized
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

    @_synchronized
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

    @_synchronized
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

    @_synchronized
    def current_mode(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT payload FROM mode_changes ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return dict(json.loads(row[0])) if row else None

    @_synchronized
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

    @_synchronized
    def list_cameras(self) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.connection.execute("SELECT payload FROM cameras ORDER BY camera_id")
        ]

    @_synchronized
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

    @staticmethod
    def _artifact_media_class(item: dict[str, Any]) -> str:
        value = item.get("media_class")
        if value in {"raw_media", "event_clip", "metadata"}:
            return str(value)
        # Existing API records predate explicit lifecycle classes.  Video
        # artifacts are event clips by default; callers can opt into raw media
        # by setting media_class in the payload.
        return "event_clip" if str(item.get("media_type", "")).startswith("video/") else "raw_media"

    @_synchronized
    def list_retention_candidates(
        self, *, cutoffs: dict[str, datetime], now: datetime
    ) -> list[dict[str, Any]]:
        """Return expired, undeleted artifacts with their lifecycle class."""

        rows = self.connection.execute(
            "SELECT * FROM artifacts WHERE deleted = 0 ORDER BY created_at"
        ).fetchall()
        candidates: list[dict[str, Any]] = []
        for row in rows:
            item = json.loads(row["payload"])
            item.update(
                {
                    "artifact_id": row["artifact_id"],
                    "path": row["path"],
                    "created_at": row["created_at"],
                    "deleted": bool(row["deleted"]),
                }
            )
            media_class = self._artifact_media_class(item)
            if media_class not in cutoffs:
                continue
            try:
                created = datetime.fromisoformat(str(item["created_at"])).astimezone(UTC)
            except ValueError:
                continue
            if created < cutoffs[media_class]:
                item["media_class"] = media_class
                candidates.append(item)
        return candidates

    @_synchronized
    def finalize_artifact_deletion(
        self, artifact_id: str, *, media_class: str, deleted_at: datetime
    ) -> bool:
        """Atomically mark an artifact deleted and write its durable audit."""

        timestamp = deleted_at.astimezone(UTC).isoformat()
        with self.connection:
            row = self.connection.execute(
                "SELECT deleted FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"artifact not found: {artifact_id}")
            changed = not bool(row[0])
            self.connection.execute(
                "UPDATE artifacts SET deleted = 1, payload = json_set(payload, '$.deleted', 1) "
                "WHERE artifact_id = ?",
                (artifact_id,),
            )
            self.connection.execute(
                """INSERT OR IGNORE INTO retention_audits
                (artifact_id,media_class,action,status,error,created_at)
                VALUES (?,?,?,?,?,?)""",
                (artifact_id, media_class, "delete", "deleted", None, timestamp),
            )
        return changed

    @_synchronized
    def append_retention_audit(self, audit: dict[str, Any]) -> dict[str, Any]:
        item = dict(audit)
        item.setdefault("action", "delete")
        item.setdefault("status", "failed")
        item.setdefault("created_at", _now())
        timestamp = item["created_at"]
        if isinstance(timestamp, datetime):
            timestamp = timestamp.astimezone(UTC).isoformat()
        with self.connection:
            self.connection.execute(
                """INSERT INTO retention_audits
                (artifact_id,media_class,action,status,error,created_at)
                VALUES (?,?,?,?,?,?)""",
                (
                    item["artifact_id"],
                    item["media_class"],
                    item["action"],
                    item["status"],
                    item.get("error"),
                    timestamp,
                ),
            )
        item["created_at"] = timestamp
        return item

    @_synchronized
    def list_retention_audits(self, artifact_id: str | None = None) -> list[dict[str, Any]]:
        if artifact_id:
            rows = self.connection.execute(
                "SELECT * FROM retention_audits WHERE artifact_id=? ORDER BY created_at",
                (artifact_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM retention_audits ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    @_synchronized
    def delete_expired_metadata(self, *, cutoff: datetime, now: datetime) -> int:
        """Delete old decision metadata after media retention has run.

        The clip deletion audit is independent, so an event's decision remains
        queryable when only its clip clock has expired.  Once the metadata clock
        expires, reviews and decisions are removed together to preserve FK
        integrity.  ``now`` is accepted to keep repository adapters uniform.
        """

        del now
        threshold = cutoff.astimezone(UTC).isoformat()
        with self.connection:
            rows = self.connection.execute(
                "SELECT decision_id FROM decisions WHERE created_at < ?", (threshold,)
            ).fetchall()
            if not rows:
                return 0
            decision_ids = [str(row[0]) for row in rows]
            placeholders = ",".join("?" for _ in decision_ids)
            # Evaluation results and audio receipts can each contain a copy of
            # the decision.  Remove every linked row in this same transaction,
            # including attempt history, before deleting the parent decision.
            self.connection.execute(
                f"DELETE FROM reviews WHERE decision_id IN ({placeholders})", decision_ids
            )
            self.connection.execute(
                f"DELETE FROM audio_receipt_attempts WHERE receipt_id IN "
                f"(SELECT receipt_id FROM audio_receipts WHERE decision_id IN ({placeholders}))",
                decision_ids,
            )
            self.connection.execute(
                f"DELETE FROM audio_receipts WHERE decision_id IN ({placeholders})", decision_ids
            )
            self.connection.execute(
                f"DELETE FROM evaluations WHERE decision_id IN ({placeholders}) "
                f"OR EXISTS (SELECT 1 FROM json_tree(evaluations.result) "
                f"WHERE json_tree.key = 'decision_id' AND json_tree.value IN ({placeholders}))",
                [*decision_ids, *decision_ids],
            )
            self.connection.execute("DELETE FROM decisions WHERE created_at < ?", (threshold,))
        return len(decision_ids)

    @_synchronized
    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT payload FROM artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    @_synchronized
    def put_evaluation(self, evaluation: dict[str, Any]) -> dict[str, Any]:
        item = dict(evaluation)
        item.setdefault("created_at", _now())
        item.setdefault("updated_at", item["created_at"])
        result = item.get("result")
        decision_id = item.get("decision_id")
        if decision_id is None and isinstance(result, dict):
            nested = result.get("decision")
            if isinstance(nested, dict):
                decision_id = nested.get("decision_id")
        item["decision_id"] = str(decision_id) if decision_id is not None else None
        with self.connection:
            # The unique idempotency key is claimed in the same write as the
            # evaluation.  A concurrent retry therefore returns the original
            # record instead of raising or creating a second evaluation.
            self.connection.execute(
                """INSERT INTO evaluations
                (evaluation_id,decision_id,status,idempotency_key,result,payload,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(idempotency_key) DO NOTHING""",
                (
                    item["evaluation_id"],
                    item["decision_id"],
                    item["status"],
                    item.get("idempotency_key"),
                    _json(result) if result is not None else None,
                    _json(item),
                    item["created_at"],
                    item["updated_at"],
                ),
            )
            row = self.connection.execute(
                "SELECT evaluation_id FROM evaluations WHERE evaluation_id=? "
                "OR (idempotency_key IS NOT NULL AND idempotency_key=?)",
                (item["evaluation_id"], item.get("idempotency_key")),
            ).fetchone()
        if row is not None and str(row[0]) != str(item["evaluation_id"]):
            existing = self.get_evaluation(str(row[0]))
            if existing is not None:
                return cast(dict[str, Any], existing)
        return self.get_evaluation(str(item["evaluation_id"])) or item

    @_synchronized
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

    @_synchronized
    def get_evaluation_by_idempotency(self, key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT evaluation_id FROM evaluations WHERE idempotency_key=?", (key,)
        ).fetchone()
        return self.get_evaluation(row[0]) if row else None

    @_synchronized
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

    @_synchronized
    def list_mutes(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT payload FROM mutes ORDER BY created_at DESC"
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    @_synchronized
    def put_audio_receipt(self, receipt: dict[str, Any]) -> dict[str, Any]:
        """Persist the latest outcome and append a durable attempt history."""

        item = dict(receipt)
        item.setdefault("created_at", _now())
        with self.connection:
            existing = self.connection.execute(
                "SELECT COALESCE(MAX(attempt_no), 0) FROM audio_receipt_attempts "
                "WHERE receipt_id=?",
                (item["receipt_id"],),
            ).fetchone()
            attempt_no = int(existing[0]) + 1 if existing else 1
            current = self.connection.execute(
                "SELECT 1 FROM audio_receipts WHERE receipt_id=?", (item["receipt_id"],)
            ).fetchone()
            values = (
                item["decision_id"],
                item["camera_id"],
                item["zone_id"],
                item["outcome"],
                item["reason_code"],
                item.get("command_id"),
                _json(item.get("playback")) if item.get("playback") is not None else None,
                item["created_at"],
                _json(item),
            )
            if current is None:
                self.connection.execute(
                    """INSERT INTO audio_receipts
                    (receipt_id,decision_id,camera_id,zone_id,outcome,reason_code,command_id,
                     playback,created_at,payload) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (item["receipt_id"], *values),
                )
            else:
                self.connection.execute(
                    """UPDATE audio_receipts SET decision_id=?, camera_id=?, zone_id=?,
                    outcome=?, reason_code=?, command_id=?, playback=?, created_at=?, payload=?
                    WHERE receipt_id=?""",
                    (*values, item["receipt_id"]),
                )
            self.connection.execute(
                """INSERT INTO audio_receipt_attempts
                (receipt_id,attempt_no,decision_id,outcome,reason_code,command_id,playback,
                 created_at,payload) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    item["receipt_id"],
                    attempt_no,
                    item["decision_id"],
                    item["outcome"],
                    item["reason_code"],
                    item.get("command_id"),
                    _json(item.get("playback")) if item.get("playback") is not None else None,
                    item["created_at"],
                    _json(item),
                ),
            )
        return self.get_audio_receipt(str(item["receipt_id"])) or item

    @_synchronized
    def get_audio_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT payload FROM audio_receipts WHERE receipt_id=?", (receipt_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    @_synchronized
    def list_audio_receipts(
        self, *, camera_id: str | None = None, zone_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[str] = []
        if camera_id is not None:
            clauses.append("camera_id=?")
            values.append(camera_id)
        if zone_id is not None:
            clauses.append("zone_id=?")
            values.append(zone_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"SELECT payload FROM audio_receipts{where} ORDER BY created_at DESC", values
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    @_synchronized
    def false_announcement_count(self, camera_id: str) -> int:
        row = self.connection.execute(
            """SELECT COUNT(*) FROM reviews r JOIN decisions d ON d.decision_id=r.decision_id
            WHERE d.camera_id=? AND r.label='false_positive'""",
            (camera_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    @_synchronized
    def list_models(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT payload FROM model_releases ORDER BY created_at DESC"
        ).fetchall()
        return [json.loads(row[0]) for row in rows]
