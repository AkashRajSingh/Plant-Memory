"""Evaluation harness.

E1 extraction: compares extracted facts with the generator's hidden ground truth.
E2 diagnosis: asks "this asset shows this failure; what is the most likely root
cause?" and scores two answerers against the true dominant cause:
  - baseline: what a planner gets from the raw SAP export (the Root Cause column, as typed)
  - plant memory: suggest_root_causes() over the ontology
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from .config import GROUND_TRUTH, REPORTS_DIR
from .reasoning import suggest_root_causes
from .taxonomy import VOCAB

FIELDS = ["asset", "component", "failure_mode", "root_cause", "action", "symptom"]


def load_truth(path: Path | None = None) -> dict[str, dict]:
    with open(path or GROUND_TRUTH, encoding="utf-8") as f:
        return {r["order"]: r for r in csv.DictReader(f) if r["order"]}


def _predictions(repo) -> dict[str, dict]:
    preds = defaultdict(dict)
    for r in repo.facts(exclude_status="rejected"):
        if r["event_id"]:
            preds[r["event_id"]][r["object_kind"]] = r["object_id"]
    return preds


def extraction_report(repo, truth: dict) -> dict:
    preds = _predictions(repo)
    report = {}
    for fld in FIELDS:
        tkey = "asset_id" if fld == "asset" else fld
        known = [(oid, t[tkey]) for oid, t in truth.items()
                 if t[tkey] and (fld == "asset" or t["kind"] == "failure" or fld in {"component", "action"})]
        in_vocab = [(o, v) for o, v in known if fld == "asset" or v in VOCAB[fld]]
        out_vocab = len(known) - len(in_vocab)
        predicted = [(o, v) for o, v in in_vocab if preds.get(o, {}).get(fld)]
        correct = sum(1 for o, v in predicted if preds[o][fld] == v)
        # macro F1 over classes
        classes = {v for _, v in in_vocab}
        f1s = []
        for c in classes:
            tp = sum(1 for o, v in in_vocab if v == c and preds.get(o, {}).get(fld) == c)
            fp = sum(1 for o, v in in_vocab if v != c and preds.get(o, {}).get(fld) == c)
            fn = sum(1 for o, v in in_vocab if v == c and preds.get(o, {}).get(fld) != c)
            p = tp / (tp + fp) if tp + fp else 0
            r = tp / (tp + fn) if tp + fn else 0
            f1s.append(2 * p * r / (p + r) if p + r else 0)
        report[fld] = {
            "events": len(in_vocab),
            "coverage": round(len(predicted) / len(in_vocab), 3) if in_vocab else None,
            "precision": round(correct / len(predicted), 3) if predicted else None,
            "accuracy": round(correct / len(in_vocab), 3) if in_vocab else None,
            "macro_f1": round(sum(f1s) / len(f1s), 3) if f1s else None,
            "outside_taxonomy": out_vocab,
        }
    # failure vs non-failure classification
    fail_true = {o for o, t in truth.items() if t["kind"] == "failure"}
    fail_pred = {o for o, p in preds.items() if p.get("failure_mode")}
    report["is_failure"] = {"precision": round(len(fail_true & fail_pred) / max(1, len(fail_pred)), 3),
                            "recall": round(len(fail_true & fail_pred) / max(1, len(fail_true)), 3)}
    # drift: events whose true failure mode is outside the taxonomy, flagged by a drift proposal
    drift_events = {o for o, t in truth.items() if t["failure_mode"] and t["failure_mode"] not in VOCAB["failure_mode"]}
    flagged = set()
    for d in repo.drift():
        flagged |= set(d["event_ids"])
    report["drift_detection"] = {"events_outside_taxonomy": len(drift_events),
                                 "flagged": len(drift_events & flagged)}
    return report


def diagnosis_report(repo, truth: dict, min_events: int = 4) -> dict:
    """Questions: one per (asset, failure mode) with enough true events to have a clear dominant cause."""
    groups = defaultdict(list)
    for o, t in truth.items():
        if t["kind"] == "failure" and t["failure_mode"] in VOCAB["failure_mode"]:
            groups[(t["asset_id"], t["failure_mode"])].append(o)
    raw = {r["event_id"]: r for r in repo.events()}
    questions, base_hits, pm_hits, pm_top3, grounded = [], 0, 0, 0, 0
    for (asset, fm), orders in sorted(groups.items()):
        if len(orders) < min_events:
            continue
        counts = Counter(truth[o]["root_cause"] for o in orders)
        (top, n_top), *rest = counts.most_common()
        if rest and rest[0][1] == n_top:
            continue  # no clear dominant cause; skip ambiguous questions
        # baseline: most common Root Cause text typed in SAP for this asset's events of this kind
        typed = Counter((raw[o]["root_cause_text"] or "").strip().lower() for o in orders if raw.get(o) and raw[o]["root_cause_text"])
        base_answer = typed.most_common(1)[0][0] if typed else None
        base_ok = bool(base_answer) and base_answer in [s.lower() for s in _rca_variants(top)]
        res = suggest_root_causes(repo, asset, failure_mode=fm)
        ranked = [c["root_cause"] for c in res["causes"]]
        pm_ok = bool(ranked) and ranked[0] == top
        base_hits += base_ok
        pm_hits += pm_ok
        pm_top3 += top in ranked[:3]
        grounded += all(c["evidence_orders"] for c in res["causes"]) if res["causes"] else 0
        questions.append({"asset": asset, "failure_mode": fm, "events": len(orders), "true_cause": top,
                          "baseline_answer": base_answer, "baseline_correct": base_ok,
                          "plant_memory_answer": ranked[0] if ranked else None, "plant_memory_correct": pm_ok,
                          "plant_memory_confidence": res["causes"][0]["score"] if res["causes"] else None})
    n = len(questions) or 1
    return {"questions": len(questions),
            "baseline_top1": round(base_hits / n, 3),
            "plant_memory_top1": round(pm_hits / n, 3),
            "plant_memory_top3": round(pm_top3 / n, 3),
            "plant_memory_grounded": round(grounded / n, 3),
            "detail": questions}


def _rca_variants(cause: str) -> list[str]:
    from .synth import RCA_TEXT
    return RCA_TEXT.get(cause, [])


def run(repo, truth_path: Path | None = None, write: bool = True) -> dict:
    truth = load_truth(truth_path)
    result = {"extraction": extraction_report(repo, truth), "diagnosis": diagnosis_report(repo, truth)}
    if write:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / "eval_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (REPORTS_DIR / "eval_report.md").write_text(to_markdown(result), encoding="utf-8")
    return result


def to_markdown(r: dict) -> str:
    ex, dx = r["extraction"], r["diagnosis"]
    out = ["# Plant Memory evaluation report", "", "## E1. Extraction against ground truth", "",
           "| Field | Events | Coverage | Precision | Accuracy | Macro F1 | Outside taxonomy |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
    for f in FIELDS:
        m = ex[f]
        out.append(f"| {f} | {m['events']} | {m['coverage']} | {m['precision']} | {m['accuracy']} | {m['macro_f1']} | {m['outside_taxonomy']} |")
    out += ["", f"Failure vs. planned work: precision {ex['is_failure']['precision']}, recall {ex['is_failure']['recall']}.",
            f"Drift: {ex['drift_detection']['flagged']} of {ex['drift_detection']['events_outside_taxonomy']} events with a failure mode outside the taxonomy were flagged for review.",
            "", "Coverage is the share of events where a value was extracted; precision is accuracy on those. "
            "Root cause coverage is limited by design: many work orders carry no cause evidence at all, "
            "which is what the knowledge-gap interviews are for.", "",
            "## E2. Diagnostic questions: raw SAP export vs. Plant Memory", "",
            f"{dx['questions']} questions of the form \"asset X keeps showing failure mode Y; what is the most likely root cause?\"", "",
            "| Answerer | Top-1 correct | Top-3 correct | Answers with evidence |", "| --- | --- | --- | --- |",
            f"| Raw SAP export (Root Cause column) | {dx['baseline_top1']} | n/a | n/a |",
            f"| Plant Memory ontology | {dx['plant_memory_top1']} | {dx['plant_memory_top3']} | {dx['plant_memory_grounded']} |", "",
            "| Asset | Failure mode | Events | True cause | Baseline | Plant Memory |", "| --- | --- | --- | --- | --- | --- |"]
    for q in dx["detail"]:
        out.append(f"| {q['asset']} | {q['failure_mode']} | {q['events']} | {q['true_cause']} | "
                   f"{q['baseline_answer'] or '(nothing recorded)'} {'✓' if q['baseline_correct'] else '✗'} | "
                   f"{q['plant_memory_answer'] or '(none)'} {'✓' if q['plant_memory_correct'] else '✗'} |")
    return "\n".join(out) + "\n"
