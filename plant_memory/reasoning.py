"""Reasoning layer: every answer carries a confidence and the work orders behind it.

Weighting: verified facts count 1.0, proposed facts count their extraction
confidence, rejected facts count 0. Technician-stated causes add asset-level weight.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import date

from .catalog import ASSETS, MATERIALS
from .config import SPARSE_EVIDENCE_THRESHOLD
from .normalize import map_term, normalize_text, resolve_asset
from .taxonomy import FAILURE_MODES, ROOT_CAUSES, VOCAB, label


class NotFound(ValueError):
    pass


def resolve_asset_ref(ref: str) -> str:
    if not ref:
        raise NotFound("asset is required")
    if ref.upper() in ASSETS:
        return ref.upper()
    aid, conf, _ = resolve_asset(None, ref)
    if aid and conf >= 0.45:
        return aid
    raise NotFound(f"Unknown asset '{ref}'. Try one of: " + ", ".join(f"{k} ({v['name']})" for k, v in ASSETS.items()))


def _term(kind: str, value: str | None) -> str | None:
    if not value:
        return None
    if value in VOCAB[kind]:
        return value
    t = map_term(kind, value)
    if not t:  # fuzzy: 'sprocket wear' -> sprocket_tooth_wear
        import difflib
        names = {}
        for tid, spec in VOCAB[kind].items():
            for n in [tid.replace("_", " "), spec["label"]] + spec.get("synonyms", []):
                names[normalize_text(n)] = tid
        hit = difflib.get_close_matches(normalize_text(value), list(names), n=1, cutoff=0.6)
        t = names[hit[0]] if hit else None
    if not t:
        raise NotFound(f"Unknown {kind} '{value}'. Known: {', '.join(VOCAB[kind])}")
    return t


def _w(f: dict) -> float:
    return 0.0 if f["status"] == "rejected" else 1.0 if f["status"] == "verified" else float(f["confidence"])


def _event_facts(repo, event_ids: list[str]) -> dict[str, dict[str, dict]]:
    out = defaultdict(dict)
    for i in range(0, len(event_ids), 500):
        for f in repo.facts(event_ids=event_ids[i:i + 500], exclude_status="rejected"):
            out[f["event_id"]][f["object_kind"]] = f
    return out


def _failure_event_ids(repo, asset_ids: list[str]) -> list[str]:
    return sorted({f["event_id"] for f in repo.facts(kind="failure_mode", exclude_status="rejected", asset_ids=asset_ids)
                   if f["event_id"]})


def _confidence_band(score: float, support: float) -> str:
    if support < SPARSE_EVIDENCE_THRESHOLD:
        return "low (sparse evidence)"
    return "high" if score >= 0.5 else "medium" if score >= 0.25 else "low"


# --- tools ---------------------------------------------------------------------------

def list_assets(repo, area: str | None = None) -> dict:
    orders = Counter(e["asset_id"] for e in repo.events())
    failures = Counter(f["asset_id"] for f in repo.facts(kind="failure_mode", exclude_status="rejected") if f["event_id"])
    stats = {aid: {"work_orders": orders[aid], "failures": failures[aid]} for aid in ASSETS}
    out = []
    for aid, a in ASSETS.items():
        if area and a["area"] != area.upper():
            continue
        s = stats.get(aid, {})
        out.append({"asset_id": aid, "name": a["name"], "area": a["area"], "asset_class": a["class"],
                    "aliases": a["aliases"], "work_orders": s.get("work_orders", 0), "failure_events": s.get("failures", 0)})
    return {"assets": out}


def asset_history(repo, asset: str, start: str | None = None, end: str | None = None, limit: int = 50) -> dict:
    aid = resolve_asset_ref(asset)
    evs = sorted(repo.events(asset_id=aid, start=start, end=end), key=lambda e: (e["posting_date"], e["event_id"]), reverse=True)
    facts = _event_facts(repo, [e["event_id"] for e in evs])
    items, fm_count = [], Counter()
    for e in evs[:limit]:
        ef = facts.get(e["event_id"], {})
        item = {"order": e["event_id"], "date": e["posting_date"], "description": e["description"],
                "work_type": e["work_type"], "cost_rs": e["total_cost"], "downtime_hrs": e["downtime_hrs"],
                "breakdown": e["breakdown"], "technician_remarks": e["remarks"]}
        for kind in ["component", "failure_mode", "root_cause", "action", "symptom"]:
            f = ef.get(kind)
            item[kind] = {"id": f["object_id"], "label": label(kind, f["object_id"]), "confidence": f["confidence"],
                          "status": f["status"], "fact_id": f["fact_id"]} if f else None
        if e["work_type_flag"]:
            item["data_quality_flag"] = e["work_type_flag"]
        items.append(item)
    for e in evs:
        f = facts.get(e["event_id"], {}).get("failure_mode")
        if f:
            fm_count[f["object_id"]] += 1
    failures = [e for e in evs if "failure_mode" in facts.get(e["event_id"], {})]
    return {
        "asset_id": aid, "name": ASSETS[aid]["name"], "area": ASSETS[aid]["area"],
        "summary": {"work_orders": len(evs), "failure_events": len(failures),
                    "total_cost_rs": round(sum(e["total_cost"] or 0 for e in evs), 2),
                    "downtime_hrs": round(sum(e["downtime_hrs"] or 0 for e in failures), 1),
                    "top_failure_modes": [{"failure_mode": k, "label": label("failure_mode", k), "events": v}
                                          for k, v in fm_count.most_common(5)]},
        "events": items, "truncated": len(evs) > limit,
    }


def suggest_root_causes(repo, asset: str, symptom: str | None = None, failure_mode: str | None = None,
                        include_similar_assets: bool = True) -> dict:
    aid = resolve_asset_ref(asset)
    sym, fm = _term("symptom", symptom), _term("failure_mode", failure_mode)
    notes = []

    def collect(asset_ids, weight_scale):
        eids = _failure_event_ids(repo, asset_ids)
        facts = _event_facts(repo, eids)
        sel = []
        for eid in eids:
            ef = facts[eid]
            if fm and ef.get("failure_mode", {}).get("object_id") != fm:
                continue
            if sym and ef.get("symptom", {}).get("object_id") != sym and not fm:
                continue
            sel.append((eid, ef, weight_scale))
        return sel

    selected = collect([aid], 1.0)
    if not selected and (sym or fm):
        notes.append("No events on this asset match the filter; showing causes across all its failures.")
        fm_saved = fm
        fm = None
        sym_saved = sym
        sym = None
        selected = collect([aid], 1.0)
        fm, sym = fm_saved, sym_saved

    weights, support, verified, orders = Counter(), Counter(), Counter(), defaultdict(list)
    actions, parts = Counter(), Counter()
    unexplained = 0
    for eid, ef, scale in selected:
        rc = ef.get("root_cause")
        if rc:
            weights[rc["object_id"]] += _w(rc) * scale
            support[rc["object_id"]] += 1
            verified[rc["object_id"]] += rc["status"] == "verified"
            orders[rc["object_id"]].append(eid)
        else:
            unexplained += 1
        if ef.get("action"):
            actions[ef["action"]["object_id"]] += 1

    # technician knowledge for this asset (and failure mode, if given)
    tech_statements = defaultdict(list)
    for t in repo.facts(method="technician", asset_ids=[aid], exclude_status="rejected"):
        t_fm = t.get("failure_mode") or (re.search(r"\[(.*)\]", t["predicate"] or "") or [None, None])[1]
        if fm and t_fm and t_fm != fm:
            continue
        weights[t["object_id"]] += 2.0 if t["status"] == "verified" else 1.2
        tech_statements[t["object_id"]].append({"statement": t["evidence_snippet"], "status": t["status"], "fact_id": t["fact_id"]})

    similar_used = False
    if include_similar_assets and sum(support.values()) < SPARSE_EVIDENCE_THRESHOLD:
        peers = [k for k, v in ASSETS.items() if v["class"] == ASSETS[aid]["class"] and k != aid]
        if peers:
            for eid, ef, scale in collect(peers, 0.5):
                rc = ef.get("root_cause")
                if rc:
                    weights[rc["object_id"]] += _w(rc) * scale
                    orders[rc["object_id"]].append(eid)
                    similar_used = True
            if similar_used:
                notes.append(f"Few explained events on {ASSETS[aid]['name']}; added evidence from similar "
                             f"{ASSETS[aid]['class'].replace('_', ' ')}s at half weight.")

    total = sum(weights.values())
    causes = []
    for cid, w in weights.most_common():
        score = round(w / total, 3) if total else 0
        causes.append({"root_cause": cid, "label": label("root_cause", cid),
                       "category": ROOT_CAUSES.get(cid, {}).get("category"), "score": score,
                       "confidence": _confidence_band(score, support[cid] + len(tech_statements[cid]) * 2),
                       "events_supporting": support[cid], "verified_events": verified[cid],
                       "evidence_orders": orders[cid][:10], "technician_statements": tech_statements[cid]})
    for l in repo.lines(event_ids=[eid for eid, _, _ in selected]):
        parts[(l["material"], l["material_description"])] += 1
    explained = len(selected) - unexplained
    return {
        "asset_id": aid, "asset": ASSETS[aid]["name"],
        "filter": {"symptom": sym, "failure_mode": fm},
        "events_considered": len(selected), "events_with_cause": explained, "events_without_cause": unexplained,
        "causes": causes,
        "typical_actions": [{"action": a, "label": label("action", a), "events": n} for a, n in actions.most_common(3)],
        "typical_parts": [{"material": m, "description": d, "events": n} for (m, d), n in parts.most_common(5)],
        "sparse_evidence": explained < SPARSE_EVIDENCE_THRESHOLD,
        "notes": notes + ([f"{unexplained} matching events have no recorded cause; see list_knowledge_gaps."] if unexplained else []),
    }


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", normalize_text(text)) if len(t) > 1 and t not in
            {"the", "and", "of", "in", "on", "to", "for", "with", "parboiling", "paddy", "rice"}]


def find_similar_failures(repo, text: str, asset: str | None = None, k: int = 5) -> dict:
    """BM25 over each failure event's description, symptom, remarks and extracted labels."""
    aid = resolve_asset_ref(asset) if asset else None
    evs = repo.events(asset_id=aid)
    facts = _event_facts(repo, [e["event_id"] for e in evs])
    docs = {}
    for e in evs:
        ef = facts.get(e["event_id"], {})
        if "failure_mode" not in ef:
            continue
        labels = " ".join(label(kd, f["object_id"]) or "" for kd, f in ef.items() if kd in VOCAB)
        docs[e["event_id"]] = (e, _tokens(" ".join(str(x or "") for x in
                               [e["description"], e["symptom_text"], e["remarks"], e["root_cause_text"], labels])))
    q = _tokens(text)
    n = len(docs) or 1
    avgdl = sum(len(d[1]) for d in docs.values()) / n
    df = Counter(t for _, toks in docs.values() for t in set(toks))
    scored = []
    for eid, (e, toks) in docs.items():
        tf = Counter(toks)
        s = 0.0
        for t in q:
            if t in tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(toks) / (avgdl or 1)))
        if s > 0:
            scored.append((s, eid))
    scored.sort(reverse=True)
    top = scored[:k]
    out = []
    best = top[0][0] if top else 1
    parts_by_event = defaultdict(list)
    for l in repo.lines(event_ids=[eid for _, eid in top]):
        if l["material_description"]:
            parts_by_event[l["event_id"]].append(l["material_description"])
    for s, eid in top:
        e, ef = docs[eid][0], facts[eid]
        out.append({"order": eid, "date": e["posting_date"], "asset_id": e["asset_id"],
                    "asset": ASSETS.get(e["asset_id"], {}).get("name"), "description": e["description"],
                    "similarity": round(s / best, 3),
                    **{kd: label(kd, ef[kd]["object_id"]) if kd in ef else None
                       for kd in ["failure_mode", "root_cause", "action"]},
                    "parts": parts_by_event[eid],
                    "technician_remarks": e["remarks"]})
    return {"query": text, "matches": out}


