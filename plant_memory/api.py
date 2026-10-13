"""Plant Memory web API (FastAPI). Serves the React app from web/dist.

The API is a thin layer over the same core the CLI and MCP server use (pipeline, verify, reasoning,
workspace), so all three interfaces always see the same knowledge graph.

Run: python -m plant_memory serve   (http://127.0.0.1:8765)
"""
import threading
import traceback
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, catalog, config, pipeline, reasoning, taxonomy, verify, workspace
from .store import open_repo, session
from .taxonomy import VOCAB, label

WEB_DIST = config.PROJECT_ROOT / "web" / "dist"

app = FastAPI(title="Plant Memory", version=__version__,
              description="Maintenance knowledge graph: upload work orders, review facts, explore the graph.")


# ---------------------------------------------------------------------------------------- errors
@app.exception_handler(ValueError)
async def _value_error(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.middleware("http")
async def _config_errors(request, call_next):
    """Configuration problems (e.g. Neo4j not running) raise SystemExit in the core: report them as 503."""
    try:
        return await call_next(request)
    except SystemExit as exc:
        return JSONResponse(status_code=503, content={"detail": str(exc)})


# ---------------------------------------------------------------------------------------- jobs
class Jobs:
    """One background build at a time; progress is polled by the UI."""

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.current: str | None = None

    def running(self) -> dict | None:
        j = self.jobs.get(self.current or "")
        return j if j and j["state"] in ("queued", "running") else None

    def start(self, kind: str, fn, **kwargs) -> dict:
        with self.lock:
            if self.running():
                raise HTTPException(409, "A build is already running. Wait for it to finish.")
            jid = uuid.uuid4().hex[:8]
            job = {"id": jid, "kind": kind, "state": "queued", "stage": "queued", "done": 0, "total": 0,
                   "log": [], "result": None, "error": None, "started_at": datetime.now().isoformat(timespec="seconds"),
                   "finished_at": None}
            self.jobs[jid] = job
            self.current = jid

        def progress(stage, done, total):
            job.update(stage=stage, done=done, total=total)

        def log(msg):
            job["log"] = (job["log"] + [str(msg)])[-200:]

        def run():
            job["state"] = "running"
            try:
                job["result"] = fn(progress=progress, log=log, **kwargs)
                job["state"] = "done"
            except BaseException as exc:  # SystemExit from config problems included
                job.update(state="failed", error=str(exc) or type(exc).__name__)
                log(traceback.format_exc(limit=3))
            finally:
                job["finished_at"] = datetime.now().isoformat(timespec="seconds")

        threading.Thread(target=run, daemon=True).start()
        return job


jobs = Jobs()


def _guard_writes():
    if jobs.running():
        raise HTTPException(409, "The knowledge graph is being rebuilt. Try again when the build finishes.")


def _start_build(use_llm: str = "auto") -> dict:
    def run(progress, log, use_llm):
        return pipeline.build(use_llm=use_llm, log=log, progress=progress)
    return jobs.start("build", run, use_llm=use_llm)


@app.get("/api/jobs/current")
def current_job():
    j = jobs.jobs.get(jobs.current or "")
    return {"job": j}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs.jobs:
        raise HTTPException(404, "No such job")
    return jobs.jobs[job_id]


@app.post("/api/rebuild")
def rebuild(body: dict = Body(default={})):
    return _start_build(body.get("llm", "auto"))


# ---------------------------------------------------------------------------------------- status / meta
@app.get("/api/status")
def status():
    with session() as repo:
        facts = repo.facts()
        n_events = len(repo.events())
        store = repo.describe()
    by_status = Counter(f["status"] for f in facts if f["object_kind"] != "asset")
    target = workspace.llm_target()
    return {"version": __version__, "store": store, "work_orders": n_events, "facts": sum(by_status.values()),
            "facts_by_status": dict(by_status), "assets": len(catalog.ASSETS), "sources": len(workspace.sources()),
            "llm": {"mode": target["mode"], "label": target["label"], "enabled": target["enabled"]},
            "job": jobs.jobs.get(jobs.current or "")}


@app.get("/api/meta")
def meta():
    """Everything the filter dropdowns need."""
    return {
        "areas": [{"code": k, "label": v["label"]} for k, v in sorted(catalog.PROCESS_AREAS.items())],
        "assets": [{"asset_id": k, "name": v["name"], "area": v["area"]} for k, v in sorted(catalog.ASSETS.items())],
        "vocab": {kind: [{"id": k, "label": v["label"]} for k, v in sorted(terms.items(), key=lambda kv: kv[1]["label"])]
                  for kind, terms in VOCAB.items()},
    }


# ---------------------------------------------------------------------------------------- settings (privacy)
@app.get("/api/settings")
def get_settings():
    return workspace.public_settings()


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    return workspace.save_settings(body)


@app.post("/api/settings/test")
def test_llm():
    import httpx
    t = workspace.llm_target()
    if not t["enabled"]:
        return {"ok": False, "message": "No LLM is active (local-only mode, or no API key)."}
    try:
        r = httpx.post(f"{t['base_url']}/chat/completions", timeout=60,
                       headers={"Authorization": f"Bearer {t['api_key']}"},
                       json={"model": t["model"], "max_tokens": 300,
                             **({"reasoning_effort": "low"} if "gpt-oss" in t["model"] else {}),
                             "messages": [{"role": "user", "content": "Reply with the word OK."}]})
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Could not reach {t['base_url']}: {exc}"}
    if r.is_success:
        return {"ok": True, "message": f"{t['label']} answered: {r.json()['choices'][0]['message']['content']!r}"}
    return {"ok": False, "message": f"HTTP {r.status_code}: {r.text[:300]}"}


# ---------------------------------------------------------------------------------------- uploads & sources
@app.post("/api/uploads")
def upload(file: UploadFile = File(...)):
    return workspace.save_upload(file.filename or "upload.xlsx", file.file)


@app.get("/api/uploads/{upload_id}")
def get_upload(upload_id: str):
    return workspace.get_upload(upload_id)


@app.post("/api/uploads/{upload_id}/discover")
def discover(upload_id: str, body: dict = Body(...)):
    return workspace.discover_assets(upload_id, body.get("mapping") or {})


@app.post("/api/uploads/{upload_id}/ingest")
def ingest(upload_id: str, body: dict = Body(...)):
    """Confirm mapping (+ new assets), register the file as a source, and rebuild in the background."""
    _guard_writes()
    for a in body.get("assets") or []:
        catalog.upsert_asset(a["asset_id"], a.get("name", ""), a.get("area", "GEN"), a.get("asset_class", "unclassified"),
                             a.get("aliases") or [])
    src = workspace.add_source(upload_id, body.get("mapping") or {})
    job = _start_build(body.get("llm", "auto"))
    return {"source": src, "job": job}


@app.get("/api/sources")
def list_sources():
    return {"sources": workspace.sources()}


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: str):
    _guard_writes()
    if not workspace.remove_source(source_id):
        raise HTTPException(404, "No such source")
    return {"removed": source_id, "next": "Rebuild to drop its work orders from the graph."}


