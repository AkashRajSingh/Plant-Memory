"""Plant Memory command line. Run `python -m plant_memory --help`."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, extract_llm, reasoning, verify
from .store import session
from .taxonomy import VOCAB, label


def _print(obj, as_json: bool, formatter=None) -> None:
    if as_json or formatter is None:
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    else:
        formatter(obj)


def _table(rows: list[list], headers: list[str]) -> None:
    rows = [[("" if c is None else str(c)) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    widths = [min(w, 48) for w in widths]
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for r in rows:
        print(fmt.format(*[c[:48] for c in r]))


# ---------------- formatters ----------------
def f_causes(r):
    print(f"\n{r['asset']} ({r['asset_id']})  filter: {r['filter']}")
    print(f"{r['events_considered']} matching failure events, {r['events_with_cause']} with a recorded cause\n")
    _table([[c["label"], f"{c['score']:.0%}", c["confidence"], c["events_supporting"], c["verified_events"],
             ", ".join(c["evidence_orders"][:4]) + (" + technician" if c["technician_statements"] else "")]
            for c in r["causes"]], ["Root cause", "Score", "Confidence", "Events", "Verified", "Evidence"])
    if r["typical_actions"]:
        print("\nTypical fix: " + ", ".join(f"{a['label']} ({a['events']})" for a in r["typical_actions"]))
    if r["typical_parts"]:
        print("Typical parts: " + "; ".join(f"{p['description']} [{p['material']}]" for p in r["typical_parts"][:3]))
    for n in r["notes"]:
        print(f"Note: {n}")
    if r["sparse_evidence"]:
        print("Warning: sparse evidence. Ask a technician (see `gaps`).")


def f_history(r):
    s = r["summary"]
    print(f"\n{r['name']} ({r['asset_id']}): {s['work_orders']} work orders, {s['failure_events']} failures, "
          f"Rs {s['total_cost_rs']:,.0f}, {s['downtime_hrs']} h breakdown downtime")
    print("Top failure modes: " + ", ".join(f"{m['label']} ({m['events']})" for m in s["top_failure_modes"]) + "\n")
    _table([[e["date"], e["order"], e["description"], (e["failure_mode"] or {}).get("label"),
             (e["root_cause"] or {}).get("label"), (e["action"] or {}).get("label"), f"{e['cost_rs']:,.0f}"]
            for e in r["events"]], ["Date", "Order", "Description", "Failure mode", "Root cause", "Action", "Cost Rs"])


def f_bad(r):
    print(f"\nFailure {r['metric']} by {r['group_by']} ({r['unit']}), total {r['total']:,} over {r['failure_events']} events")
    _table([[i + 1, x["name"], f"{x[r['metric']]:,}", x["events"], f"{x['share']:.0%}", f"{x['cumulative_share']:.0%}",
             ", ".join(x["evidence_orders"][:3])] for i, x in enumerate(r["ranking"])],
           ["#", "Name", r["metric"], "Events", "Share", "Cum.", "Top orders"])
    print(f"\n{r['insight']}")


def f_recur(r):
    print(f"\nRepeat failures: >= {r['min_events']} in {r['window_days']} days")
    _table([[x["asset"], x["label"], x["events_in_window"], f"{x['first']} to {x['last']}", f"{x['cost_rs']:,.0f}",
             ", ".join(f"{k} ({v})" for k, v in x["known_causes"].items())] for x in r["recurrences"]],
           ["Asset", "Failure mode", "Events", "Window", "Cost Rs", "Known causes"])


def f_spares(r):
    print(f"\nSpares, lead time {r['lead_time_days']} days, {r['months_of_history']} months of history. {r['method']}")
    _table([[x["description"], x["material"], x["criticality"], x["avg_monthly_use"], x["suggested_min_stock"], x["uom"],
             f"{x['breakdown_downtime_hrs']}", ", ".join(x["used_on_assets"][:2])] for x in r["recommendations"]],
           ["Material", "Code", "Criticality", "Avg/month", "Min stock", "UoM", "Downtime h", "Used on"])


def f_similar(r):
    print(f"\nSimilar past failures for: {r['query']}")
    _table([[m["date"], m["order"], m["asset"], m["description"], m["root_cause"], m["action"], m["similarity"]]
            for m in r["matches"]], ["Date", "Order", "Asset", "Description", "Root cause", "Action", "Sim"])


def f_queue(items):
    _table([[q["fact_id"], q["object_kind"], q["label"], q["confidence"], q["method"], (q["evidence"] or "")[:40]]
            for q in items], ["Fact", "Kind", "Value", "Conf", "Method", "Evidence"])


def f_gaps(items):
    for q in items:
        print(f"\n[{q['question_id']}] {q['question']}\n    orders: {', '.join(q['event_ids'][:6])}")


def f_drift(items):
    _table([[d["proposal_id"], d["kind"], d["phrase"], d["count"], d["status"], ", ".join(d["event_ids"][:3])]
            for d in items], ["Id", "Kind", "Phrase", "Events", "Status", "Orders"])


# ---------------- commands ----------------
def cmd_generate(a):
    from .synth import generate
    res = generate(Path(a.out), Path(a.truth), months=a.months, seed=a.seed)
    print(f"Wrote {res['rows']} SAP lines / {res['orders']} work orders to {res['file']}")
    print(f"Ground truth (for evaluation only) at {a.truth}")


def cmd_build(a):
    from .pipeline import build
    res = build(a.input, use_llm=a.llm, limit=a.limit)
    _print(res, True)


def cmd_llm_check(a):
    from .workspace import llm_target
    t = llm_target()
    if t["mode"] == "off":
        sys.exit("Privacy mode is 'local only' (no LLM). Change it in the web app's Settings, or data/workspace/settings.json.")
    if not t["enabled"]:
        sys.exit("No API key. Create one at https://console.groq.com/keys and put GROQ_API_KEY in .env (or in Settings).")
    import httpx
    print(f"Testing {t['label']} at {t['base_url']} ...")
    r = httpx.post(f"{t['base_url']}/chat/completions", timeout=60,
                   headers={"Authorization": f"Bearer {t['api_key']}"},
                   json={"model": t["model"], "max_tokens": 300,  # reasoning models spend tokens before answering
                         **({"reasoning_effort": "low"} if "gpt-oss" in t["model"] else {}),
                         "messages": [{"role": "user", "content": "Reply with the word OK."}]})
    print(r.status_code, r.json()["choices"][0]["message"]["content"] if r.is_success else r.text)
    if r.status_code == 404:
        m = httpx.get(f"{t['base_url']}/models", timeout=30, headers={"Authorization": f"Bearer {t['api_key']}"})
        if m.is_success:
            ids = sorted(x["id"] for x in m.json().get("data", []) if "whisper" not in x["id"] and "tts" not in x["id"])
            print(f"\nModel '{t['model']}' is not available. Models you can use:\n  " + "\n  ".join(ids))


def with_con(fn):
    def run(a):
        with session() as repo:
            try:
                return fn(repo, a)
            except (reasoning.NotFound, ValueError) as exc:
                sys.exit(f"Error: {exc}")
    return run


@with_con
def cmd_assets(repo, a):
    r = reasoning.list_assets(repo, a.area)
    _print(r, a.json, lambda r: _table([[x["asset_id"], x["name"], x["area"], x["asset_class"], x["work_orders"], x["failure_events"]]
                                        for x in r["assets"]], ["Id", "Name", "Area", "Class", "Orders", "Failures"]))


@with_con
def cmd_history(repo, a):
    _print(reasoning.asset_history(repo, a.asset, a.start, a.end, a.limit), a.json, f_history)


@with_con
def cmd_causes(repo, a):
    _print(reasoning.suggest_root_causes(repo, a.asset, a.symptom, a.failure_mode), a.json, f_causes)


@with_con
def cmd_similar(repo, a):
    _print(reasoning.find_similar_failures(repo, a.text, a.asset, a.k), a.json, f_similar)


@with_con
def cmd_bad(repo, a):
    _print(reasoning.rank_bad_actors(repo, a.metric, a.by, a.area, a.start, a.end, a.top), a.json, f_bad)


@with_con
def cmd_recur(repo, a):
    _print(reasoning.detect_recurrence(repo, a.window, a.min_events, a.asset), a.json, f_recur)


@with_con
def cmd_spares(repo, a):
    _print(reasoning.recommend_spares(repo, a.asset, a.area, a.lead_time), a.json, f_spares)


@with_con
def cmd_evidence(repo, a):
    _print(reasoning.get_evidence(repo, a.ref), True)


@with_con
def cmd_quality(repo, a):
    _print(reasoning.data_quality(repo), True)


@with_con
def cmd_queue(repo, a):
    _print(verify.queue(repo, a.limit, a.kind, reasoning.resolve_asset_ref(a.asset) if a.asset else None), a.json, f_queue)


@with_con
def cmd_review(repo, a):
    """Interactive verification session, lowest confidence first."""
    items = verify.queue(repo, a.limit, a.kind, reasoning.resolve_asset_ref(a.asset) if a.asset else None)
    if not items:
        print("Nothing to review.")
        return
    print(f"Reviewing {len(items)} facts as {a.by}. [a]pprove  [r]eject  [e]dit  [s]kip  [q]uit\n")
    for q in items:
        print(f"{q['fact_id']}  {q['posting_date']}  {q['description']}")
        print(f"   {q['object_kind']} = {q['label']}  (confidence {q['confidence']}, {q['method']})")
        print(f"   evidence [{q['evidence_field']}]: {q['evidence']}")
        if q["note"]:
            print(f"   note: {q['note']}")
        choice = input("   > ").strip().lower()
        if choice == "q":
            break
        if choice == "a":
            verify.decide(repo, [q["fact_id"]], "verified", a.by)
        elif choice == "r":
            verify.decide(repo, [q["fact_id"]], "rejected", a.by, note=input("   reason: ").strip() or None)
        elif choice == "e":
            print("   options: " + ", ".join(VOCAB[q["object_kind"]]))
            verify.decide(repo, [q["fact_id"]], "verified", a.by, new_value=input("   correct value: ").strip())
        repo.commit()
        print()


@with_con
def cmd_decide(repo, a):
    n = verify.decide(repo, a.fact_ids, a.status, a.by, new_value=getattr(a, "value", None), note=a.note)
    print(f"{n} fact(s) marked {a.status} by {a.by}")


@with_con
def cmd_approve_above(repo, a):
    n = verify.approve_above(repo, a.min, a.by, a.kind)
    print(f"{n} fact(s) with confidence >= {a.min} verified by {a.by}")


@with_con
def cmd_gaps(repo, a):
    _print(verify.gap_questions(repo, reasoning.resolve_asset_ref(a.asset) if a.asset else None, a.status), a.json, f_gaps)


@with_con
def cmd_answer(repo, a):
    _print(verify.answer_gap(repo, a.question_id, a.answer, a.by), True)


@with_con
def cmd_drift(repo, a):
    if a.action == "list":
        _print(verify.drift_proposals(repo, a.status), a.json, f_drift)
        return
    if not a.proposal_id:
        sys.exit("proposal id required")
    decision = {"approve": "approved", "map": "mapped", "reject": "rejected"}[a.action]
    _print(verify.decide_drift(repo, a.proposal_id, decision, a.by, mapped_to=a.to, new_label=a.label,
                               synonyms=[s.strip() for s in (a.synonyms or "").split(",") if s.strip()],
                               components=[s.strip() for s in (a.components or "").split(",") if s.strip()],
                               category=a.category), True)


@with_con
def cmd_eval(repo, a):
    from .evaluate import run, to_markdown
    res = run(repo)
    if a.json:
        _print(res, True)
    else:
        print(to_markdown(res))
        print(f"Saved to {config.REPORTS_DIR / 'eval_report.md'}")


@with_con
def cmd_export(repo, a):
    from .ontology_export import export
    _print(export(repo, a.out), True)


def cmd_neo4j_check(a):
    from .neo4j_store import Neo4jRepo
    info = Neo4jRepo().check()
    _print(info, True)
    if info.get("other_nodes_in_database"):
        print(f"Note: this database also holds {info['other_nodes_in_database']} non-Plant-Memory nodes. "
              "Builds only touch nodes labelled :PM, but a dedicated database is cleaner.")
    if not info.get("plant_memory_nodes"):
        print("Connected. No Plant Memory data yet: run `python -m plant_memory build`.")


@with_con
def cmd_reset(repo, a):
    if not a.yes:
        sys.exit(f"This deletes all Plant Memory data in {repo.describe()} (facts, verifications, gap answers, drift "
                 "decisions; the audit log is kept). Re-run with --yes to confirm.")
    repo.reset()
    repo.audit("cli", "reset", {"store": repo.describe()})
    print(f"Cleared Plant Memory data in {repo.describe()}. Run `python -m plant_memory build` to rebuild.")


def cmd_mcp_check(a):
    """Start the MCP server the way Claude Desktop does (stdio, via plant_memory_mcp.py) and call a few tools."""
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    launcher = config.PROJECT_ROOT / "plant_memory_mcp.py"

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(launcher)])
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = (await s.list_tools()).tools
                print(f"Server started: {len(tools)} tools -> " + ", ".join(t.name for t in tools))
                res = await s.call_tool("suggest_root_causes", {"asset": "Dryer elevator", "symptom": "noise"})
                data = json.loads(res.content[0].text)
                if "error" in data:
                    print("Tool call returned an error: " + data["error"])
                    return False
                top = data["causes"][0] if data["causes"] else None
                print(f"suggest_root_causes(Dryer elevator, noise) -> " +
                      (f"{top['label']} ({top['score']:.0%}), evidence {', '.join(top['evidence_orders'][:3])}" if top else "no causes"))
                return True

    ok = asyncio.run(run())
    py = sys.executable.replace("\\", "\\\\")
    path = str(launcher).replace("\\", "\\\\")
    print("\nPaste this into Claude Desktop's config (Settings > Developer > Edit Config):\n")
    print('{\n  "mcpServers": {\n    "plant-memory": {\n      "command": "' + py + '",\n      "args": ["' + path + '"]\n    }\n  }\n}')
    if not ok:
        sys.exit(1)


def cmd_serve(a):
    from .api import serve
    serve(a.host, a.port)


def cmd_mcp(a):
    from .mcp_server import main
    main()


def cmd_demo(a):
    """Generate data, build, simulate a technician review, evaluate."""
    from .evaluate import run
    from .ontology_export import export
    from .pipeline import build
    from .synth import generate
    print("1/5 Generating synthetic SAP PM export ...")
    generate(config.RAW_EXPORT, config.GROUND_TRUTH)
    print("2/5 Building the ontology ...")
    print(json.dumps(build(use_llm=a.llm), indent=2))
    with session() as repo:
        print("3/5 Simulating technician review: approving facts that came from structured SAP columns ...")
        print(f"    {verify.approve_above(repo, 0.95, 'Demo reviewer')} facts verified")
        print("4/5 Evaluating ...")
        res = run(repo)
        ex, dx = res["extraction"], res["diagnosis"]
        print(f"    failure mode accuracy {ex['failure_mode']['accuracy']}, root cause precision {ex['root_cause']['precision']}")
        print(f"    diagnosis top-1: raw SAP {dx['baseline_top1']} vs Plant Memory {dx['plant_memory_top1']}")
        print("5/5 Exporting RDF ...")
        print(f"    {export(repo)['triples']} triples -> {config.ONTOLOGY_EXPORT}")
    print("\nTry: python -m plant_memory causes \"Dryer elevator\" --symptom noise")


def main(argv=None):
    p = argparse.ArgumentParser(prog="plant_memory", description="Maintenance knowledge ontology + MCP server")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.add_argument("--store", choices=["auto", "neo4j", "sqlite"],
                   help="storage backend (default: PLANT_MEMORY_STORE, else Neo4j when NEO4J_PASSWORD is set)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("generate", help="create a synthetic SAP PM export")
    s.add_argument("--out", default=str(config.RAW_EXPORT))
    s.add_argument("--truth", default=str(config.GROUND_TRUTH))
    s.add_argument("--months", type=int, default=12)
    s.add_argument("--seed", type=int, default=7)
    s.set_defaults(fn=cmd_generate)

    s = sub.add_parser("build", help="ingest an export and (re)build the ontology")
    s.add_argument("--input", default=str(config.RAW_EXPORT))
    s.add_argument("--llm", choices=["auto", "on", "off"], default="auto", help="auto = use the LLM from the privacy settings, if one is available")
    s.add_argument("--limit", type=int, help="only the first N work orders (to save LLM quota)")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("demo", help="generate + build + simulated review + eval + export")
    s.add_argument("--llm", choices=["auto", "on", "off"], default="auto")
    s.set_defaults(fn=cmd_demo)

    sub.add_parser("llm-check", help="test the configured LLM connection").set_defaults(fn=cmd_llm_check)
    sub.add_parser("neo4j-check", help="test the Neo4j connection and show what is stored").set_defaults(fn=cmd_neo4j_check)

    s = sub.add_parser("assets")
    s.add_argument("--area")
    s.set_defaults(fn=cmd_assets)

    s = sub.add_parser("history", help="asset maintenance history")
    s.add_argument("asset")
    s.add_argument("--start")
    s.add_argument("--end")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_history)

    s = sub.add_parser("causes", help="likely root causes for a problem on an asset")
    s.add_argument("asset")
    s.add_argument("--symptom")
    s.add_argument("--failure-mode", dest="failure_mode")
    s.set_defaults(fn=cmd_causes)

    s = sub.add_parser("similar", help="past failures similar to a description")
    s.add_argument("text")
    s.add_argument("--asset")
    s.add_argument("-k", type=int, default=5)
    s.set_defaults(fn=cmd_similar)

    s = sub.add_parser("bad-actors", help="Pareto of failure cost / downtime / frequency")
    s.add_argument("--metric", choices=["cost", "downtime", "frequency"], default="cost")
    s.add_argument("--by", choices=["asset", "failure_mode", "asset_failure_mode"], default="asset")
    s.add_argument("--area")
    s.add_argument("--start")
    s.add_argument("--end")
    s.add_argument("--top", type=int, default=10)
    s.set_defaults(fn=cmd_bad)

    s = sub.add_parser("recurrence", help="repeat failures within a window")
    s.add_argument("--window", type=int, default=60)
    s.add_argument("--min-events", dest="min_events", type=int, default=3)
    s.add_argument("--asset")
    s.set_defaults(fn=cmd_recur)

    s = sub.add_parser("spares", help="critical spares and minimum stock")
    s.add_argument("--asset")
    s.add_argument("--area")
    s.add_argument("--lead-time", dest="lead_time", type=int, default=30)
    s.set_defaults(fn=cmd_spares)

    s = sub.add_parser("evidence", help="raw evidence for a fact id or order number")
    s.add_argument("ref")
    s.set_defaults(fn=cmd_evidence)

    sub.add_parser("quality", help="data-quality findings").set_defaults(fn=cmd_quality)

    s = sub.add_parser("queue", help="facts awaiting verification (lowest confidence first)")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--kind", choices=list(VOCAB))
    s.add_argument("--asset")
    s.set_defaults(fn=cmd_queue)

    s = sub.add_parser("review", help="interactive technician review")
    s.add_argument("--by", required=True, help="who is verifying, e.g. 'Ramesh (Sr. Technician)'")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--kind", choices=list(VOCAB))
    s.add_argument("--asset")
    s.set_defaults(fn=cmd_review)

    for name, status in [("approve", "verified"), ("reject", "rejected")]:
        s = sub.add_parser(name, help=f"mark facts {status}")
        s.add_argument("fact_ids", nargs="+")
        s.add_argument("--by", required=True)
        s.add_argument("--note")
        if name == "approve":
            s.add_argument("--value", help="correct the value while approving")
        s.set_defaults(fn=cmd_decide, status=status)

    s = sub.add_parser("approve-above", help="bulk-verify facts at or above a confidence")
    s.add_argument("--min", type=float, default=0.95)
    s.add_argument("--by", required=True)
    s.add_argument("--kind", choices=list(VOCAB))
    s.set_defaults(fn=cmd_approve_above)

    s = sub.add_parser("gaps", help="knowledge-gap questions for technicians")
    s.add_argument("--asset")
    s.add_argument("--status", default="open", choices=["open", "answered"])
    s.set_defaults(fn=cmd_gaps)

    s = sub.add_parser("answer", help="record a technician's answer to a gap question")
    s.add_argument("question_id")
    s.add_argument("answer")
    s.add_argument("--by", required=True)
    s.set_defaults(fn=cmd_answer)

    s = sub.add_parser("drift", help="ontology drift proposals: list | approve | map | reject")
    s.add_argument("action", choices=["list", "approve", "map", "reject"])
    s.add_argument("proposal_id", nargs="?")
    s.add_argument("--by", default="Reliability engineer")
    s.add_argument("--status", default="open")
    s.add_argument("--to", help="approve: new term id; map: existing term id")
    s.add_argument("--label")
    s.add_argument("--synonyms", help="comma-separated extra synonyms")
    s.add_argument("--components", help="failure modes: comma-separated component ids")
    s.add_argument("--category", help="root causes: cause category")
    s.set_defaults(fn=cmd_drift)

    sub.add_parser("eval", help="run the evaluation harness").set_defaults(fn=cmd_eval)

    s = sub.add_parser("export", help="export the ontology as RDF/Turtle")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("reset", help="delete all Plant Memory data in the current store (asks for --yes)")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_reset)

    sub.add_parser("mcp", help="run the MCP server over stdio").set_defaults(fn=cmd_mcp)
    s = sub.add_parser("serve", help="run the web app + API (http://127.0.0.1:8765)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_serve)
    sub.add_parser("mcp-check", help="test the MCP server as Claude Desktop runs it, and print its config").set_defaults(fn=cmd_mcp_check)

    a = p.parse_args(argv)
    if a.store:
        config.STORE = a.store
    a.fn(a)


if __name__ == "__main__":
    main()
