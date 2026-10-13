"""Core tests. Run with `python -m pytest -q`.

Storage tests run on every available backend:
- sqlite  : always
- cypher  : the Neo4j backend's Cypher on embedded FalkorDB (if `falkordblite` is installed)
- neo4j   : a real Neo4j server, only when PLANT_MEMORY_TEST_NEO4J=1. Runs in a separate database
            (NEO4J_TEST_DATABASE, default 'plantmemorytest', created if missing), never your working graph.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from plant_memory import extract_llm, pipeline, reasoning, verify  # noqa: E402
from plant_memory.extract_rules import extract  # noqa: E402
from plant_memory.ingest import build_events, load_export  # noqa: E402
from plant_memory.normalize import normalize_text, resolve_asset  # noqa: E402
from plant_memory.store import SqliteRepo  # noqa: E402
from plant_memory.synth import generate  # noqa: E402

import falkor_executor  # noqa: E402

SAMPLE = """Order,Posting Date,Work Description,Material description,Cost (In Rs),Quantity,Base Unit of Measure,Work Type,Movement type,Material,Technician Remarks,Root Cause
30000999001,10/11/2023,Parb.- Dryer elevator Bearing Change,BEARING 22215 EK/C3,"₹ 9,494.31",-2,EA,Preventive,261,5802059553,no grease in brg housing,
30000999001,10/12/2023,Parb.- Dryer elevator Bearing Change,V-BELT C-106,"₹ 2,107.35",-3,EA,Preventive,261,5814182553,,
20000999002,10/9/2023,P2R BRAN ROOM SHAFT BROKEN(REPAIR),"ELCTRD WELD,2.5MM",₹ 595.72,-1,PAC,Corrective,261,6423751283,,Overload
,10/26/2023,Reservations by MB21,"BELT DR,V,B134,16MM,11MM,3350MM","₹ 4,629.60",-8,EA,Preventive,201,5814220023,,
"""

BACKENDS = ["sqlite"]
if falkor_executor.available():
    BACKENDS.append("cypher")
if os.environ.get("PLANT_MEMORY_TEST_NEO4J") == "1":
    BACKENDS.append("neo4j")


def make_repo(backend: str, tmp: Path):
    if backend == "sqlite":
        return SqliteRepo(tmp / "pm.db")
    from plant_memory.neo4j_store import Neo4jExecutor, Neo4jRepo
    if backend == "cypher":
        return Neo4jRepo(falkor_executor.FalkorExecutor(str(tmp / "falkor.db")))
    # Real Neo4j: never the working database. Use (and create if needed) a separate test database.
    from plant_memory import config
    test_db = os.environ.get("NEO4J_TEST_DATABASE", "plantmemorytest")
    if test_db == config.NEO4J_DATABASE:
        pytest.skip("NEO4J_TEST_DATABASE must differ from NEO4J_DATABASE")
    try:
        driver = Neo4jExecutor().driver
    except SystemExit as exc:
        pytest.skip(f"Neo4j not reachable: {exc}")
    try:
        driver.execute_query("CREATE DATABASE $db IF NOT EXISTS WAIT", {"db": test_db}, database_="system")
    except Exception as exc:
        pytest.skip(f"Could not create test database '{test_db}' ({type(exc).__name__}). Neo4j Community has one "
                    "database only: point NEO4J_* at a separate, empty instance to run the Neo4j tests.")
    return Neo4jRepo(Neo4jExecutor(database=test_db))


def _sample(tmp: Path) -> Path:
    p = tmp / "sample.csv"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


def _events():
    with tempfile.TemporaryDirectory() as d:
        return build_events(load_export(_sample(Path(d))))


def test_normalize_and_assets():
    assert normalize_text("Parb.- Hodi Elevator Plmbr Blck Change") == "parboiling - hodi elevator plummer block change"
    assert resolve_asset(None, "Belt and brg change of Elev1&2-Parboilin")[0] == "PARB-ELV-01"
    assert resolve_asset(None, "P2R- ELevator 12 Chain Coupling Change")[0] == "P2R-ELV-12"
    assert resolve_asset(None, "Parb.- Dryer elevator Bearing Change")[0] == "PARB-DRY-ELV-01"
    assert resolve_asset("UTL-DM-PMP-01", "anything")[1] > 0.9


def test_ingest_merges_lines_and_parses_rupees():
    events, consumption = _events()
    assert len(events) == 2 and len(consumption) == 1
    ev = next(e for e in events if e.event_id == "30000999001")
    assert ev.total_cost == round(9494.31 + 2107.35, 2)
    assert ev.posting_date == "2023-10-11" and ev.last_posting_date == "2023-10-12"


def test_rule_extraction():
    by_id = {e.event_id: extract(e) for e in _events()[0]}
    brg = by_id["30000999001"].values
    assert brg["component"].term == "bearing" and brg["failure_mode"].term == "bearing_wear"
    assert brg["root_cause"].term == "inadequate_lubrication" and brg["root_cause"].field == "Technician Remarks"
    shaft = by_id["20000999002"].values
    assert shaft["failure_mode"].term == "shaft_fracture"
    assert shaft["root_cause"].term == "overload" and shaft["root_cause"].confidence == 0.95


def test_merge_with_llm():
    ev = next(e for e in _events()[0] if e.event_id == "30000999001")
    llm = {"id": ev.event_id, "is_failure": True, "component": "bearing", "failure_mode": "bearing_wear",
           "root_cause": "inadequate_lubrication", "action": "replace", "symptom": "other:smoke from housing",
           "confidence": {"component": 0.9, "failure_mode": 0.8, "root_cause": 0.8}}
    merged = pipeline._merge(extract(ev), llm)
    assert merged.values["failure_mode"].method == "llm+rules"
    assert merged.values["failure_mode"].confidence > 0.8
    assert ("symptom", "smoke from housing", "LLM") in merged.drift


def test_llm_disagreement_keeps_rules_and_flags():
    ev = next(e for e in _events()[0] if e.event_id == "30000999001")
    r = extract(ev)
    llm = {"id": ev.event_id, "component": "v_belt", "failure_mode": "belt_wear", "confidence": {"component": 0.85}}
    merged = pipeline._merge(r, llm)
    assert merged.values["component"].term == "bearing"
    assert merged.values["component"].confidence < r.values["component"].confidence
    assert any("LLM suggested" in n for n in merged.notes)


def test_llm_cannot_veto_planned_wear_failure():
    ev = next(e for e in _events()[0] if e.event_id == "30000999001")  # Preventive bearing change
    llm = {"id": ev.event_id, "is_failure": False, "component": "bearing", "failure_mode": "other:planned replacement"}
    merged = pipeline._merge(extract(ev), llm)
    assert merged.is_failure and merged.values["failure_mode"].term == "bearing_wear"
    assert not merged.drift, "an 'other:' answer is not drift when the rules already found a term"


def test_llm_batching_and_cache():
    calls = []

    def fake_call(batch, client, max_retries=5):
        calls.append(len(batch))
        return [{"id": p["id"], "component": "bearing", "confidence": {"component": 0.9}} for p in batch]

    events = _events()[0]
    with tempfile.TemporaryDirectory() as d:
        saved = (extract_llm.llm_target, extract_llm.LLM_CACHE, extract_llm._call)
        extract_llm.llm_target = lambda: {"enabled": True, "mode": "api", "base_url": "http://x", "model": "m",
                                          "api_key": "k", "label": "test"}
        extract_llm.LLM_CACHE, extract_llm._call = Path(d) / "cache.jsonl", fake_call
        try:
            first = extract_llm.extract_batch(events)
            second = extract_llm.extract_batch(events)
        finally:
            extract_llm.llm_target, extract_llm.LLM_CACHE, extract_llm._call = saved
    assert len(first) == 2 and first == second
    assert calls == [2], "second run must be served from cache"


@pytest.fixture(scope="module")
def dataset():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        generate(d / "export.xlsx", d / "truth.csv", months=4, seed=3)
        yield d


@pytest.mark.parametrize("backend", BACKENDS)
def test_end_to_end_on_backend(backend, dataset):
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(backend, Path(tmp))
        quiet = lambda *a: None  # noqa: E731
        s = pipeline.build(dataset / "export.xlsx", use_llm="off", repo=repo, log=quiet)
        assert s["work_orders"] > 50 and s["facts"] > 200

        # reads
        facts = repo.facts()
        assert len(facts) >= s["facts"]
        assert {"fact_id", "object_kind", "object_id", "confidence", "status", "evidence_snippet"} <= set(facts[0])
        events = repo.events()
        assert len(events) == s["work_orders"] and events[0]["posting_date"] <= events[-1]["posting_date"]
        assert any(l["event_id"] is None for l in repo.lines()), "consumption-only lines are stored"

        # reasoning
        res = reasoning.suggest_root_causes(repo, "Dryer elevator")
        assert res["causes"] and all(c["evidence_orders"] for c in res["causes"])
        assert reasoning.rank_bad_actors(repo, metric="cost")["ranking"]
        assert reasoning.asset_history(repo, "Hodi Elev")["events"]
        assert reasoning.find_similar_failures(repo, "bearing noise elevator")["matches"]
        assert "recurrences" in reasoning.detect_recurrence(repo, window_days=60, min_events=2)
        assert reasoning.recommend_spares(repo, area="PARB")["recommendations"]
        ev_id = events[0]["event_id"]
        assert reasoning.get_evidence(repo, ev_id)["work_order"]["sap_lines"] is not None

        # verification: reject one, edit one, bulk-approve, answer a gap
        q = verify.queue(repo, 5)
        rejected = q[0]["fact_id"]
        verify.decide(repo, [rejected], "rejected", "tester")
        n_bulk = verify.approve_above(repo, 0.95, "tester")
        assert n_bulk > 0
        gaps = verify.gap_questions(repo)
        if gaps:
            r = verify.answer_gap(repo, gaps[0]["question_id"], "pulley misalignment after belt jointing", "Ramesh")
            assert r["mapped_root_cause"] == "misalignment"
            assert repo.facts(fact_id=r["fact_id"])[0]["method"] == "technician"
        repo.commit()

        # rebuild keeps decisions
        pipeline.build(dataset / "export.xlsx", use_llm="off", repo=repo, log=quiet)
        assert repo.facts(fact_id=rejected)[0]["status"] == "rejected"
        assert len(repo.facts(status="verified")) >= n_bulk
        if gaps:
            assert repo.facts(fact_id=r["fact_id"]), "technician knowledge survives rebuilds"
            assert repo.gaps(status="answered")
        assert repo.drift() is not None
        repo.close()


@pytest.mark.parametrize("backend", BACKENDS)
def test_edit_fact_value(backend, dataset):
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(backend, Path(tmp))
        pipeline.build(dataset / "export.xlsx", use_llm="off", repo=repo, log=lambda *a: None)
        f = next(x for x in repo.facts(kind="root_cause", status="proposed"))
        new = "overload" if f["object_id"] != "overload" else "misalignment"
        verify.decide(repo, [f["fact_id"]], "verified", "tester", new_value=new)
        after = repo.facts(fact_id=f["fact_id"])
        assert len(after) == 1 and after[0]["object_id"] == new and after[0]["status"] == "verified"
        assert after[0]["method"].endswith("+edited")
        repo.close()