def rank_bad_actors(repo, metric: str = "cost", group_by: str = "asset", area: str | None = None,
                    start: str | None = None, end: str | None = None, top: int = 10) -> dict:
    """Pareto of failure events by cost, downtime or frequency, grouped by asset or failure mode."""
    if metric not in {"cost", "downtime", "frequency"}:
        raise NotFound("metric must be cost, downtime or frequency")
    if group_by not in {"asset", "failure_mode", "asset_failure_mode"}:
        raise NotFound("group_by must be asset, failure_mode or asset_failure_mode")
    evs = [e for e in _failure_events(repo, start, end) if not area or ASSETS.get(e["asset_id"], {}).get("area") == area.upper()]
    agg = defaultdict(lambda: {"value": 0.0, "events": 0, "orders": []})
    for e in evs:
        key = {"asset": e["asset_id"], "failure_mode": e["fm"], "asset_failure_mode": f"{e['asset_id']}|{e['fm']}"}[group_by]
        v = {"cost": e["total_cost"] or 0, "downtime": e["downtime_hrs"] or 0, "frequency": 1}[metric]
        agg[key]["value"] += v
        agg[key]["events"] += 1
        agg[key]["orders"].append((v, e["event_id"]))
    total = sum(a["value"] for a in agg.values()) or 1
    ranked = sorted(agg.items(), key=lambda kv: -kv[1]["value"])
    running, k80 = 0.0, 0
    for _, a in ranked:
        running += a["value"]
        k80 += 1
        if running / total >= 0.8:
            break
    cum, out = 0.0, []
    for key, a in ranked[:top]:
        cum += a["value"]
        if group_by == "asset":
            name = ASSETS.get(key, {}).get("name", key)
        elif group_by == "failure_mode":
            name = label("failure_mode", key)
        else:
            aid, fm = key.split("|")
            name = f"{ASSETS.get(aid, {}).get('name', aid)}: {label('failure_mode', fm)}"
        out.append({"key": key, "name": name, metric: round(a["value"], 1), "events": a["events"],
                    "share": round(a["value"] / total, 3), "cumulative_share": round(cum / total, 3),
                    "evidence_orders": [o for _, o in sorted(a["orders"], reverse=True)[:5]]})
    unit = {"cost": "Rs", "downtime": "hours", "frequency": "events"}[metric]
    return {"metric": metric, "unit": unit, "group_by": group_by, "area": area, "period": {"start": start, "end": end},
            "total": round(total, 1), "failure_events": len(evs), "ranking": out,
            "insight": f"{k80} of {len(agg)} groups account for 80% of failure {metric}." if out else "No failure events in range."}


