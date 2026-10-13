"""Workspace state for the app: settings (privacy mode), uploaded files, data sources, asset discovery.

Everything lives in data/workspace/ as small JSON files so the CLI, the MCP server and the web app share it.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from . import config
from .catalog import ASSETS, PROCESS_AREAS, slug
from .normalize import detect_area, match_terms, normalize_text

WORKSPACE_DIR = config.DATA_DIR / "workspace"
UPLOAD_DIR = config.DATA_DIR / "uploads"
SETTINGS_FILE = WORKSPACE_DIR / "settings.json"
SOURCES_FILE = WORKSPACE_DIR / "sources.json"

LLM_MODES = {
    "off": "Local only: rules-based extraction; no data leaves this machine",
    "api": "LLM API: work-order text is sent to an OpenAI-compatible provider (default Groq)",
    "local": "Local LLM: an OpenAI-compatible model on this machine or network (e.g. Ollama)",
}


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# --- settings ---------------------------------------------------------------------------
def default_settings() -> dict:
    return {
        "llm_mode": "api" if config.GROQ_API_KEY else "off",
        "api": {"provider": "Groq", "base_url": config.GROQ_BASE_URL, "model": config.GROQ_MODEL, "api_key": ""},
        "local": {"base_url": "http://localhost:11434/v1", "model": "llama3.1:8b"},
        "batch_size": config.LLM_BATCH_SIZE,
    }


def get_settings() -> dict:
    s = default_settings()
    saved = _read(SETTINGS_FILE, {})
    s.update({k: v for k, v in saved.items() if k not in ("api", "local")})
    for k in ("api", "local"):
        s[k].update(saved.get(k, {}))
    return s


def save_settings(new: dict) -> dict:
    cur = get_settings()
    if new.get("llm_mode") and new["llm_mode"] not in LLM_MODES:
        raise ValueError(f"llm_mode must be one of {', '.join(LLM_MODES)}")
    for k in ("llm_mode", "batch_size"):
        if k in new and new[k] is not None:
            cur[k] = new[k]
    for k in ("api", "local"):
        for kk, vv in (new.get(k) or {}).items():
            if kk == "api_key" and (vv is None or str(vv).startswith("••••")):
                continue  # masked value sent back unchanged
            cur[k][kk] = vv
    _write(SETTINGS_FILE, cur)
    return public_settings()


def public_settings() -> dict:
    s = get_settings()
    key = s["api"].get("api_key") or config.GROQ_API_KEY
    s["api"] = {**s["api"], "api_key": ("••••" + key[-4:]) if key else "", "key_source": (
        "settings" if s["api"].get("api_key") else ".env (GROQ_API_KEY)" if config.GROQ_API_KEY else "none")}
    s["modes"] = LLM_MODES
    s["active"] = llm_target()
    s["active"].pop("api_key", None)
    return s


def llm_target() -> dict:
    """Where extraction would send text right now: {enabled, mode, base_url, model, api_key, label}."""
    s = get_settings()
    mode = s["llm_mode"]
    if mode == "api":
        key = s["api"].get("api_key") or config.GROQ_API_KEY
        return {"enabled": bool(key), "mode": mode, "base_url": s["api"]["base_url"].rstrip("/"),
                "model": s["api"]["model"], "api_key": key,
                "label": f"{s['api'].get('provider') or 'API'} · {s['api']['model']}" + ("" if key else " (no API key)")}
    if mode == "local":
        return {"enabled": True, "mode": mode, "base_url": s["local"]["base_url"].rstrip("/"),
                "model": s["local"]["model"], "api_key": "local", "label": f"Local · {s['local']['model']}"}
    return {"enabled": False, "mode": "off", "base_url": "", "model": "", "api_key": "", "label": "Local only (rules)"}


# --- uploads and sources ------------------------------------------------------------------
def save_upload(filename: str, fileobj) -> dict:
    from .ingest import read_table, suggest_mapping
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)[-80:] or "upload.xlsx"
    if Path(safe).suffix.lower() not in {".xlsx", ".xls", ".csv"}:
        raise ValueError("Upload an .xlsx, .xls or .csv file")
    uid = uuid.uuid4().hex[:10]
    path = UPLOAD_DIR / f"{uid}_{safe}"
    with open(path, "wb") as f:
        shutil.copyfileobj(fileobj, f)
    try:
        df = read_table(path)
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise ValueError(f"Could not read the file: {exc}") from None
    columns = [str(c) for c in df.columns]
    sample = df.head(8).astype(object).where(df.head(8).notna(), None)
    meta = {"upload_id": uid, "filename": safe, "path": str(path.relative_to(config.DATA_DIR)), "rows": int(len(df)),
            "columns": columns, "suggested_mapping": suggest_mapping(columns),
            "sample": [{c: (None if v is None else str(v)) for c, v in zip(columns, row)} for row in sample.values.tolist()],
            "uploaded_at": datetime.now().isoformat(timespec="seconds")}
    _write(UPLOAD_DIR / f"{uid}.json", meta)
    return meta


def get_upload(upload_id: str) -> dict:
    meta = _read(UPLOAD_DIR / f"{re.sub(r'[^A-Za-z0-9]', '', upload_id)}.json", None)
    if not meta:
        raise ValueError(f"No upload {upload_id}")
    return meta


def sources() -> list[dict]:
    return _read(SOURCES_FILE, [])


def add_source(upload_id: str, mapping: dict[str, str]) -> dict:
    meta = get_upload(upload_id)
    missing = [k for k in ("order", "posting_date", "description") if k not in set(mapping.values())]
    if missing:
        raise ValueError("Map these required fields first: " + ", ".join(missing))
    src = [s for s in sources() if s["id"] != upload_id]
    entry = {"id": upload_id, "filename": meta["filename"], "path": meta["path"], "mapping": mapping,
             "rows": meta["rows"], "added_at": datetime.now().isoformat(timespec="seconds")}
    src.append(entry)
    _write(SOURCES_FILE, src)
    return entry


def remove_source(source_id: str) -> bool:
    src = sources()
    keep = [s for s in src if s["id"] != source_id]
    _write(SOURCES_FILE, keep)
    return len(keep) != len(src)


def source_inputs() -> list[dict]:
    """[{path, mapping, name}] for the pipeline, oldest first (newer files win for the same order)."""
    return [{"path": config.DATA_DIR / s["path"], "mapping": s["mapping"], "name": s["filename"]}
            for s in sources() if (config.DATA_DIR / s["path"]).exists()]


def register_demo_source() -> dict:
    """Generate the synthetic rice-plant export (if needed) and register it as a source."""
    from .ingest import read_table, suggest_mapping
    from .synth import generate
    if not config.RAW_EXPORT.exists():
        generate(config.RAW_EXPORT, config.GROUND_TRUTH)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    uid = "demo000000"
    dest = UPLOAD_DIR / f"{uid}_sap_pm_export_demo.xlsx"
    shutil.copyfile(config.RAW_EXPORT, dest)
    cols = [str(c) for c in read_table(dest, nrows=5).columns]
    meta = {"upload_id": uid, "filename": dest.name.split("_", 1)[1], "path": str(dest.relative_to(config.DATA_DIR)),
            "rows": int(len(read_table(dest))), "columns": cols, "suggested_mapping": suggest_mapping(cols),
            "sample": [], "uploaded_at": datetime.now().isoformat(timespec="seconds")}
    _write(UPLOAD_DIR / f"{uid}.json", meta)
    return add_source(uid, meta["suggested_mapping"])


# --- asset discovery ----------------------------------------------------------------------
_NOISE = {"change", "changed", "replace", "replaced", "replacement", "repair", "work", "maintenance", "new", "and",
          "of", "the", "for", "with", "problem", "attended", "pm", "done", "breakdown", "noisy", "noise", "leak",
          "lubrication", "greasing", "round", "tension", "belt", "air", "supply", "guard", "fabrication", "adjust",
          "tightening", "slip", "chng", "replacement", "modification", "broken", "joint", "repl"}


_AREA_WORDS = ["parboiling", "paddy-to-rice", "mill", "milling", "utility", "utl", "packing", "pck", "warehouse",
               "wh", "refurbishment", "boiler house", "plant"]


def _asset_phrase(desc: str) -> str:
    """Strip area prefixes and component/action/symptom words: what's left is usually the equipment name."""
    from .normalize import _area_cues
    norm = normalize_text(desc)
    cues = sorted({c for cs in _area_cues().values() for c in cs} | set(_AREA_WORDS), key=len, reverse=True)
    for _ in range(2):  # 'parboiling-dryer elevator', 'mill - bran room', 'pck packing m/c 1'
        norm = re.sub(r"^(?:" + "|".join(re.escape(c) for c in cues if c) + r")(?:\s*-\s*|\s+)", "", norm).strip()
    norm = re.sub(r"^[^-]{1,25}\s-\s", "", norm)
    for kind in ("component", "action", "symptom", "failure_mode"):
        for _, phrase in match_terms(kind, norm):
            norm = re.sub(r"(?<![\w])" + re.escape(phrase) + r"(?![\w])", " ", norm)
    toks = [t for t in re.findall(r"[a-z0-9.&/]+", norm) if t not in _NOISE and len(t) > 1 or t.isdigit()]
    # SAP short texts are cut at 40 characters: drop a trailing fragment of a noise word ('replacem')
    while toks and len(toks[-1]) >= 3 and any(w.startswith(toks[-1]) and w != toks[-1] for w in _NOISE):
        toks.pop()
    return " ".join(toks[:3])