@app.post("/api/workspace/demo")
def load_demo():
    """Demo plant: demo asset register, the synthetic SAP export as the only source, then build."""
    _guard_writes()
    catalog.reset_to_demo()
    for s in workspace.sources():
        workspace.remove_source(s["id"])
    src = workspace.register_demo_source()
    return {"source": src, "job": _start_build("auto")}


@app.post("/api/workspace/fresh")
def fresh_workspace():
    """Empty workspace for a real plant: no assets, no sources, empty graph (audit log kept)."""
    _guard_writes()
    catalog.clear()
    for s in workspace.sources():
        workspace.remove_source(s["id"])
    with session() as repo:
        repo.reset()
        repo.audit("web", "fresh_workspace", {})
    return {"ok": True}


# ---------------------------------------------------------------------------------------- assets & areas
@app.get("/api/assets")
def assets():
    with session() as repo:
        return reasoning.list_assets(repo)


@app.post("/api/assets")
def create_asset(body: dict = Body(...)):
    if not body.get("asset_id"):
        raise ValueError("asset_id is required")
    return catalog.upsert_asset(body["asset_id"], body.get("name", ""), body.get("area", "GEN"),
                                body.get("asset_class", "unclassified"), body.get("aliases") or [])


@app.put("/api/assets/{asset_id}")
def update_asset(asset_id: str, body: dict = Body(...)):
    cur = catalog.ASSETS.get(asset_id.upper())
    if not cur:
        raise HTTPException(404, "No such asset")
    return catalog.upsert_asset(asset_id, body.get("name", cur["name"]), body.get("area", cur["area"]),
                                body.get("asset_class", cur["class"]), body.get("aliases", cur["aliases"]))


