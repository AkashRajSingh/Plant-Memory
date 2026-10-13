"""Storage layer.

Business logic talks to a Repo; two backends implement it:
- Neo4jRepo (neo4j_store.py): the default when NEO4J_PASSWORD is set. A property graph:
  (MaintenanceEvent)-[HAS_FAILURE_MODE {fact_id, confidence, status, ...}]->(FailureMode), etc.
- SqliteRepo (this file): offline fallback and the reference implementation for tests.

Both return plain dicts with the same keys, so reasoning code is backend-agnostic.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import config

PREDICATES = {
    "asset": "onAsset",
    "component": "involvesComponent",
    "failure_mode": "hasFailureMode",
    "root_cause": "hasRootCause",
    "action": "resolvedBy",
    "symptom": "observedSymptom",
}
EVENT_COLUMNS = ["event_id", "posting_date", "last_posting_date", "description", "normalized_text", "work_type",
                 "planning_department", "functional_location", "asset_id", "asset_confidence", "asset_method",
                 "symptom_text", "root_cause_text", "cause_category_text", "action_text", "downtime_hrs", "breakdown",
                 "remarks", "technician", "total_cost", "line_count", "work_type_flag"]
LINE_COLUMNS = ["line_id", "event_id", "source_row", "source_file", "posting_date", "material", "material_description", "component",
                "quantity", "uom", "cost", "movement_type", "cost_center"]
FACT_COLUMNS = ["fact_id", "event_id", "asset_id", "predicate", "object_kind", "object_id", "confidence", "method",
                "status", "verified_by", "verified_at", "note", "created_at", "failure_mode",
                "evidence_field", "evidence_snippet"]
DRIFT_COLUMNS = ["proposal_id", "kind", "phrase", "event_ids", "count", "status", "decided_by", "decided_at", "mapped_to"]
GAP_COLUMNS = ["question_id", "asset_id", "failure_mode", "question", "event_ids", "status", "answer",
               "answered_by", "answered_at"]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def fact_kind(fact_id: str) -> str:
    """F-<order>-<kind> for extracted facts, T-... for technician statements (always root causes)."""
    if fact_id.startswith("T-"):
        return "root_cause"
    return fact_id.split("-", 2)[2]


def complete(d: dict, columns: list[str]) -> dict:
    return {c: d.get(c) for c in columns} | {k: v for k, v in d.items() if k not in columns}


SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (asset_id TEXT PRIMARY KEY, name TEXT, area TEXT, asset_class TEXT, aliases TEXT);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY, posting_date TEXT, last_posting_date TEXT, description TEXT, normalized_text TEXT,
    work_type TEXT, planning_department TEXT, functional_location TEXT, asset_id TEXT, asset_confidence REAL,
    asset_method TEXT, symptom_text TEXT, root_cause_text TEXT, cause_category_text TEXT, action_text TEXT,
    downtime_hrs REAL, breakdown TEXT, remarks TEXT, technician TEXT, total_cost REAL, line_count INTEGER,
    work_type_flag TEXT);
CREATE TABLE IF NOT EXISTS lines (
    line_id INTEGER PRIMARY KEY, event_id TEXT, source_row INTEGER, source_file TEXT, posting_date TEXT, material TEXT,
    material_description TEXT, component TEXT, quantity REAL, uom TEXT, cost REAL, movement_type TEXT, cost_center TEXT);
CREATE TABLE IF NOT EXISTS facts (
    fact_id TEXT PRIMARY KEY, event_id TEXT, asset_id TEXT, predicate TEXT, object_kind TEXT, object_id TEXT,
    confidence REAL, method TEXT, status TEXT DEFAULT 'proposed', verified_by TEXT, verified_at TEXT, note TEXT,
    created_at TEXT, failure_mode TEXT, evidence_field TEXT, evidence_snippet TEXT);
CREATE TABLE IF NOT EXISTS drift_proposals (
    proposal_id TEXT PRIMARY KEY, kind TEXT, phrase TEXT, event_ids TEXT, count INTEGER, status TEXT,
    decided_by TEXT, decided_at TEXT, mapped_to TEXT);
CREATE TABLE IF NOT EXISTS gap_questions (
    question_id TEXT PRIMARY KEY, asset_id TEXT, failure_mode TEXT, question TEXT, event_ids TEXT, status TEXT,
    answer TEXT, answered_by TEXT, answered_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, actor TEXT, action TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_facts_event ON facts(event_id);
CREATE INDEX IF NOT EXISTS ix_facts_kind ON facts(object_kind, status);
CREATE INDEX IF NOT EXISTS ix_lines_event ON lines(event_id);
CREATE INDEX IF NOT EXISTS ix_events_asset ON events(asset_id);
"""