def discover_assets(upload_id: str, mapping: dict[str, str], limit: int = 40) -> dict:
    """Assets the export mentions that the register does not know yet, with suggested ids, names and areas."""
    from .ingest import build_events, load_export
    meta = get_upload(upload_id)
    df = load_export(config.DATA_DIR / meta["path"], mapping, meta["filename"])
    events, _ = build_events(df)
    resolved = sum(1 for e in events if e.asset_id and e.asset_confidence >= 0.6)
    groups: dict[str, dict] = defaultdict(lambda: {"count": 0, "examples": [], "phrases": Counter(), "fl": None})
    for e in events:
        if e.asset_id and e.asset_confidence >= 0.6:
            continue
        fl = str(e.header.get("functional_location") or "").strip()
        phrase = _asset_phrase(e.description)
        key = f"FL:{fl.upper()}" if fl else f"TX:{phrase}"
        if key == "TX:":
            continue
        g = groups[key]
        g["count"] += 1
        g["fl"] = fl or None
        g["phrases"][phrase] += 1
        if len(g["examples"]) < 3:
            g["examples"].append(e.description)
    # an equipment ID group absorbs description-only groups that use the same name
    phrase_to_fl = {}
    for key, g in groups.items():
        if key.startswith("FL:"):
            for p in g["phrases"]:
                phrase_to_fl.setdefault(p, key)
    for key in [k for k in groups if k.startswith("TX:")]:
        target = phrase_to_fl.get(key[3:])
        if target:
            g = groups.pop(key)
            groups[target]["count"] += g["count"]
            groups[target]["phrases"].update(g["phrases"])
            groups[target]["examples"] = (groups[target]["examples"] + g["examples"])[:3]
    candidates = []
    for key, g in sorted(groups.items(), key=lambda kv: -kv[1]["count"])[:limit]:
        phrase = g["phrases"].most_common(1)[0][0] if g["phrases"] else ""
        name = phrase.title() if phrase else (g["fl"] or "")
        area = detect_area(normalize_text(" ".join(g["examples"])))
        if not area and g["fl"] and "-" in g["fl"]:
            area = g["fl"].split("-")[0].upper()
        candidates.append({"asset_id": (g["fl"] or slug(name)).upper(), "name": name or g["fl"], "area": area or "GEN",
                           "asset_class": "unclassified", "aliases": [p for p in g["phrases"] if p][:4],
                           "work_orders": g["count"], "examples": g["examples"], "from": "functional location" if g["fl"] else "description"})
    return {"work_orders": len(events), "resolved": resolved, "unresolved": len(events) - resolved,
            "known_assets": len(ASSETS), "areas": sorted(PROCESS_AREAS), "candidates": candidates}