@app.delete("/api/assets/{asset_id}")
def delete_asset(asset_id: str):
    if not catalog.delete_asset(asset_id):
        raise HTTPException(404, "No such asset")
    return {"deleted": asset_id, "next": "Rebuild so work orders are re-linked."}


@app.post("/api/areas")
def create_area(body: dict = Body(...)):
    return catalog.upsert_area(body["code"], body.get("label", body["code"]), body.get("text_prefixes"))


# ---------------------------------------------------------------------------------------- taxonomy
@app.get("/api/taxonomy")
def get_taxonomy():
    out = {}
    for kind, terms in VOCAB.items():
        out[kind] = [{"id": k, "label": v["label"], "synonyms": v.get("synonyms", []),
                      "builtin": not v.get("extension"),
                      "editable_synonyms": [s for s in v.get("synonyms", []) if not taxonomy.is_builtin_synonym(kind, k, s)],
                      **{f: v.get(f) for f in ("category", "iso_mechanism", "components", "default_mode") if v.get(f)}}
                     for k, v in sorted(terms.items(), key=lambda kv: kv[1]["label"])]
    return out


@app.post("/api/taxonomy/{kind}")
def add_term(kind: str, body: dict = Body(...)):
    return taxonomy.add_term(kind, body.get("label", ""), body.get("synonyms") or [], body.get("id"),
                             body.get("components") or [], body.get("category"))


@app.post("/api/taxonomy/{kind}/{term_id}/synonyms")
def add_synonym(kind: str, term_id: str, body: dict = Body(...)):
    return taxonomy.add_synonym(kind, term_id, body.get("synonym", ""))


@app.delete("/api/taxonomy/{kind}/{term_id}")
def delete_term(kind: str, term_id: str, synonym: str | None = None):
    if not taxonomy.remove_extension(kind, term_id, synonym):
        raise HTTPException(400, "Only workspace-added terms and synonyms can be removed.")
    return {"removed": term_id, "synonym": synonym}


# ---------------------------------------------------------------------------------------- review
@app.get("/api/review/queue")
def review_queue(kind: str | None = None, asset: str | None = None, limit: int = 50):
    with session() as repo:
        aid = reasoning.resolve_asset_ref(asset) if asset else None
        items = verify.queue(repo, limit, kind, aid)
        proposed = repo.facts(status="proposed")
    counts = Counter(f["object_kind"] for f in proposed if f["object_kind"] != "asset")
    return {"items": items, "proposed_total": sum(counts.values()), "proposed_by_kind": dict(counts)}


@app.post("/api/facts/{fact_id}/decision")
def decide(fact_id: str, body: dict = Body(...)):
    _guard_writes()
    status = body.get("status")
    if status not in ("verified", "rejected"):
        raise ValueError("status must be verified or rejected")
    with session() as repo:
        n = verify.decide(repo, [fact_id], status, body.get("by") or "web user", body.get("value") or None, body.get("note"))
        if not n:
            raise HTTPException(404, "No such fact")
        return repo.facts(fact_id=fact_id)[0]


@app.post("/api/facts/bulk-approve")
def bulk_approve(body: dict = Body(...)):
    _guard_writes()
    with session() as repo:
        n = verify.approve_above(repo, float(body.get("min_confidence", 0.95)), body.get("by") or "web user", body.get("kind"))
    return {"verified": n}


@app.get("/api/gaps")
def gaps(status: str = "open"):
    with session() as repo:
        items = verify.gap_questions(repo, None, status)
    for q in items:
        q["asset"] = catalog.ASSETS.get(q["asset_id"], {}).get("name", q["asset_id"])
        q["failure_mode_label"] = label("failure_mode", q["failure_mode"])
    return {"items": items}


