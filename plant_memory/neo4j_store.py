"""Neo4j backend: the maintenance ontology as a property graph.

Graph model (every node also carries the :PM label so Plant Memory never touches other data):

  (:Asset)-[:LOCATED_IN]->(:ProcessArea)          (:Asset)-[:INSTANCE_OF]->(:AssetClass)
  (:MaintenanceEvent)-[:ON_ASSET]->(:Asset)        (:MaintenanceEvent)-[:PERFORMED_BY]->(:Technician)
  (:MaintenanceEvent)-[:INVOLVES_COMPONENT]->(:Component)
  (:MaintenanceEvent)-[:OBSERVED_SYMPTOM]->(:Symptom)
  (:MaintenanceEvent)-[:HAS_FAILURE_MODE]->(:FailureMode)
  (:MaintenanceEvent)-[:HAS_ROOT_CAUSE]->(:RootCause)
  (:MaintenanceEvent)-[:RESOLVED_BY]->(:Action)
  (:MaintenanceEvent|ConsumptionRecord)-[:CONSUMED {qty, cost, source_row}]->(:SparePart)-[:REALIZES]->(:Component)
  (:Asset)-[:HAS_ROOT_CAUSE {method:'technician', failure_mode}]->(:RootCause)     technician knowledge
  (:FailureMode)-[:TYPICALLY_AFFECTS]->(:Component)   (:RootCause)-[:IN_CATEGORY]->(:CauseCategory)
  (:DriftProposal)-[:SEEN_IN]->(:MaintenanceEvent)    (:GapQuestion)-[:ABOUT]->(:Asset)    (:AuditEntry)

Every fact relationship carries fact_id, confidence, method, status (proposed/verified/rejected),
verified_by, verified_at and its evidence (evidence_field, evidence_snippet).

Queries use plain openCypher (no APOC) so they also run on the embedded engine used in tests.
"""
from __future__ import annotations

import json
import warnings
from collections import defaultdict

from . import config
from .store import (DRIFT_COLUMNS, EVENT_COLUMNS, FACT_COLUMNS, GAP_COLUMNS, LINE_COLUMNS, PREDICATES, complete,
                    fact_kind, now)

REL = {"asset": "ON_ASSET", "component": "INVOLVES_COMPONENT", "symptom": "OBSERVED_SYMPTOM",
       "failure_mode": "HAS_FAILURE_MODE", "root_cause": "HAS_ROOT_CAUSE", "action": "RESOLVED_BY"}
KIND_OF_REL = {v: k for k, v in REL.items()}
TARGET = {"asset": ("Asset", "asset_id"), "component": ("Component", "id"), "symptom": ("Symptom", "id"),
          "failure_mode": ("FailureMode", "id"), "root_cause": ("RootCause", "id"), "action": ("Action", "id")}
TERM_LABEL = {"component": "Component", "failure_mode": "FailureMode", "root_cause": "RootCause",
              "action": "Action", "symptom": "Symptom"}
ALL_FACT_RELS = "|".join(REL.values())

CONSTRAINTS = [("MaintenanceEvent", "event_id"), ("Asset", "asset_id"), ("SparePart", "material"),
               ("Component", "id"), ("Symptom", "id"), ("FailureMode", "id"), ("RootCause", "id"), ("Action", "id"),
               ("ProcessArea", "code"), ("AssetClass", "id"), ("CauseCategory", "id"), ("Technician", "id"),
               ("DriftProposal", "proposal_id"), ("GapQuestion", "question_id")]


def _clean(d: dict) -> dict:
    """Neo4j properties cannot be null or nested maps."""
    out = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, dict):
            v = json.dumps(v, default=str)
        out[k] = v
    return out


class Neo4jExecutor:
    """Runs Cypher on a Neo4j server through the official driver (one shared driver per process)."""
    _driver = None

    def __init__(self, uri=None, user=None, password=None, database=None):
        from neo4j import GraphDatabase
        self.database = database or config.NEO4J_DATABASE
        if Neo4jExecutor._driver is None:
            if not (password or config.NEO4J_PASSWORD):
                raise SystemExit("NEO4J_PASSWORD is not set. Add NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD to .env, "
                                 "or run with --store sqlite.")
            driver = GraphDatabase.driver(uri or config.NEO4J_URI,
                                          auth=(user or config.NEO4J_USER, password or config.NEO4J_PASSWORD))
            try:
                driver.verify_connectivity()
            except Exception as exc:
                driver.close()
                hint = ("Check NEO4J_USER / NEO4J_PASSWORD." if "auth" in str(exc).lower() or "Unauthorized" in type(exc).__name__
                        else "Is the instance started in Neo4j Desktop, and is NEO4J_URI right?")
                raise SystemExit(f"Could not connect to Neo4j at {uri or config.NEO4J_URI}: "
                                 f"{type(exc).__name__}: {exc}\n{hint} (Or run with --store sqlite.)") from None
            Neo4jExecutor._driver = driver
        self.driver = Neo4jExecutor._driver
        self.target = f"{uri or config.NEO4J_URI} (database '{self.database}')"
        self.supports_ddl = True

    def run(self, query: str, params: dict | None = None) -> list[dict]:
        res = self.driver.execute_query(query, params or {}, database_=self.database)
        return [r.data() for r in res.records]


