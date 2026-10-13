"""Build the ontology: ingest -> normalize -> extract (rules + Groq) -> facts, evidence, drift, gaps.

Re-running the build keeps technician decisions: a fact that was verified or
rejected keeps its status as long as the extracted value is unchanged, and
technician-stated facts are carried over untouched.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import extract_llm
from .catalog import ASSETS, MATERIALS, PROCESS_AREAS
from .config import RAW_EXPORT
from .extract_rules import Extraction, Value, extract, fm_components
from .ingest import Event, build_events, load_export
from .normalize import normalize_text
from .store import PREDICATES, now, open_repo
from .taxonomy import FAILURE_MODES, VOCAB


LLM_NO_GAP_FILL = {"component"}


def _merge(rule: Extraction, llm: dict | None) -> Extraction:
    """Combine rule and LLM outputs field by field.

    Policy, measured against ground truth (see docs/llm_evaluation.md):
    - Structured SAP columns always win.
    - On disagreement the rules keep the value; the disagreement lowers confidence and adds a note,
      so the fact moves up the technician review queue (the LLM acts as a second opinion).
    - The LLM fills gaps the rules left empty, except for components, where its gap-fills were mostly wrong.
    """
    if not llm:
        return rule
    out = Extraction(rule.event_id, rule.is_failure, dict(rule.values), list(rule.drift), list(rule.notes))
    # The rules decide failure vs. planned work (work type, action, symptom). The LLM may not veto a failure:
    # it tends to call planned wear replacements "not a failure" because the work type says Preventive.
    if llm.get("is_failure") is False and rule.is_failure and "component" not in rule.values and "symptom" not in rule.values:
        out.is_failure = False
    conf = llm.get("confidence") or {}
    evid = llm.get("evidence") or {}
    for kind in VOCAB:
        v = llm.get(kind)
        if not v or not isinstance(v, str):
            continue
        if v.startswith("other:"):
            if kind not in rule.values:  # only a gap the rules could not fill is a drift signal
                out.drift.append((kind, normalize_text(v[6:]), "LLM"))
            continue
        if v not in VOCAB[kind]:
            continue
        c_llm = min(0.85, float(conf.get(kind, 0.7) or 0.7))
        r = rule.values.get(kind)
        snippet = str(evid.get(kind) or "")
        if r and r.method == "structured":
            if r.term == v:
                out.values[kind] = Value(v, 0.97, "structured+llm", r.field, r.snippet)
            continue
        if kind == "failure_mode" and not out.is_failure:
            continue
        if r is None:
            if kind not in LLM_NO_GAP_FILL:
                out.values[kind] = Value(v, min(0.7, c_llm), "llm", "LLM", snippet or "LLM reading of the work order")
        elif r.term == v:
            out.values[kind] = Value(v, min(0.93, max(c_llm, r.confidence) + 0.07), "llm+rules", r.field, r.snippet)
        else:
            out.values[kind] = Value(r.term, round(max(0.3, r.confidence - 0.15), 3), r.method, r.field, r.snippet)
            out.notes.append(f"{kind}: LLM suggested {v}; check")
    if not out.is_failure:
        out.values.pop("failure_mode", None)
        out.values.pop("root_cause", None)
    return out


def reference_data() -> dict:
    """Assets, taxonomy and materials: the ontology's T-box, written before any events."""
    return {
        "areas": [{"code": c, "label": a["label"]} for c, a in PROCESS_AREAS.items()],
        "assets": [{"asset_id": k, "name": a["name"], "area": a["area"], "asset_class": a["class"], "aliases": a["aliases"]}
                   for k, a in ASSETS.items()],
        "terms": {kind: [{"id": tid, "label": spec["label"], "synonyms": spec.get("synonyms", []),
                          "iso_mechanism": spec.get("iso_mechanism"), "category": spec.get("category"),
                          "extension": bool(spec.get("extension"))} for tid, spec in terms.items()]
                  for kind, terms in VOCAB.items()},
        "fm_components": [{"fm": fm, "component": c} for fm, cs in fm_components().items() for c in sorted(cs)
                          if fm in VOCAB["failure_mode"]],
        "materials": [{"material": code, "description": d, "unit_cost": cost, "uom": uom, "component": comp}
                      for code, (d, cost, uom, comp) in MATERIALS.items()],
    }