class SqliteRepo:
    backend = "sqlite"

    def __init__(self, path: Path | None = None):
        self.path = Path(path or config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(lines)")}
        if "source_file" not in cols:  # databases created before multi-file ingest
            self.con.execute("ALTER TABLE lines ADD COLUMN source_file TEXT")

    def describe(self) -> str:
        return f"sqlite:{self.path}"

    def _rows(self, sql: str, params=()) -> list[dict]:
        return [dict(r) for r in self.con.execute(sql, params).fetchall()]

    def commit(self) -> None:
        self.con.commit()

    def close(self) -> None:
        self.con.commit()
        self.con.close()

    # ---- writes -------------------------------------------------------------
    def reset(self) -> None:
        for t in ["assets", "events", "lines", "facts", "drift_proposals", "gap_questions"]:
            self.con.execute(f"DELETE FROM {t}")

    def write_reference(self, ref: dict) -> None:
        for a in ref["assets"]:
            self.con.execute("INSERT OR REPLACE INTO assets VALUES (?,?,?,?,?)",
                             (a["asset_id"], a["name"], a["area"], a["asset_class"], json.dumps(a["aliases"])))

    def add_events(self, events: list[dict], lines: list[dict]) -> None:
        self.con.executemany(f"INSERT INTO events({','.join(EVENT_COLUMNS)}) VALUES ({','.join('?' * len(EVENT_COLUMNS))})",
                             [[e.get(c) for c in EVENT_COLUMNS] for e in events])
        self._add_lines(lines)

    def add_consumption(self, lines: list[dict]) -> None:
        self._add_lines(lines)

    def _add_lines(self, lines):
        self.con.executemany(f"INSERT INTO lines({','.join(LINE_COLUMNS)}) VALUES ({','.join('?' * len(LINE_COLUMNS))})",
                             [[l.get(c) for c in LINE_COLUMNS] for l in lines])

    def add_facts(self, facts: list[dict]) -> None:
        cols = [c for c in FACT_COLUMNS]
        self.con.executemany(f"INSERT OR REPLACE INTO facts({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                             [[f.get(c) for c in cols] for f in facts])

    def upsert_technician_fact(self, fact: dict) -> None:
        self.add_facts([fact])

    def set_fact_status(self, fact_ids: list[str], status: str, by: str | None, at: str | None, note: str | None) -> int:
        n = 0
        for fid in fact_ids:
            n += self.con.execute("UPDATE facts SET status=?, verified_by=?, verified_at=?, note=COALESCE(?, note) WHERE fact_id=?",
                                  (status, by, at, note, fid)).rowcount
        return n

    def set_fact_value(self, fact_id: str, object_id: str) -> None:
        self.con.execute("UPDATE facts SET object_id=?, method=method||'+edited' WHERE fact_id=?", (object_id, fact_id))

    def restore_decisions(self, kept: list[dict]) -> int:
        n = 0
        for k in kept:
            if k["method"] == "technician":
                self.upsert_technician_fact(k)
                n += 1
                continue
            n += self.con.execute("""UPDATE facts SET status=?, verified_by=?, verified_at=?, note=COALESCE(?, note)
                                     WHERE fact_id=? AND object_id=?""",
                                  (k["status"], k["verified_by"], k["verified_at"], k["note"], k["fact_id"], k["object_id"])).rowcount
        return n

    def replace_drift(self, rows: list[dict]) -> None:
        self.con.execute("DELETE FROM drift_proposals")
        for r in rows:
            self.upsert_drift(r)

    def upsert_drift(self, row: dict) -> None:
        r = complete(row, DRIFT_COLUMNS)
        r["event_ids"] = json.dumps(r["event_ids"] or [])
        self.con.execute(f"INSERT OR REPLACE INTO drift_proposals VALUES ({','.join('?' * len(DRIFT_COLUMNS))})",
                         [r[c] for c in DRIFT_COLUMNS])

    def update_drift(self, proposal_id: str, fields: dict) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        self.con.execute(f"UPDATE drift_proposals SET {sets} WHERE proposal_id=?", [*fields.values(), proposal_id])

    def replace_gaps(self, rows: list[dict]) -> None:
        self.con.execute("DELETE FROM gap_questions")
        for row in rows:
            r = complete(row, GAP_COLUMNS)
            r["event_ids"] = json.dumps(r["event_ids"] or [])
            self.con.execute(f"INSERT INTO gap_questions VALUES ({','.join('?' * len(GAP_COLUMNS))})", [r[c] for c in GAP_COLUMNS])

    def update_gap(self, question_id: str, fields: dict) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        self.con.execute(f"UPDATE gap_questions SET {sets} WHERE question_id=?", [*fields.values(), question_id])

    def audit(self, actor: str, action: str, detail) -> None:
        self.con.execute("INSERT INTO audit_log(at, actor, action, detail) VALUES (?,?,?,?)",
                         (now(), actor, action, detail if isinstance(detail, str) else json.dumps(detail, default=str)))

    # ---- reads --------------------------------------------------------------
    def facts(self, *, fact_id=None, event_ids=None, asset_ids=None, kind=None, status=None, exclude_status=None,
              method=None, min_confidence=None) -> list[dict]:
        sql, p = "SELECT * FROM facts WHERE 1=1", []
        if fact_id:
            sql += " AND fact_id=?"
            p.append(fact_id)
        if event_ids is not None:
            if not event_ids:
                return []
            sql += f" AND event_id IN ({','.join('?' * len(event_ids))})"
            p += list(event_ids)
        if asset_ids is not None:
            sql += f" AND asset_id IN ({','.join('?' * len(asset_ids))})"
            p += list(asset_ids)
        for col, val in (("object_kind", kind), ("status", status), ("method", method)):
            if val:
                sql += f" AND {col}=?"
                p.append(val)
        if exclude_status:
            sql += " AND status != ?"
            p.append(exclude_status)
        if min_confidence is not None:
            sql += " AND confidence >= ?"
            p.append(min_confidence)
        return [complete(r, FACT_COLUMNS) for r in self._rows(sql, p)]

    def events(self, *, event_ids=None, asset_id=None, start=None, end=None) -> list[dict]:
        sql, p = "SELECT * FROM events WHERE 1=1", []
        if event_ids is not None:
            if not event_ids:
                return []
            sql += f" AND event_id IN ({','.join('?' * len(event_ids))})"
            p += list(event_ids)
        if asset_id:
            sql += " AND asset_id=?"
            p.append(asset_id)
        if start:
            sql += " AND posting_date >= ?"
            p.append(start)
        if end:
            sql += " AND posting_date <= ?"
            p.append(end)
        return self._rows(sql + " ORDER BY posting_date, event_id", p)

    def lines(self, *, event_ids=None) -> list[dict]:
        sql = """SELECT l.*, e.asset_id, e.downtime_hrs, e.breakdown FROM lines l
                 LEFT JOIN events e ON e.event_id = l.event_id"""
        p: list = []
        if event_ids is not None:
            if not event_ids:
                return []
            sql += f" WHERE l.event_id IN ({','.join('?' * len(event_ids))})"
            p = list(event_ids)
        return self._rows(sql + " ORDER BY l.source_row", p)

    def drift(self, *, status=None, proposal_id=None) -> list[dict]:
        sql, p = "SELECT * FROM drift_proposals WHERE 1=1", []
        if status:
            sql += " AND status=?"
            p.append(status)
        if proposal_id:
            sql += " AND proposal_id=?"
            p.append(proposal_id)
        out = self._rows(sql + " ORDER BY count DESC, proposal_id", p)
        for d in out:
            d["event_ids"] = json.loads(d["event_ids"] or "[]")
        return out

    def gaps(self, *, status=None, asset_id=None, question_id=None) -> list[dict]:
        sql, p = "SELECT * FROM gap_questions WHERE 1=1", []
        for col, val in (("status", status), ("asset_id", asset_id), ("question_id", question_id)):
            if val:
                sql += f" AND {col}=?"
                p.append(val)
        out = self._rows(sql + " ORDER BY question_id", p)
        for g in out:
            g["event_ids"] = json.loads(g["event_ids"] or "[]")
        return out

    def audit_log(self, limit: int = 50) -> list[dict]:
        return self._rows("SELECT at, actor, action, detail FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))


# ---- backend selection ---------------------------------------------------------
def backend_name(override: str | None = None) -> str:
    choice = (override or config.STORE).lower()
    if choice == "auto":
        return "neo4j" if config.NEO4J_PASSWORD else "sqlite"
    if choice not in {"neo4j", "sqlite"}:
        raise SystemExit(f"PLANT_MEMORY_STORE must be neo4j, sqlite or auto (got {choice})")
    return choice


def open_repo(backend: str | None = None, db_path: Path | None = None):
    name = "sqlite" if db_path else backend_name(backend)
    if name == "neo4j":
        from .neo4j_store import Neo4jRepo
        return Neo4jRepo()
    return SqliteRepo(db_path)


@contextmanager
def session(backend: str | None = None, db_path: Path | None = None):
    repo = open_repo(backend, db_path)
    try:
        yield repo
        repo.commit()
    finally:
        repo.close()
