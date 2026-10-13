"""Human-in-the-loop: verification queue, knowledge-gap answers, drift decisions."""
from __future__ import annotations

from .normalize import map_term
from .store import now
from .taxonomy import VOCAB, label, load_extensions, reload_extensions, save_extensions


def queue(repo, limit: int = 20, kind: str | None = None, asset_id: str | None = None) -> list[dict]:
    """Proposed facts, lowest confidence first, with their evidence."""
    facts = [f for f in repo.facts(status="proposed", kind=kind, asset_ids=[asset_id] if asset_id else None)
             if f["object_kind"] != "asset"]
    facts.sort(key=lambda f: (f["confidence"], f["event_id"] or ""))
    facts = facts[:limit]
    events = {e["event_id"]: e for e in repo.events(event_ids=[f["event_id"] for f in facts if f["event_id"]])}
    out = []
    for f in facts:
        e = events.get(f["event_id"], {})
        out.append({**f, "label": label(f["object_kind"], f["object_id"]) if f["object_kind"] in VOCAB else f["object_id"],
                    "description": e.get("description"), "posting_date": e.get("posting_date"),
                    "evidence_field": f["evidence_field"], "evidence": f["evidence_snippet"]})
    return out


def decide(repo, fact_ids: list[str], status: str, by: str, new_value: str | None = None, note: str | None = None) -> int:
    assert status in {"verified", "rejected"}
    n = 0
    for fid in fact_ids:
        found = repo.facts(fact_id=fid)
        if not found:
            continue
        fact = found[0]
        if new_value:
            kind = fact["object_kind"]
            value = new_value
            if kind in VOCAB and value not in VOCAB[kind]:
                value = map_term(kind, value)
                if not value:
                    raise ValueError(f"'{new_value}' is not a known {kind}. Known: {', '.join(VOCAB[kind])}")
            repo.set_fact_value(fid, value)
        else:
            value = fact["object_id"]
        repo.set_fact_status([fid], status, by, now(), note)
        repo.audit(by, f"fact_{status}", {"fact_id": fid, "old": fact["object_id"], "new": value})
        n += 1
    return n


def approve_above(repo, min_confidence: float, by: str, kind: str | None = None) -> int:
    ids = [f["fact_id"] for f in repo.facts(status="proposed", min_confidence=min_confidence, kind=kind)]
    n = repo.set_fact_status(ids, "verified", by, now(), f"bulk approve >= {min_confidence}")
    repo.audit(by, "bulk_verify", {"min_confidence": min_confidence, "kind": kind, "facts": n})
    return n


def gap_questions(repo, asset_id: str | None = None, status: str = "open") -> list[dict]:
    return repo.gaps(status=status, asset_id=asset_id)


def answer_gap(repo, question_id: str, answer: str, by: str) -> dict:
    """Record a technician's answer. A recognisable cause becomes a proposed, technician-stated fact."""
    found = repo.gaps(question_id=question_id)
    if not found:
        raise ValueError(f"No question {question_id}")
    q = found[0]
    repo.update_gap(question_id, {"status": "answered", "answer": answer, "answered_by": by, "answered_at": now()})
    cause = map_term("root_cause", answer)
    result = {"question_id": question_id, "mapped_root_cause": cause, "fact_id": None, "drift_proposal": None}
    if cause:
        fid = f"T-{q['asset_id']}-{q['failure_mode']}-{cause}"
        repo.upsert_technician_fact({
            "fact_id": fid, "event_id": None, "asset_id": q["asset_id"],
            "predicate": f"hasRootCause[{q['failure_mode']}]", "object_kind": "root_cause", "object_id": cause,
            "confidence": 0.8, "method": "technician", "status": "proposed", "note": answer, "created_at": now(),
            "failure_mode": q["failure_mode"], "evidence_field": "Technician answer", "evidence_snippet": f"{by}: {answer}"})
        result["fact_id"] = fid
    else:
        pid = f"DT{question_id[1:]}"
        repo.upsert_drift({"proposal_id": pid, "kind": "root_cause", "phrase": answer.lower()[:80],
                           "event_ids": q["event_ids"], "count": 1, "status": "open"})
        result["drift_proposal"] = pid
    repo.audit(by, "gap_answered", result)
    return result


def drift_proposals(repo, status: str = "open") -> list[dict]:
    return repo.drift(status=status)


def decide_drift(repo, proposal_id: str, decision: str, by: str, mapped_to: str | None = None,
                 new_label: str | None = None, synonyms: list[str] | None = None,
                 components: list[str] | None = None, category: str | None = None) -> dict:
    """approve: add a new ontology term; map: record the phrase as a synonym of an existing term; reject.

    Approved changes are written to ontology/extensions.json and apply on the next build.
    """
    assert decision in {"approved", "mapped", "rejected"}
    found = repo.drift(proposal_id=proposal_id)
    if not found:
        raise ValueError(f"No drift proposal {proposal_id}")
    kind, phrase = found[0]["kind"], found[0]["phrase"]
    ext = load_extensions()
    change = {}
    if decision == "approved":
        term_id = mapped_to or "_".join(phrase.split())[:40]
        spec = {"label": new_label or phrase.capitalize(), "synonyms": list(dict.fromkeys([phrase] + (synonyms or [])))}
        if kind == "failure_mode":
            spec.update({"iso_mechanism": "to be classified", "components": components or []})
        if kind == "root_cause":
            spec["category"] = category or "Unclassified"
        if kind == "component":
            spec["default_mode"] = None
        ext.setdefault("terms", {}).setdefault(kind, {})[term_id] = spec
        mapped_to, change = term_id, {"added_term": term_id, "spec": spec}
    elif decision == "mapped":
        if not mapped_to or mapped_to not in VOCAB[kind]:
            raise ValueError(f"--to must be an existing {kind} id")
        ext.setdefault("synonyms", {}).setdefault(kind, {}).setdefault(mapped_to, []).append(phrase)
        change = {"synonym_of": mapped_to, "phrase": phrase}
    if change:
        save_extensions(ext)
        reload_extensions()
    repo.update_drift(proposal_id, {"status": decision, "decided_by": by, "decided_at": now(), "mapped_to": mapped_to})
    repo.audit(by, f"drift_{decision}", {"proposal_id": proposal_id, **change})
    return {"proposal_id": proposal_id, "decision": decision, **change,
            "next_step": "Run `python -m plant_memory build` to re-extract with the updated ontology." if change else None}
