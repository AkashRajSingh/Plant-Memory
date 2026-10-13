"""Web API tests (SQLite store, isolated temporary workspace)."""
import io
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from plant_memory import catalog, config, taxonomy, workspace  # noqa: E402
from plant_memory.synth import generate  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "pm.db")
    monkeypatch.setattr(config, "LLM_CACHE", data / "cache" / "llm.jsonl")
    monkeypatch.setattr(config, "RAW_EXPORT", data / "raw" / "export.xlsx")
    monkeypatch.setattr(config, "GROUND_TRUTH", data / "eval" / "truth.csv")
    monkeypatch.setattr(config, "STORE", "sqlite")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(workspace, "WORKSPACE_DIR", data / "workspace")
    monkeypatch.setattr(workspace, "UPLOAD_DIR", data / "uploads")
    monkeypatch.setattr(workspace, "SETTINGS_FILE", data / "workspace" / "settings.json")
    monkeypatch.setattr(workspace, "SOURCES_FILE", data / "workspace" / "sources.json")
    monkeypatch.setattr(catalog, "CATALOG_FILE", data / "workspace" / "catalog.json")
    monkeypatch.setattr(taxonomy, "EXTENSIONS_FILE", data / "workspace" / "extensions.json")
    catalog.reload()
    taxonomy.reload_extensions()
    from plant_memory.api import app
    yield TestClient(app)
    catalog.CATALOG_FILE.unlink(missing_ok=True)
    catalog.reload()
    taxonomy.reload_extensions()


def wait(client, job_id, timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["state"] in ("done", "failed"):
            return j
        time.sleep(0.2)
    raise AssertionError("job timed out")


def test_full_flow(client, tmp_path):
    # privacy: default is local-only without a key
    s = client.get("/api/settings").json()
    assert s["llm_mode"] == "off" and s["active"]["enabled"] is False

    # a fresh workspace knows no assets
    assert client.post("/api/workspace/fresh").json()["ok"]
    assert client.get("/api/meta").json()["assets"] == []

    # upload with non-standard column names -> mapping suggestions
    xlsx = tmp_path / "export.xlsx"
    generate(xlsx, tmp_path / "truth.csv", months=3, seed=5)
    import pandas as pd
    df = pd.read_excel(xlsx).rename(columns={"Order": "WO Number", "Work Description": "Short Text"})
    csv_bytes = df.to_csv(index=False).encode()
    up = client.post("/api/uploads", files={"file": ("plant_export.csv", io.BytesIO(csv_bytes), "text/csv")}).json()
    mapping = up["suggested_mapping"]
    assert mapping["Posting Date"] == "posting_date"
    mapping["WO Number"], mapping["Short Text"] = "order", "description"

    # required fields enforced
    bad = client.post(f"/api/uploads/{up['upload_id']}/ingest", json={"mapping": {"Posting Date": "posting_date"}})
    assert bad.status_code == 400 and "order" in bad.json()["detail"]

    # asset discovery proposes assets from functional locations
    disc = client.post(f"/api/uploads/{up['upload_id']}/discover", json={"mapping": mapping}).json()
    assert disc["unresolved"] > 0 and disc["candidates"]
    assets = disc["candidates"][:8]

    res = client.post(f"/api/uploads/{up['upload_id']}/ingest", json={"mapping": mapping, "assets": assets}).json()
    job = wait(client, res["job"]["id"])
    assert job["state"] == "done", job
    assert job["result"]["work_orders"] > 30

    st = client.get("/api/status").json()
    assert st["work_orders"] == job["result"]["work_orders"] and st["sources"] == 1

    # data table + evidence
    ev = client.get("/api/events", params={"failures_only": True, "page_size": 5}).json()
    assert ev["total"] > 0 and len(ev["items"]) <= 5
    order = ev["items"][0]["order"]
    detail = client.get(f"/api/events/{order}").json()
    assert detail["work_order"]["sap_lines"] is not None

    # review queue: approve + edit + reject
    q = client.get("/api/review/queue", params={"limit": 5}).json()
    assert q["proposed_total"] > 0
    f0 = q["items"][0]
    r = client.post(f"/api/facts/{f0['fact_id']}/decision", json={"status": "verified", "by": "tester"}).json()
    assert r["status"] == "verified" and r["verified_by"] == "tester"
    assert client.post("/api/facts/bulk-approve", json={"min_confidence": 0.95, "by": "tester"}).json()["verified"] >= 0

    # graph: summary and events views
    g = client.get("/api/graph").json()
    assert g["nodes"] and g["edges"] and {n["type"] for n in g["nodes"]} >= {"asset", "failure_mode"}
    aid = next(n["ref"] for n in g["nodes"] if n["type"] == "asset")
    ge = client.get("/api/graph", params={"view": "events", "asset": aid, "include_parts": True}).json()
    assert any(e.get("fact_id") for e in ge["edges"])

    # insights
    assert client.get("/api/insights/summary").json()["work_orders"] > 0
    assert client.get("/api/insights/bad-actors").json()["ranking"]

    # taxonomy editing
    t = client.post("/api/taxonomy/root_cause", json={"label": "Breather choked", "synonyms": ["breather blocked"]}).json()
    assert t["id"] == "breather_choked"
    assert client.delete("/api/taxonomy/root_cause/breather_choked").status_code == 200
    assert client.delete("/api/taxonomy/root_cause/misalignment").status_code == 400  # built-in

    # assets CRUD
    client.post("/api/assets", json={"asset_id": "TEST-1", "name": "Test pump", "area": "UTL", "aliases": ["tp1"]})
    assert any(a["asset_id"] == "TEST-1" for a in client.get("/api/meta").json()["assets"])
    assert client.delete("/api/assets/TEST-1").status_code == 200

    # privacy settings round trip; masked key never returned
    s = client.put("/api/settings", json={"llm_mode": "api", "api": {"api_key": "gsk_secret1234"}}).json()
    assert s["llm_mode"] == "api" and s["api"]["api_key"].endswith("1234") and "secret" not in s["api"]["api_key"]
    assert "secret" not in str(client.get("/api/settings").json())