def build(input_path: Path | None = None, use_llm: str = "auto", limit: int | None = None,
          db_path: Path | None = None, log=print, repo=None, inputs: list[dict] | None = None,
          progress=None) -> dict:
    """(Re)build the knowledge graph from every registered source file (or one input file).

    inputs: [{path, mapping, name}], oldest first; a work order present in several files is taken from the newest.
    progress(stage, done, total): optional callback for the UI.
    """
    progress = progress or (lambda *a: None)
    if inputs is None:
        if input_path is not None:
            inputs = [{"path": Path(input_path), "mapping": None, "name": Path(input_path).name}]
        else:
            from .workspace import source_inputs
            inputs = source_inputs() or [{"path": RAW_EXPORT, "mapping": None, "name": RAW_EXPORT.name}]
    progress("reading", 0, len(inputs))
    by_order, consumption, n_lines = {}, [], 0
    seen_cons = set()
    for i, src in enumerate(inputs, 1):
        df = load_export(src["path"], src.get("mapping"), src.get("name"))
        n_lines += len(df)
        evs, cons = build_events(df)
        for ev in evs:
            by_order[ev.event_id] = ev  # newest file wins
        for c in cons:
            key = (c["posting_date"], c["material"], c["quantity"], c["cost"], c["cost_center"])
            if key not in seen_cons:
                seen_cons.add(key)
                consumption.append(c)
        progress("reading", i, len(inputs))
    events = sorted(by_order.values(), key=lambda e: (e.posting_date, e.event_id))
    if limit:
        events = events[:limit]
    log(f"Loaded {n_lines} lines from {len(inputs)} file(s) -> {len(events)} work orders, "
        f"{len(consumption)} consumption-only lines")

    llm_on = use_llm == "on" or (use_llm == "auto" and extract_llm.available())
    if use_llm == "on" and not extract_llm.available():
        raise SystemExit("No LLM is configured. Set the privacy mode to 'api' (with a key) or 'local', "
                         "or run with --llm off.")

    def llm_log(msg):
        log(msg)
        m = re.search(r"(\d+)/(\d+)", msg)
        if m:
            progress("llm", int(m.group(1)), int(m.group(2)))

    progress("llm" if llm_on else "rules", 0, len(events))
    llm_results = extract_llm.extract_batch(events, progress=llm_log) if llm_on else {}
    if use_llm == "off":
        reason = "--llm off"
    elif extract_llm.llm_target()["mode"] == "off":
        reason = "privacy mode is 'local only'"
    else:
        reason = "no API key for the LLM provider"
    log(f"Extraction: rules + {extract_llm.target_label()} ({len(llm_results)} events)" if llm_on
        else f"Extraction: rules only ({reason})")
    progress("writing", 0, 1)

    own_repo = repo is None
    repo = repo or open_repo(db_path=db_path)
    log(f"Store: {repo.describe()}")
    kept = [f for f in repo.facts() if f["status"] != "proposed" or f["method"] == "technician"]
    old_gaps = [g for g in repo.gaps() if g["status"] != "open"]
    old_drift = [d for d in repo.drift() if d["status"] != "open"]
    repo.reset()
    repo.write_reference(reference_data())

    stats = Counter()
    drift = defaultdict(set)
    event_rows, line_rows, fact_rows = [], [], []
    line_id = 0
    for ev in events:
        ex = _merge(extract(ev), llm_results.get(ev.event_id))
        event_rows.append(_event_row(ev))
        for l in ev.lines:
            line_id += 1
            line_rows.append({**l, "line_id": line_id})
        note = "; ".join(ex.notes) or None
        added = False
        for kind, val in ex.values.items():
            if kind in {"failure_mode", "root_cause"} and not ex.is_failure:
                continue
            fact_rows.append(_fact_row(ev, kind, val.term, val.confidence, val.method, val.field, val.snippet, note))
            stats[f"facts_{kind}"] += 1
            added = True
        if added and ev.asset_id:
            fact_rows.append(_fact_row(ev, "asset", ev.asset_id, ev.asset_confidence, ev.asset_method,
                                       "Functional Location" if ev.asset_method == "functional_location" else "Work Description",
                                       ev.header.get("functional_location") or ev.description, None))
        for kind, phrase, _field in ex.drift:
            if phrase:
                drift[(kind, phrase)].add(ev.event_id)
        stats["failure_events"] += ex.is_failure
    cons_rows = []
    for l in consumption:
        line_id += 1
        cons_rows.append({**l, "line_id": line_id})

    repo.add_events(event_rows, line_rows)
    repo.add_consumption(cons_rows)
    repo.add_facts(fact_rows)
    restored = repo.restore_decisions(kept)

    decided = {(d["kind"], d["phrase"]): d for d in old_drift}
    drift_rows = []
    for i, ((kind, phrase), eids) in enumerate(sorted(drift.items(), key=lambda x: (-len(x[1]), x[0])), 1):
        prev = decided.get((kind, phrase), {})
        drift_rows.append({"proposal_id": f"D{i:03d}", "kind": kind, "phrase": phrase, "event_ids": sorted(eids),
                           "count": len(eids), "status": prev.get("status", "open"), "decided_by": prev.get("decided_by"),
                           "decided_at": prev.get("decided_at"), "mapped_to": prev.get("mapped_to")})
    repo.replace_drift(drift_rows)
    n_gaps = generate_gap_questions(repo, old_gaps)
    repo.audit("system", "build", {"input": str(input_path), "events": len(events), "llm": llm_on,
                                   "llm_events": len(llm_results), "restored_decisions": restored})
    repo.commit()
    summary = {
        "store": repo.describe(),
        "work_orders": len(events), "failure_events": stats["failure_events"],
        "facts": sum(v for k, v in stats.items() if k.startswith("facts_")),
        "by_kind": {k[6:]: v for k, v in stats.items() if k.startswith("facts_")},
        "drift_proposals": len(drift_rows), "knowledge_gap_questions": n_gaps, "restored_decisions": restored,
        "work_type_flags": sum(1 for e in events if e.work_type_flag),
        "llm": extract_llm.target_label() if llm_on else "off",
    }
    if own_repo:
        repo.close()
    progress("done", 1, 1)
    return summary