class Neo4jRepo:
    backend = "neo4j"
    _schema_ready: set = set()

    def __init__(self, executor=None):
        try:
            self.x = executor or Neo4jExecutor()
        except SystemExit:
            raise
        except Exception as exc:  # driver import or connection problems
            raise SystemExit(f"Could not connect to Neo4j at {config.NEO4J_URI}: {exc}. "
                             "Is the database started in Neo4j Desktop? Or run with --store sqlite.") from exc
        self._ensure_schema()

    def describe(self) -> str:
        return f"neo4j:{getattr(self.x, 'target', 'embedded')}"

    def _ensure_schema(self) -> None:
        key = self.describe()
        if key in Neo4jRepo._schema_ready or not getattr(self.x, "supports_ddl", False):
            return
        stmts = [f"CREATE CONSTRAINT pm_{lbl.lower()}_{prop} IF NOT EXISTS FOR (n:{lbl}) REQUIRE n.{prop} IS UNIQUE"
                 for lbl, prop in CONSTRAINTS]
        stmts += [f"CREATE INDEX pm_rel_{rel.lower()} IF NOT EXISTS FOR ()-[r:{rel}]-() ON (r.fact_id)" for rel in REL.values()]
        stmts += ["CREATE INDEX pm_consumed_event IF NOT EXISTS FOR ()-[r:CONSUMED]-() ON (r.event_id)"]
        for s in stmts:
            try:
                self.x.run(s)
            except Exception as exc:  # e.g. existing non-PM data violates a constraint
                warnings.warn(f"Neo4j schema statement skipped ({exc.__class__.__name__}): {s}. "
                              "Use a dedicated database for Plant Memory to avoid clashes.")
        Neo4jRepo._schema_ready.add(key)

    def commit(self) -> None:  # each query auto-commits
        pass

    def close(self) -> None:  # the driver is shared and closed at interpreter exit
        pass

    # ---- writes -------------------------------------------------------------
    def reset(self) -> None:
        self.x.run("MATCH (n:PM) WHERE NOT n:AuditEntry DETACH DELETE n")

    def write_reference(self, ref: dict) -> None:
        self.x.run("UNWIND $rows AS a MERGE (p:PM:ProcessArea {code: a.code}) SET p.label = a.label", {"rows": ref["areas"]})
        self.x.run("""UNWIND $rows AS a
                      MERGE (x:PM:Asset {asset_id: a.asset_id})
                      SET x.name = a.name, x.area = a.area, x.asset_class = a.asset_class, x.aliases = a.aliases
                      MERGE (c:PM:AssetClass {id: a.asset_class})
                      MERGE (x)-[:INSTANCE_OF]->(c)
                      WITH x, a
                      MATCH (p:PM:ProcessArea {code: a.area})
                      MERGE (x)-[:LOCATED_IN]->(p)""", {"rows": ref["assets"]})
        for kind, terms in ref["terms"].items():
            self.x.run(f"UNWIND $rows AS t MERGE (n:PM:{TERM_LABEL[kind]} {{id: t.id}}) SET n += t",
                       {"rows": [_clean(t) for t in terms]})
        self.x.run("""UNWIND $rows AS t
                      MATCH (r:PM:RootCause {id: t.id})
                      MERGE (c:PM:CauseCategory {id: t.category})
                      MERGE (r)-[:IN_CATEGORY]->(c)""",
                   {"rows": [t for t in ref["terms"].get("root_cause", []) if t.get("category")]})
        self.x.run("""UNWIND $rows AS p
                      MATCH (f:PM:FailureMode {id: p.fm})
                      MATCH (c:PM:Component {id: p.component})
                      MERGE (f)-[:TYPICALLY_AFFECTS]->(c)""", {"rows": ref["fm_components"]})
        self.x.run("""UNWIND $rows AS m
                      MERGE (s:PM:SparePart {material: m.material})
                      SET s.description = m.description, s.unit_cost = m.unit_cost, s.uom = m.uom, s.component = m.component
                      WITH s, m
                      MATCH (c:PM:Component {id: m.component})
                      MERGE (s)-[:REALIZES]->(c)""", {"rows": ref["materials"]})

    def add_events(self, events: list[dict], lines: list[dict]) -> None:
        self.x.run("UNWIND $rows AS e MERGE (n:PM:MaintenanceEvent {event_id: e.event_id}) SET n += e",
                   {"rows": [_clean(e) for e in events]})
        self.x.run("""UNWIND $rows AS e
                      MATCH (n:PM:MaintenanceEvent {event_id: e.event_id})
                      MERGE (t:PM:Technician {id: e.technician})
                      MERGE (n)-[:PERFORMED_BY]->(t)""",
                   {"rows": [{"event_id": e["event_id"], "technician": e["technician"]} for e in events if e.get("technician")]})
        self.x.run("""UNWIND $rows AS l
                      MATCH (n:PM:MaintenanceEvent {event_id: l.event_id})
                      MERGE (s:PM:SparePart {material: l.material})
                      ON CREATE SET s.description = l.material_description
                      CREATE (n)-[c:CONSUMED]->(s)
                      SET c = l""", {"rows": [_clean(l) for l in lines]})

    def add_consumption(self, lines: list[dict]) -> None:
        self.x.run("""UNWIND $rows AS l
                      CREATE (r:PM:ConsumptionRecord {source_row: l.source_row})
                      SET r.posting_date = l.posting_date, r.cost_center = l.cost_center
                      MERGE (s:PM:SparePart {material: l.material})
                      ON CREATE SET s.description = l.material_description
                      CREATE (r)-[c:CONSUMED]->(s)
                      SET c = l""", {"rows": [_clean(l) for l in lines]})

    def add_facts(self, facts: list[dict]) -> None:
        by_kind = defaultdict(list)
        technician = []
        for f in facts:
            (technician if not f.get("event_id") else by_kind[f["object_kind"]]).append(_clean(f))
        for kind, rows in by_kind.items():
            label, key = TARGET[kind]
            self.x.run(f"""UNWIND $rows AS f
                           MATCH (e:PM:MaintenanceEvent {{event_id: f.event_id}})
                           MERGE (t:PM:{label} {{{key}: f.object_id}})
                           CREATE (e)-[r:{REL[kind]}]->(t)
                           SET r = f""", {"rows": rows})
        for f in technician:
            self.upsert_technician_fact(f)

    def upsert_technician_fact(self, fact: dict) -> None:
        f = _clean(fact)
        self.x.run("""MATCH (a:PM:Asset {asset_id: $asset_id})
                      MERGE (t:PM:RootCause {id: $object_id})
                      MERGE (a)-[r:HAS_ROOT_CAUSE {fact_id: $fact_id}]->(t)
                      SET r = $props""",
                   {"asset_id": f["asset_id"], "object_id": f["object_id"], "fact_id": f["fact_id"], "props": f})

    def set_fact_status(self, fact_ids: list[str], status: str, by: str | None, at: str | None, note: str | None) -> int:
        groups = defaultdict(list)
        for fid in fact_ids:
            groups[fact_kind(fid)].append(fid)
        n = 0
        for kind, ids in groups.items():
            res = self.x.run(f"""UNWIND $ids AS id
                                 MATCH ()-[r:{REL[kind]}]->()
                                 WHERE r.fact_id = id
                                 SET r.status = $status, r.verified_by = $by, r.verified_at = $at,
                                     r.note = coalesce($note, r.note)
                                 RETURN count(r) AS n""",
                             {"ids": ids, "status": status, "by": by, "at": at, "note": note})
            n += res[0]["n"] if res else 0
        return n

    def set_fact_value(self, fact_id: str, object_id: str) -> None:
        kind = fact_kind(fact_id)
        label, key = TARGET[kind]
        rel = REL[kind]
        self.x.run(f"""MATCH (s)-[r:{rel}]->(old)
                       WHERE r.fact_id = $fid
                       MERGE (t:PM:{label} {{{key}: $new}})
                       CREATE (s)-[r2:{rel}]->(t)
                       SET r2 = properties(r)
                       SET r2.object_id = $new, r2.method = r.method + '+edited'
                       DELETE r""", {"fid": fact_id, "new": object_id})

    def restore_decisions(self, kept: list[dict]) -> int:
        n = 0
        groups = defaultdict(list)
        for k in kept:
            if k["method"] == "technician":
                self.upsert_technician_fact(k)
                n += 1
            else:
                groups[k["object_kind"]].append(_clean({c: k.get(c) for c in
                                                        ["fact_id", "object_id", "status", "verified_by", "verified_at", "note"]}))
        for kind, rows in groups.items():
            res = self.x.run(f"""UNWIND $rows AS k
                                 MATCH ()-[r:{REL[kind]}]->()
                                 WHERE r.fact_id = k.fact_id AND r.object_id = k.object_id
                                 SET r.status = k.status, r.verified_by = k.verified_by, r.verified_at = k.verified_at,
                                     r.note = coalesce(k.note, r.note)
                                 RETURN count(r) AS n""", {"rows": rows})
            n += res[0]["n"] if res else 0
        return n

    def replace_drift(self, rows: list[dict]) -> None:
        self.x.run("MATCH (d:PM:DriftProposal) DETACH DELETE d")
        for r in rows:
            self.upsert_drift(r)

    def upsert_drift(self, row: dict) -> None:
        r = _clean(complete(row, DRIFT_COLUMNS))
        r["event_ids"] = list(row.get("event_ids") or [])
        self.x.run("MERGE (d:PM:DriftProposal {proposal_id: $pid}) SET d += $row", {"pid": r["proposal_id"], "row": r})
        self.x.run("""MATCH (d:PM:DriftProposal {proposal_id: $pid})
                      UNWIND $events AS eid
                      MATCH (e:PM:MaintenanceEvent {event_id: eid})
                      MERGE (d)-[:SEEN_IN]->(e)""", {"pid": r["proposal_id"], "events": r["event_ids"]})

    def update_drift(self, proposal_id: str, fields: dict) -> None:
        self._update("DriftProposal", "proposal_id", proposal_id, fields)

    def replace_gaps(self, rows: list[dict]) -> None:
        self.x.run("MATCH (g:PM:GapQuestion) DETACH DELETE g")
        clean = []
        for row in rows:
            r = _clean(complete(row, GAP_COLUMNS))
            r["event_ids"] = list(row.get("event_ids") or [])
            clean.append(r)
        self.x.run("""UNWIND $rows AS q
                      CREATE (g:PM:GapQuestion)
                      SET g = q
                      WITH g, q
                      MATCH (a:PM:Asset {asset_id: q.asset_id})
                      MERGE (g)-[:ABOUT]->(a)""", {"rows": clean})

    def update_gap(self, question_id: str, fields: dict) -> None:
        self._update("GapQuestion", "question_id", question_id, fields)

    def _update(self, label: str, key: str, value: str, fields: dict) -> None:
        sets = {k: v for k, v in fields.items() if v is not None}
        removes = [k for k, v in fields.items() if v is None]
        q = f"MATCH (n:PM:{label} {{{key}: $value}}) SET n += $sets"
        for k in removes:
            q += f" REMOVE n.{k}"
        self.x.run(q, {"value": value, "sets": sets})

    def audit(self, actor: str, action: str, detail) -> None:
        self.x.run("CREATE (a:PM:AuditEntry {at: $at, actor: $actor, action: $action, detail: $detail})",
                   {"at": now(), "actor": actor, "action": action,
                    "detail": detail if isinstance(detail, str) else json.dumps(detail, default=str)})

    # ---- reads --------------------------------------------------------------
    def facts(self, *, fact_id=None, event_ids=None, asset_ids=None, kind=None, status=None, exclude_status=None,
              method=None, min_confidence=None) -> list[dict]:
        if event_ids is not None and not event_ids:
            return []
        rels = REL[fact_kind(fact_id)] if fact_id else REL[kind] if kind else ALL_FACT_RELS
        where, p = ["r.fact_id IS NOT NULL"], {}
        for cond, name, val in (("r.fact_id = $fact_id", "fact_id", fact_id),
                                ("r.event_id IN $event_ids", "event_ids", list(event_ids) if event_ids is not None else None),
                                ("r.asset_id IN $asset_ids", "asset_ids", list(asset_ids) if asset_ids is not None else None),
                                ("r.status = $status", "status", status),
                                ("r.status <> $exclude_status", "exclude_status", exclude_status),
                                ("r.method = $method", "method", method),
                                ("r.confidence >= $min_confidence", "min_confidence", min_confidence)):
            if val is not None:
                where.append(cond)
                p[name] = val
        rows = self.x.run(f"MATCH ()-[r:{rels}]->() WHERE {' AND '.join(where)} RETURN properties(r) AS r, type(r) AS rel", p)
        out = []
        for row in rows:
            f = dict(row["r"])
            f.setdefault("object_kind", KIND_OF_REL[row["rel"]])
            if not f.get("predicate"):
                f["predicate"] = PREDICATES[f["object_kind"]]
            out.append(complete(f, FACT_COLUMNS))
        return out

    def events(self, *, event_ids=None, asset_id=None, start=None, end=None) -> list[dict]:
        if event_ids is not None and not event_ids:
            return []
        where, p = ["true"], {}
        if event_ids is not None:
            where.append("e.event_id IN $event_ids")
            p["event_ids"] = list(event_ids)
        if asset_id:
            where.append("e.asset_id = $asset_id")
            p["asset_id"] = asset_id
        if start:
            where.append("e.posting_date >= $start")
            p["start"] = start
        if end:
            where.append("e.posting_date <= $end")
            p["end"] = end
        rows = self.x.run(f"MATCH (e:PM:MaintenanceEvent) WHERE {' AND '.join(where)} RETURN properties(e) AS e", p)
        out = [complete(dict(r["e"]), EVENT_COLUMNS) for r in rows]
        return sorted(out, key=lambda e: (e["posting_date"] or "", e["event_id"]))

    def lines(self, *, event_ids=None) -> list[dict]:
        if event_ids is not None and not event_ids:
            return []
        q = "MATCH (x:PM)-[c:CONSUMED]->(s:PM:SparePart)"
        p = {}
        if event_ids is not None:
            q += " WHERE c.event_id IN $event_ids"
            p["event_ids"] = list(event_ids)
        q += " RETURN properties(c) AS c, x.asset_id AS asset_id, x.downtime_hrs AS downtime_hrs, x.breakdown AS breakdown"
        out = []
        for r in self.x.run(q, p):
            line = complete(dict(r["c"]), LINE_COLUMNS)
            line.update(asset_id=r["asset_id"], downtime_hrs=r["downtime_hrs"], breakdown=r["breakdown"])
            out.append(line)
        return sorted(out, key=lambda l: l["source_row"] or 0)

    def drift(self, *, status=None, proposal_id=None) -> list[dict]:
        rows = self._nodes("DriftProposal", {"status": status, "proposal_id": proposal_id}, DRIFT_COLUMNS)
        return sorted(rows, key=lambda d: (-(d["count"] or 0), d["proposal_id"]))

    def gaps(self, *, status=None, asset_id=None, question_id=None) -> list[dict]:
        rows = self._nodes("GapQuestion", {"status": status, "asset_id": asset_id, "question_id": question_id}, GAP_COLUMNS)
        return sorted(rows, key=lambda g: g["question_id"])

    def _nodes(self, label: str, filters: dict, columns: list[str]) -> list[dict]:
        active = {k: v for k, v in filters.items() if v}
        where = " AND ".join(f"n.{k} = ${k}" for k in active) or "true"
        out = []
        for r in self.x.run(f"MATCH (n:PM:{label}) WHERE {where} RETURN properties(n) AS n", active):
            d = complete(dict(r["n"]), columns)
            d["event_ids"] = list(d.get("event_ids") or [])
            out.append(d)
        return out

    def audit_log(self, limit: int = 50) -> list[dict]:
        rows = [dict(r["a"]) for r in self.x.run("MATCH (a:PM:AuditEntry) RETURN properties(a) AS a")]
        return sorted(rows, key=lambda a: a.get("at", ""), reverse=True)[:limit]

    # ---- diagnostics --------------------------------------------------------
    def check(self) -> dict:
        info = {"target": self.describe()}
        try:
            comp = self.x.run("CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition")
            info["server"] = comp[0] if comp else None
        except Exception as exc:
            info["server"] = f"unknown ({exc.__class__.__name__})"
        counts = self.x.run("MATCH (n:PM) UNWIND labels(n) AS l WITH l WHERE l <> 'PM' RETURN l AS label, count(*) AS n")
        info["plant_memory_nodes"] = {r["label"]: r["n"] for r in counts}
        other = self.x.run("MATCH (n) WHERE NOT n:PM RETURN count(n) AS n")
        info["other_nodes_in_database"] = other[0]["n"] if other else 0
        return info
