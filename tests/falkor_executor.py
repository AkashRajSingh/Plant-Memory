"""Test-only executor: runs the Neo4j backend's Cypher on embedded FalkorDB (an openCypher engine).

It checks the graph logic without a Neo4j server. Neo4j-only DDL (constraints, indexes) is skipped,
so the real-server test (PLANT_MEMORY_TEST_NEO4J=1) remains the final check.
"""
from __future__ import annotations

from collections import OrderedDict


def _plain(v):
    if isinstance(v, (OrderedDict, dict)):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


class FalkorExecutor:
    supports_ddl = False

    def __init__(self, path: str, graph: str = "plant_memory"):
        from redislite.falkordb_client import FalkorDB
        self.db = FalkorDB(path)
        self.graph = self.db.select_graph(graph)
        self.target = f"falkordb-embedded:{path}"

    def run(self, query: str, params: dict | None = None) -> list[dict]:
        res = self.graph.query(query, params or {})
        names = [h[1] for h in (res.header or [])]
        return [{n: _plain(v) for n, v in zip(names, row)} for row in (res.result_set or [])]


def available() -> bool:
    try:
        import redislite.falkordb_client  # noqa: F401
        return True
    except Exception:
        return False