def _failure_events(repo, start=None, end=None) -> list[dict]:
    """Events that have a (non-rejected) failure mode, with that mode as `fm`."""
    fm = {f["event_id"]: f["object_id"] for f in repo.facts(kind="failure_mode", exclude_status="rejected") if f["event_id"]}
    return [{**e, "fm": fm[e["event_id"]]} for e in repo.events(event_ids=list(fm), start=start, end=end)]


def _label_of(f: dict) -> str:
    return label(f["object_kind"], f["object_id"]) if f["object_kind"] in VOCAB else f["object_id"]


def get_evidence(repo, ref: str) -> dict:
    """Evidence for a fact id (F-<order>-<kind>, T-...) or an order number."""
    if ref.startswith(("F-", "T-")):
        found = repo.facts(fact_id=ref)
        if not found:
            raise NotFound(f"No fact {ref}")
        f = found[0]
        out = {"fact": {**f, "label": _label_of(f)},
               "evidence": [{"field": f["evidence_field"], "snippet": f["evidence_snippet"]}]}
        if f["event_id"]:
            out["work_order"] = get_evidence(repo, f["event_id"])["work_order"]
        return out
    found = repo.events(event_ids=[ref])
    if not found:
        raise NotFound(f"No work order or fact '{ref}'")
    keep = ["source_row", "posting_date", "material", "material_description", "quantity", "uom", "cost"]
    lines = [{k: l[k] for k in keep} for l in repo.lines(event_ids=[ref])]
    facts = [{k: f[k] for k in ["fact_id", "event_id", "object_kind", "object_id", "confidence", "method", "status",
                                 "verified_by", "note", "evidence_field", "evidence_snippet"]} | {"label": _label_of(f)}
             for f in repo.facts(event_ids=[ref])]
    return {"work_order": {**found[0], "sap_lines": lines, "facts": facts}}


