"""Paths and settings. Reads a .env file in the project root if present."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_EXPORT = DATA_DIR / "raw" / "sap_pm_export.xlsx"
GROUND_TRUTH = DATA_DIR / "eval" / "ground_truth.csv"
DB_PATH = DATA_DIR / "plant_memory.db"
LLM_CACHE = DATA_DIR / "cache" / "llm_extractions.jsonl"
ONTOLOGY_SCHEMA = PROJECT_ROOT / "ontology" / "plant_memory.ttl"
ONTOLOGY_EXPORT = PROJECT_ROOT / "ontology" / "plant_memory_instances.ttl"
REPORTS_DIR = PROJECT_ROOT / "reports"


def _load_dotenv() -> None:
    env = PROJECT_ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
LLM_BATCH_SIZE = int(os.environ.get("PLANT_MEMORY_LLM_BATCH", "8"))
# Answers backed by fewer events than this carry a sparse-evidence warning
SPARSE_EVIDENCE_THRESHOLD = int(os.environ.get("PLANT_MEMORY_SPARSE_THRESHOLD", "3"))

# Storage: "neo4j", "sqlite", or "auto" (Neo4j when NEO4J_PASSWORD is set, otherwise SQLite)
STORE = os.environ.get("PLANT_MEMORY_STORE", "auto")
NEO4J_URI = os.environ.get("NEO4J_URI", "neo4j://127.0.0.1:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")