@app.post("/api/gaps/{question_id}/answer")
def answer_gap(question_id: str, body: dict = Body(...)):
    _guard_writes()
    with session() as repo:
        return verify.answer_gap(repo, question_id, body.get("answer", ""), body.get("by") or "web user")


@app.get("/api/drift")
def drift(status: str = "open"):
    with session() as repo:
        return {"items": verify.drift_proposals(repo, status)}


@app.post("/api/drift/{proposal_id}/decision")
def drift_decision(proposal_id: str, body: dict = Body(...)):
    _guard_writes()
    decision = {"approve": "approved", "map": "mapped", "reject": "rejected"}.get(body.get("decision"), body.get("decision"))
    with session() as repo:
        return verify.decide_drift(repo, proposal_id, decision, body.get("by") or "web user", body.get("to") or None,
                                   body.get("label"), body.get("synonyms") or [], body.get("components") or [],
                                   body.get("category"))


# ---------------------------------------------------------------------------------------- data (read-only evidence)
def _fact_maps(repo):
    by_event = defaultdict(dict)
    for f in repo.facts(exclude_status="rejected"):
        if f["event_id"]:
            by_event[f["event_id"]][f["object_kind"]] = f
    return by_event


@app.get("/api/events")
def list_events(q: str | None = None, area: str | None = None, asset: str | None = None,
                failure_mode: str | None = None, root_cause: str | None = None,
                review: str | None = Query(None, description="needs_review | verified"),
                start: str | None = None, end: str | None = None, failures_only: bool = False,
                sort: str = "-date", page: int = 1, page_size: int = 50):
    with session() as repo:
        evs = repo.events(start=start, end=end)
        facts = _fact_maps(repo)
    ql = (q or "").lower().strip()
    rows = []
    for e in evs:
        ef = facts.get(e["event_id"], {})
        a = catalog.ASSETS.get(e["asset_id"] or "", {})
        if area and a.get("area") != area or asset and e["asset_id"] != asset:
            continue
        if failure_mode and ef.get("failure_mode", {}).get("object_id") != failure_mode:
            continue
        if root_cause and ef.get("root_cause", {}).get("object_id") != root_cause:
            continue
        if failures_only and "failure_mode" not in ef:
            continue
        kinds = [k for k in ef if k != "asset"]
        n_verified = sum(1 for k in kinds if ef[k]["status"] == "verified")
        if review == "needs_review" and n_verified == len(kinds):
            continue
        if review == "verified" and (not kinds or n_verified < len(kinds)):
            continue
        if ql and ql not in " ".join(str(x or "") for x in (e["event_id"], e["description"], e["remarks"], a.get("name"))).lower():
            continue
        rows.append({"order": e["event_id"], "date": e["posting_date"], "description": e["description"],
                     "asset_id": e["asset_id"], "asset": a.get("name"), "area": a.get("area"), "work_type": e["work_type"],
                     "cost": e["total_cost"], "downtime_hrs": e["downtime_hrs"], "breakdown": e["breakdown"],
                     "remarks": e["remarks"], "flag": e["work_type_flag"],
                     **{k: ({"id": ef[k]["object_id"], "label": label(k, ef[k]["object_id"]), "status": ef[k]["status"],
                             "confidence": ef[k]["confidence"], "fact_id": ef[k]["fact_id"]} if k in ef else None)
                        for k in ("component", "failure_mode", "root_cause", "action", "symptom")},
                     "facts_total": len(kinds), "facts_verified": n_verified})
    key, rev = sort.lstrip("-"), sort.startswith("-")
    sort_key = {"date": lambda r: (r["date"] or "", r["order"]), "cost": lambda r: r["cost"] or 0,
                "downtime": lambda r: r["downtime_hrs"] or 0, "asset": lambda r: r["asset"] or ""}.get(key, lambda r: r["date"] or "")
    rows.sort(key=sort_key, reverse=rev)
    page_size = max(1, min(page_size, 500))
    return {"total": len(rows), "page": page, "page_size": page_size,
            "items": rows[(page - 1) * page_size: page * page_size]}