def detect_recurrence(repo, window_days: int = 60, min_events: int = 3, asset: str | None = None) -> dict:
    """Repeat failures: the same failure mode on the same asset >= min_events times within window_days."""
    aid = resolve_asset_ref(asset) if asset else None
    evs = sorted((e for e in _failure_events(repo) if not aid or e["asset_id"] == aid),
                 key=lambda e: (e["posting_date"], e["event_id"]))
    causes_by_event = {f["event_id"]: f["object_id"] for f in repo.facts(kind="root_cause", exclude_status="rejected") if f["event_id"]}
    groups = defaultdict(list)
    for e in evs:
        groups[(e["asset_id"], e["fm"])].append(e)
    out = []
    for (a, fm), es in groups.items():
        dates = [date.fromisoformat(e["posting_date"]) for e in es]
        best, i = None, 0
        for j in range(len(es)):
            while (dates[j] - dates[i]).days > window_days:
                i += 1
            if j - i + 1 >= min_events and (best is None or j - i + 1 > best[1] - best[0] + 1):
                best = (i, j)
        if best:
            chunk = es[best[0]:best[1] + 1]
            causes = Counter(causes_by_event[c["event_id"]] for c in chunk if c["event_id"] in causes_by_event)
            out.append({"asset_id": a, "asset": ASSETS.get(a, {}).get("name"), "failure_mode": fm,
                        "label": label("failure_mode", fm), "events_in_window": len(chunk),
                        "first": chunk[0]["posting_date"], "last": chunk[-1]["posting_date"],
                        "events_total": len(es), "cost_rs": round(sum(c["total_cost"] or 0 for c in chunk), 2),
                        "known_causes": {label("root_cause", c): n for c, n in causes.most_common()},
                        "evidence_orders": [c["event_id"] for c in chunk]})
    out.sort(key=lambda r: (-r["events_in_window"], -r["cost_rs"]))
    return {"window_days": window_days, "min_events": min_events, "recurrences": out}


