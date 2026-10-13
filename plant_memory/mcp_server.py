"""Plant Memory MCP server (stdio).

Exposes the maintenance ontology to any MCP client (Claude Desktop, Claude
Code, Cursor, ...). Read tools answer with confidence and evidence; the only
write tool (submit_technician_answer) creates a proposal and never changes
verified knowledge.
"""

import functools
from typing import Literal

try:  # mcp >= 2.0 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from . import reasoning, verify
from .config import ONTOLOGY_SCHEMA
from .store import session
from .taxonomy import VOCAB

mcp = FastMCP(
    "plant-memory",
    instructions=(
        "Maintenance knowledge for a parboiled rice plant, built from SAP PM work orders and verified by technicians. "
        "Asset arguments accept an id (PARB-DRY-ELV-01), a name (Dryer elevator) or a shop-floor alias (Dryer Elev). "
        "Every answer lists evidence_orders (SAP order numbers) and a confidence; cite them, and use get_evidence "
        "to show the raw lines. Treat 'proposed' facts as unverified and say so. If sparse_evidence is true, say the "
        "answer is weak and suggest asking a technician (list_knowledge_gaps)."
    ),
)


def _tool(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            with session() as repo:
                return fn(repo, *args, **kwargs)
        except (reasoning.NotFound, ValueError) as exc:
            return {"error": str(exc)}
        except SystemExit as exc:  # e.g. Neo4j not running: report it, keep the server alive
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
    # FastMCP reads the signature: drop the leading `repo` parameter
    import inspect
    sig = inspect.signature(fn)
    wrapper.__signature__ = sig.replace(parameters=list(sig.parameters.values())[1:])
    del wrapper.__wrapped__
    return mcp.tool()(wrapper)


@_tool
def list_assets(repo, area: str | None = None) -> dict:
    """List plant assets with ids, names, aliases and failure counts. area: PARB, P2R, UTL, PCK or WH."""
    return reasoning.list_assets(repo, area)


@_tool
def get_asset_history(repo, asset: str, start: str | None = None, end: str | None = None, limit: int = 30) -> dict:
    """Maintenance history of one asset: each work order with its component, failure mode, root cause, action,
    symptom (each with confidence and verification status), cost and downtime. Dates as YYYY-MM-DD."""
    return reasoning.asset_history(repo, asset, start, end, limit)


@_tool
def suggest_root_causes(repo, asset: str, symptom: str | None = None, failure_mode: str | None = None) -> dict:
    """Rank likely root causes for a problem on an asset, from past work orders and technician statements.
    symptom / failure_mode accept ids or plain words (e.g. 'noise', 'bearing wear')."""
    return reasoning.suggest_root_causes(repo, asset, symptom, failure_mode)


@_tool
def find_similar_failures(repo, description: str, asset: str | None = None, k: int = 5) -> dict:
    """Find past failures similar to a free-text problem description, with what caused and fixed them."""
    return reasoning.find_similar_failures(repo, description, asset, k)


@_tool
def rank_bad_actors(repo, metric: Literal["cost", "downtime", "frequency"] = "cost",
                    group_by: Literal["asset", "failure_mode", "asset_failure_mode"] = "asset",
                    area: str | None = None, start: str | None = None, end: str | None = None, top: int = 10) -> dict:
    """Pareto ranking of what drives failure cost, downtime or frequency. Use it to decide what to fix first."""
    return reasoning.rank_bad_actors(repo, metric, group_by, area, start, end, top)


@_tool
def get_evidence(repo, ref: str) -> dict:
    """Raw evidence for a fact id (F-<order>-<kind>) or an SAP order number: source rows, materials, facts."""
    return reasoning.get_evidence(repo, ref)


@_tool
def detect_recurrence(repo, window_days: int = 60, min_events: int = 3, asset: str | None = None) -> dict:
    """Repeat failures: the same failure mode on the same asset at least min_events times within window_days."""
    return reasoning.detect_recurrence(repo, window_days, min_events, asset)


@_tool
def recommend_spares(repo, asset: str | None = None, area: str | None = None, lead_time_days: int = 30) -> dict:
    """Critical spares and suggested minimum stock, from consumption history and linked breakdown downtime."""
    return reasoning.recommend_spares(repo, asset, area, lead_time_days)


@_tool
def list_knowledge_gaps(repo, asset: str | None = None) -> dict:
    """Open questions for technicians where an asset keeps failing without a confirmed root cause."""
    aid = reasoning.resolve_asset_ref(asset) if asset else None
    return {"questions": verify.gap_questions(repo, aid)}


@_tool
def submit_technician_answer(repo, question_id: str, answer: str, technician: str) -> dict:
    """Record a technician's answer to a knowledge-gap question. Creates a PROPOSED fact for review."""
    return verify.answer_gap(repo, question_id, answer, technician)


@_tool
def list_drift_proposals(repo) -> dict:
    """Terms seen in new work orders that the ontology does not have yet, awaiting an engineer's decision."""
    return {"proposals": verify.drift_proposals(repo)}


@_tool
def data_quality_report(repo) -> dict:
    """Data problems found in the SAP export: breakdowns labelled Preventive, unresolved assets, split postings."""
    return reasoning.data_quality(repo)


@mcp.tool()
def ontology_terms(kind: Literal["component", "failure_mode", "root_cause", "action", "symptom"]) -> dict:
    """The controlled vocabulary for one kind of ontology term: ids, labels, synonyms."""
    return {"kind": kind, "terms": [{"id": k, "label": v["label"], "synonyms": v.get("synonyms", [])[:6]}
                                    for k, v in VOCAB[kind].items()]}


@mcp.resource("plantmemory://ontology/schema")
def ontology_schema() -> str:
    """OWL schema of the Plant Memory ontology (Turtle)."""
    return ONTOLOGY_SCHEMA.read_text(encoding="utf-8")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