def _event_row(ev: Event) -> dict:
    h = ev.header
    dt = h.get("downtime_hrs")
    fl = h.get("functional_location")
    return {"event_id": ev.event_id, "posting_date": ev.posting_date, "last_posting_date": ev.last_posting_date,
            "description": ev.description, "normalized_text": ev.normalized_text, "work_type": ev.work_type,
            "planning_department": ev.planning_department, "functional_location": str(fl) if fl else None,
            "asset_id": ev.asset_id, "asset_confidence": ev.asset_confidence, "asset_method": ev.asset_method,
            "symptom_text": _s(h.get("symptom")), "root_cause_text": _s(h.get("root_cause")),
            "cause_category_text": _s(h.get("cause_category")), "action_text": _s(h.get("action_taken")),
            "downtime_hrs": float(dt) if dt not in (None, "") else None, "breakdown": _s(h.get("breakdown")),
            "remarks": _s(h.get("remarks")), "technician": _s(h.get("technician")), "total_cost": ev.total_cost,
            "line_count": len(ev.lines), "work_type_flag": ev.work_type_flag}


def _s(v):
    return None if v is None else str(v)


def _fact_row(ev: Event, kind: str, term: str, confidence: float, method: str, field: str, snippet, note) -> dict:
    return {"fact_id": f"F-{ev.event_id}-{kind}", "event_id": ev.event_id, "asset_id": ev.asset_id,
            "predicate": PREDICATES[kind], "object_kind": kind, "object_id": term,
            "confidence": round(float(confidence), 3), "method": method, "status": "proposed",
            "note": note, "created_at": now(), "evidence_field": field,
            "evidence_snippet": None if snippet is None else str(snippet)}


def generate_gap_questions(repo, old: list[dict] | None = None, min_events: int = 2) -> int:
    """Where an asset keeps failing the same way without a confirmed root cause, ask a technician."""
    answered = {(g["asset_id"], g["failure_mode"]): g for g in (old or [])}
    facts = repo.facts(exclude_status="rejected")
    fm_by_event = {f["event_id"]: f for f in facts if f["object_kind"] == "failure_mode" and f["event_id"] and f["asset_id"]}
    cause_ok = {f["event_id"] for f in facts if f["object_kind"] == "root_cause" and f["event_id"]
                and (f["confidence"] >= 0.7 or f["status"] == "verified")}
    groups = defaultdict(list)
    for eid, f in fm_by_event.items():
        groups[(f["asset_id"], f["object_id"])].append(eid)
    events = {e["event_id"]: e for e in repo.events(event_ids=list(fm_by_event))}
    rows = []
    candidates = []
    for (aid, fm), eids in groups.items():
        with_cause = sum(1 for e in eids if e in cause_ok)
        if len(eids) >= min_events and with_cause / len(eids) < 0.5:
            candidates.append((len(eids) - with_cause, aid, fm, sorted(eids, key=lambda e: events[e]["posting_date"]), with_cause))
    for n, (missing, aid, fm, eids, with_cause) in enumerate(sorted(candidates, key=lambda c: (-c[0], c[1], c[2])), 1):
        asset = ASSETS[aid]["name"]
        fm_label = FAILURE_MODES.get(fm, {}).get("label", fm).lower()
        techs = Counter(events[e]["technician"] for e in eids if events[e]["technician"])
        since = events[eids[0]]["posting_date"]
        q = (f"{asset}: {missing} of {len(eids)} '{fm_label}' events since {since} have no confirmed root cause. "
             f"In your experience, what usually causes {fm_label} on the {asset}, and what check would confirm it?")
        if techs:
            q += f" (Suggested: {techs.most_common(1)[0][0]})"
        prev = answered.get((aid, fm), {})
        rows.append({"question_id": f"Q{n:03d}", "asset_id": aid, "failure_mode": fm, "question": q,
                     "event_ids": eids[:12], "status": prev.get("status", "open"), "answer": prev.get("answer"),
                     "answered_by": prev.get("answered_by"), "answered_at": prev.get("answered_at")})
    repo.replace_gaps(rows)
    return len(rows)
