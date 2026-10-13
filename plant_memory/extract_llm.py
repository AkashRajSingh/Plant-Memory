"""LLM extractor for any OpenAI-compatible API: Groq (default), another provider, or a local model (Ollama).

Events are sent in batches, outputs are constrained to taxonomy ids, and every
response is cached on disk so re-runs do not spend the daily request quota.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx

from .config import LLM_CACHE
from .ingest import Event
from .taxonomy import VOCAB
from .workspace import get_settings, llm_target

PROMPT_VERSION = "v1"
_active = {"model": "", "base_url": ""}  # target of the current extract_batch run

SYSTEM = """You are a maintenance reliability engineer at a parboiled rice plant. You read SAP PM work orders
and map each one onto a fixed maintenance ontology. Short texts are abbreviated and messy
(Parb = parboiling, P2R = paddy-to-rice milling, brg = bearing, chng = change, B/D = breakdown).

For every event return:
- component, failure_mode, root_cause, action, symptom: one id from the allowed lists, or null.
- If the text clearly states something that has NO matching id, return "other:<short phrase>" for that field.
- root_cause: only when the Root Cause column or the technician remarks state or strongly imply it.
  Never guess a root cause from the component alone. null is the right answer when there is no evidence.
- is_failure: false for lubrication rounds, modifications, fabrication and other planned non-failure work.
- confidence: 0..1 per field, how sure you are given the evidence.
- evidence: per field, the exact words you relied on.

Allowed ids:
{vocab}

Reply with JSON only: {{"events": [{{"id": "...", "is_failure": true, "component": ..., "failure_mode": ...,
"root_cause": ..., "action": ..., "symptom": ..., "confidence": {{"component": 0.9, ...}},
"evidence": {{"component": "...", ...}}}}]}}"""


def _vocab_block() -> str:
    out = []
    for kind, terms in VOCAB.items():
        out.append(f"{kind}: " + ", ".join(f"{k} ({v['label']})" for k, v in terms.items()))
    return "\n".join(out)


def available() -> bool:
    return llm_target()["enabled"]


def target_label() -> str:
    return llm_target()["label"]


def _event_payload(ev: Event) -> dict:
    h = ev.header
    return {
        "id": ev.event_id, "work_description": ev.description, "work_type": ev.work_type,
        "materials": sorted({l["material_description"] for l in ev.lines if l["material_description"]}),
        "symptom_column": h.get("symptom"), "root_cause_column": h.get("root_cause"),
        "action_taken_column": h.get("action_taken"), "breakdown": h.get("breakdown"),
        "technician_remarks": h.get("remarks"),
    }


def _key(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str) + PROMPT_VERSION + _active["model"]
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _load_cache(path: Path) -> dict:
    cache = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                cache[rec["key"]] = rec["result"]
            except (json.JSONDecodeError, KeyError):
                continue
    return cache


def _append_cache(path: Path, items: list[tuple[str, dict]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for key, result in items:
            f.write(json.dumps({"key": key, "result": result}) + "\n")


def _call(batch: list[dict], client: httpx.Client, max_retries: int = 8) -> list[dict]:
    body = {
        "model": _active["model"],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        **({"reasoning_effort": "low"} if "gpt-oss" in _active["model"] else {}),  # fewer tokens against the free-tier limit
        "messages": [
            {"role": "system", "content": SYSTEM.format(vocab=_vocab_block())},
            {"role": "user", "content": json.dumps({"events": batch}, ensure_ascii=False, default=str)},
        ],
    }
    bad_json = 0
    for attempt in range(max_retries):
        r = client.post(f"{_active['base_url']}/chat/completions", json=body)
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get("retry-after", 0) or 0) or min(60, 4 * 2 ** attempt)
            time.sleep(wait)
            continue
        if r.status_code == 400 and "json_validate_failed" in r.text and bad_json < 1:
            bad_json += 1  # the model produced malformed JSON; one retry, then the caller splits the batch
            continue
        if not r.is_success:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
        content = r.json()["choices"][0]["message"]["content"]
        try:
            return _events_from(json.loads(content))
        except json.JSONDecodeError as exc:
            raise LLMError(f"model returned invalid JSON: {exc}") from None
    raise LLMError("LLM rate limit: retries exhausted")


class LLMError(RuntimeError):
    pass


def _events_from(data) -> list[dict]:
    """Accept {"events": [...]}, a bare list, a single event object, or any dict holding one list of events."""
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("events"), list):
            return [e for e in data["events"] if isinstance(e, dict)]
        if "id" in data:
            return [data]
        lists = [v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        if len(lists) == 1:
            return lists[0]
    raise LLMError(f"unexpected JSON shape from model: {str(data)[:120]}")


def _call_split(batch: list[dict], client: httpx.Client, progress=None) -> list[dict]:
    """Call the LLM; if a batch fails on bad output, split it in half and try the halves."""
    try:
        return _call(batch, client)
    except LLMError as exc:
        if len(batch) == 1 or "rate limit" in str(exc) or "HTTP 4" in str(exc) and "json_validate_failed" not in str(exc):
            if progress:
                progress(f"LLM failed for {len(batch)} event(s) ({exc}); using rules for them")
            return []
        mid = len(batch) // 2
        return _call_split(batch[:mid], client, progress) + _call_split(batch[mid:], client, progress)


def extract_batch(events: list[Event], progress=None) -> dict[str, dict]:
    """Return {event_id: llm_result}. Missing ids mean the LLM gave nothing usable for that event."""
    target = llm_target()
    if not target["enabled"]:
        return {}
    _active.update(model=target["model"], base_url=target["base_url"])
    batch_size = int(get_settings().get("batch_size") or 8)
    cache = _load_cache(LLM_CACHE)
    results, todo = {}, []
    for ev in events:
        payload = _event_payload(ev)
        key = _key(payload)
        if key in cache:
            results[ev.event_id] = cache[key]
        else:
            todo.append((ev.event_id, key, payload))
    headers = {"Authorization": f"Bearer {target['api_key']}"}
    with httpx.Client(timeout=90, headers=headers) as client:
        for i in range(0, len(todo), batch_size):
            chunk = todo[i:i + batch_size]
            try:
                out = _call_split([p for _, _, p in chunk], client, progress)
            except Exception as exc:  # network or anything unexpected: never abort a long build; rules cover this chunk
                if progress:
                    progress(f"LLM batch failed ({exc.__class__.__name__}: {exc}); using rules for {len(chunk)} events")
                continue
            by_id = {str(o.get("id")): o for o in out if isinstance(o, dict)}
            fresh = []
            for eid, key, _ in chunk:
                if eid in by_id:
                    results[eid] = by_id[eid]
                    fresh.append((key, by_id[eid]))
            _append_cache(LLM_CACHE, fresh)
            if progress:
                progress(f"LLM extracted {min(i + batch_size, len(todo))}/{len(todo)} new events")
    return results