@app.get("/api/events/{order}")
def event_detail(order: str):
    with session() as repo:
        return reasoning.get_evidence(repo, order)


@app.get("/api/evidence/{ref}")
def evidence(ref: str):
    with session() as repo:
        return reasoning.get_evidence(repo, ref)


# ---------------------------------------------------------------------------------------- graph
NODE_TYPES = {"asset": "Asset", "failure_mode": "Failure mode", "root_cause": "Root cause", "action": "Action",
              "part": "Spare part", "event": "Work order", "component": "Component"}


@app.get("/api/graph")
def graph(area: str | None = None, asset: str | None = None, failure_mode: str | None = None,
          root_cause: str | None = None, status: str = "all", start: str | None = None, end: str | None = None,
          view: str = "summary", include_parts: bool = False, include_actions: bool = False, max_events: int = 250):
    """Summary view: assets -> failure modes -> root causes, edges weighted by work orders.
    Events view: one node per work order, each edge is a reviewable fact."""
    with session() as repo:
        evs = repo.events(start=start, end=end)
        facts = _fact_maps(repo)
        parts_by_event = defaultdict(list)
        if include_parts:
            for l in repo.lines(event_ids=[e["event_id"] for e in evs]):
                parts_by_event[l["event_id"]].append(l)

    def ok_status(f):
        return status == "all" or f["status"] == status

    selected = []
    for e in evs:
        ef = facts.get(e["event_id"], {})
        fm = ef.get("failure_mode")
        a = catalog.ASSETS.get(e["asset_id"] or "")
        if not fm or not a:
            continue
        if area and a["area"] != area or asset and e["asset_id"] != asset:
            continue
        if failure_mode and fm["object_id"] != failure_mode:
            continue
        if root_cause and ef.get("root_cause", {}).get("object_id") != root_cause:
            continue
        selected.append((e, ef))
    truncated = False
    nodes, edges = {}, {}

    def node(nid, ntype, lbl, **extra):
        n = nodes.setdefault(nid, {"id": nid, "type": ntype, "label": lbl, "count": 0, **extra})
        n["count"] += 1
        return n

    def edge(src, tgt, kind, order, f=None, weight=0.0):
        eid = f"{src}->{tgt}"
        ed = edges.setdefault(eid, {"id": eid, "source": src, "target": tgt, "kind": kind, "count": 0, "verified": 0,
                                     "proposed": 0, "orders": [], "value": 0.0})
        ed["count"] += 1
        ed["value"] += weight
        if f:
            ed["verified" if f["status"] == "verified" else "proposed"] += 1
            if view == "events":
                ed.update(fact_id=f["fact_id"], status=f["status"], confidence=f["confidence"])
        if len(ed["orders"]) < 60:
            ed["orders"].append(order)

    if view == "events":
        if len(selected) > max_events:
            selected = sorted(selected, key=lambda x: x[0]["posting_date"], reverse=True)[:max_events]
            truncated = True
        for e, ef in selected:
            ev_id, aid = f"event:{e['event_id']}", e["asset_id"]
            node(ev_id, "event", e["event_id"], date=e["posting_date"], description=e["description"])
            node(f"asset:{aid}", "asset", catalog.ASSETS[aid]["name"], ref=aid)
            edge(ev_id, f"asset:{aid}", "ON_ASSET", e["event_id"], ef.get("asset"))
            for kind, rel in (("failure_mode", "HAS_FAILURE_MODE"), ("root_cause", "HAS_ROOT_CAUSE"), ("action", "RESOLVED_BY")):
                f = ef.get(kind)
                if f and (kind != "action" or include_actions) and ok_status(f):
                    node(f"{kind}:{f['object_id']}", kind, label(kind, f["object_id"]), ref=f["object_id"])
                    edge(ev_id, f"{kind}:{f['object_id']}", rel, e["event_id"], f)
            for l in parts_by_event.get(e["event_id"], []):
                node(f"part:{l['material']}", "part", l["material_description"] or l["material"], ref=l["material"])
                edge(ev_id, f"part:{l['material']}", "CONSUMED", e["event_id"], weight=l["cost"] or 0)
    else:
        for e, ef in selected:
            aid, fm = e["asset_id"], ef["failure_mode"]
            if not ok_status(fm):
                continue
            a_id, fm_id = f"asset:{aid}", f"failure_mode:{fm['object_id']}"
            node(a_id, "asset", catalog.ASSETS[aid]["name"], ref=aid)
            node(fm_id, "failure_mode", label("failure_mode", fm["object_id"]), ref=fm["object_id"])
            edge(a_id, fm_id, "FAILED_WITH", e["event_id"], fm, weight=e["downtime_hrs"] or 0)
            rc = ef.get("root_cause")
            if rc and ok_status(rc):
                rc_id = f"root_cause:{rc['object_id']}"
                node(rc_id, "root_cause", label("root_cause", rc["object_id"]), ref=rc["object_id"])
                edge(fm_id, rc_id, "CAUSED_BY", e["event_id"], rc)
            act = ef.get("action")
            if include_actions and act and ok_status(act):
                node(f"action:{act['object_id']}", "action", label("action", act["object_id"]), ref=act["object_id"])
                edge(fm_id, f"action:{act['object_id']}", "FIXED_BY", e["event_id"], act)
            for l in parts_by_event.get(e["event_id"], []):
                node(f"part:{l['material']}", "part", l["material_description"] or l["material"], ref=l["material"])
                edge(a_id, f"part:{l['material']}", "CONSUMED", e["event_id"], weight=l["cost"] or 0)
    for ed in edges.values():
        ed["value"] = round(ed["value"], 1)
    return {"view": view, "work_orders": len(selected), "truncated": truncated, "node_types": NODE_TYPES,
            "nodes": list(nodes.values()), "edges": list(edges.values())}