def recommend_spares(repo, asset: str | None = None, area: str | None = None, lead_time_days: int = 30,
                     service_level_z: float = 1.65, top: int = 15) -> dict:
    """Minimum stock per material from monthly consumption, ranked by the downtime the part is linked to."""
    aid = resolve_asset_ref(asset) if asset else None
    all_lines = [l for l in repo.lines() if l["material"]]
    ls = list(all_lines)
    if aid:
        ls = [l for l in ls if l["asset_id"] == aid]
    elif area:
        ls = [l for l in ls if l["asset_id"] and ASSETS[l["asset_id"]]["area"] == area.upper()]
    if not ls:
        return {"recommendations": [], "note": "No consumption found for this scope."}
    months = sorted({l["posting_date"][:7] for l in all_lines if l["posting_date"]})
    n_months = len(months) or 1
    by_mat = defaultdict(lambda: {"monthly": Counter(), "cost": 0.0, "downtime": 0.0, "orders": set(), "assets": Counter()})
    for l in ls:
        m = by_mat[l["material"]]
        m["monthly"][l["posting_date"][:7]] += l["quantity"]
        m["cost"] += l["cost"] or 0
        if l["event_id"]:
            m["orders"].add(l["event_id"])
            if l["asset_id"]:
                m["assets"][l["asset_id"]] += 1
    seen = set()
    for l in ls:
        key = (l["material"], l["event_id"])
        if l["event_id"] and key not in seen and str(l["breakdown"] or "").lower() == "yes":
            by_mat[l["material"]]["downtime"] += l["downtime_hrs"] or 0
            seen.add(key)
    lt = lead_time_days / 30.0
    recs = []
    for code, m in by_mat.items():
        series = [m["monthly"].get(mo, 0) for mo in months]
        mean = sum(series) / n_months
        sd = math.sqrt(sum((x - mean) ** 2 for x in series) / max(1, n_months - 1))
        min_stock = math.ceil(mean * lt + service_level_z * sd * math.sqrt(lt))
        desc, unit_cost, uom, _ = MATERIALS.get(code, (None, 0, None, None))
        recs.append({"material": code, "description": desc, "uom": uom, "avg_monthly_use": round(mean, 2),
                     "monthly_std": round(sd, 2), "suggested_min_stock": min_stock,
                     "stock_value_rs": round(min_stock * unit_cost, 0), "annual_spend_rs": round(m["cost"], 0),
                     "breakdown_downtime_hrs": round(m["downtime"], 1), "used_on_orders": len(m["orders"]),
                     "used_on_assets": [ASSETS[a]["name"] for a, _ in m["assets"].most_common(4)],
                     "evidence_orders": sorted(m["orders"])[-5:]})
    recs.sort(key=lambda r: (-r["breakdown_downtime_hrs"], -r["annual_spend_rs"]))
    for r in recs:
        r["criticality"] = "critical" if r["breakdown_downtime_hrs"] >= 20 else "important" if r["breakdown_downtime_hrs"] > 0 else "routine"
    return {"scope": {"asset": aid, "area": area}, "lead_time_days": lead_time_days, "service_level_z": service_level_z,
            "months_of_history": n_months, "method": "min stock = mean monthly use x lead time + z x std x sqrt(lead time)",
            "recommendations": recs[:top]}


def data_quality(repo) -> dict:
    evs = repo.events()
    pick = lambda e, keys: {k: e[k] for k in keys}  # noqa: E731
    flags = [pick(e, ["event_id", "posting_date", "description", "work_type", "work_type_flag"]) for e in evs if e["work_type_flag"]]
    unresolved = [pick(e, ["event_id", "description"]) for e in evs if not e["asset_id"]]
    multi_date = [pick(e, ["event_id", "posting_date", "last_posting_date"]) for e in evs
                  if e["last_posting_date"] and e["posting_date"] != e["last_posting_date"]]
    return {"work_type_mislabels": {"count": len(flags), "examples": flags[:10]},
            "unresolved_assets": {"count": len(unresolved), "examples": unresolved[:10]},
            "orders_posted_on_multiple_dates": {"count": len(multi_date), "examples": multi_date[:5]}}