# ---------------------------------------------------------------------------------------- insights
@app.get("/api/insights/summary")
def insights_summary():
    with session() as repo:
        evs = repo.events()
        facts = repo.facts(exclude_status="rejected")
    failures = {f["event_id"] for f in facts if f["object_kind"] == "failure_mode" and f["event_id"]}
    ev_fail = [e for e in evs if e["event_id"] in failures]
    kinds = [f for f in facts if f["object_kind"] != "asset" and f["event_id"]]
    months = sorted({e["posting_date"][:7] for e in evs if e["posting_date"]})
    return {"work_orders": len(evs), "failure_events": len(ev_fail),
            "downtime_hrs": round(sum(e["downtime_hrs"] or 0 for e in ev_fail), 1),
            "failure_cost": round(sum(e["total_cost"] or 0 for e in ev_fail), 0),
            "verified_share": round(sum(1 for f in kinds if f["status"] == "verified") / len(kinds), 3) if kinds else 0,
            "period": {"start": months[0] if months else None, "end": months[-1] if months else None}}


@app.get("/api/insights/bad-actors")
def bad_actors(metric: str = "downtime", group_by: str = "asset", area: str | None = None, top: int = 10):
    with session() as repo:
        return reasoning.rank_bad_actors(repo, metric, group_by, area, None, None, top)


@app.get("/api/insights/recurrence")
def recurrence(window_days: int = 60, min_events: int = 3):
    with session() as repo:
        return reasoning.detect_recurrence(repo, window_days, min_events)


@app.get("/api/insights/spares")
def spares(area: str | None = None, lead_time_days: int = 30):
    with session() as repo:
        return reasoning.recommend_spares(repo, None, area, lead_time_days, top=20)


@app.get("/api/insights/quality")
def quality():
    with session() as repo:
        return reasoning.data_quality(repo)


@app.get("/api/insights/causes")
def causes(asset: str, symptom: str | None = None, failure_mode: str | None = None):
    with session() as repo:
        return reasoning.suggest_root_causes(repo, asset, symptom, failure_mode)


# ---------------------------------------------------------------------------------------- web app
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = WEB_DIST / path
        return FileResponse(f if path and f.is_file() else WEB_DIST / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def no_ui():
        return JSONResponse({"message": "API is running. Build the web app (cd web && npm install && npm run build) "
                                        "or open /docs for the API."})


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn
    print(f"Plant Memory: http://{host}:{port}   (API docs: /docs)")
    uvicorn.run(app, host=host, port=port, log_level="warning")
