"""Text normalization and asset resolution for SAP short texts."""
from __future__ import annotations

import difflib
import re
from functools import lru_cache

from .catalog import ASSETS, PROCESS_AREAS
from .taxonomy import ABBREVIATIONS, VOCAB

AREA_CUES = {
    "PARB": ["parboiling"],
    "P2R": ["paddy-to-rice", "mill"],
    "UTL": ["utility", "boiler", "utl", "dm"],
    "PCK": ["packing", "pck"],
    "WH": ["warehouse", "wh"],
}


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    s = str(text).lower()
    s = re.sub(r"\bp2r\b", "paddy-to-rice", s)        # expand before letter/digit splitting
    s = re.sub(r"([a-z])(\d)", r"\1 \2", s)          # elev1 -> elev 1
    s = s.replace("&", " & ").replace("(", " ").replace(")", " ").replace(",", " ")
    for pat, rep in ABBREVIATIONS.items():
        s = re.sub(pat, rep, s)
    s = re.sub(r"\s*-\s+|\s+-\s*", " - ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


@lru_cache(maxsize=None)
def _alias_patterns():
    pats = []
    for aid, a in ASSETS.items():
        for alias in a["aliases"] + [a["name"]]:
            na = normalize_text(alias)
            if na:
                pats.append((aid, na, re.compile(r"(?<![\w])" + re.escape(na) + r"(?![\w])")))
    pats.sort(key=lambda p: -len(p[1]))  # longest alias first
    return pats


@lru_cache(maxsize=None)
def _area_cues() -> dict[str, list[str]]:
    """Static cues for the demo plant plus each workspace area's code, label and text prefixes."""
    cues = {k: [normalize_text(c) for c in v] for k, v in AREA_CUES.items() if k in PROCESS_AREAS}
    for code, area in PROCESS_AREAS.items():
        extra = [code.lower(), normalize_text(area.get("label", ""))]
        extra += [normalize_text(p).strip(" -") for p in area.get("text_prefixes", [])]
        cues.setdefault(code, [])
        cues[code] += [c for c in extra if c and c not in cues[code]]
    return cues


def detect_area(norm: str) -> str | None:
    for area, cues in _area_cues().items():
        if any(re.search(r"(?<![\w])" + re.escape(c) + r"(?![\w])", norm) for c in cues):
            return area
    return None


def clear_caches() -> None:
    for fn in (_alias_patterns, _vocab_patterns, _area_cues):
        fn.cache_clear()


def resolve_asset(functional_location: str | None, description: str | None) -> tuple[str | None, float, str]:
    """Return (asset_id, confidence, method)."""
    fl = (functional_location or "").strip().upper()
    if fl in ASSETS:
        return fl, 0.98, "functional_location"
    if not ASSETS:
        return None, 0.0, "unresolved"
    norm = normalize_text(description)
    area = detect_area(norm)
    matches = []
    for aid, alias, pat in _alias_patterns():
        if pat.search(norm):
            matches.append((aid, alias))
    if matches:
        best_len = len(matches[0][1])
        top = {aid for aid, al in matches if len(al) == best_len}
        if area:
            in_area = {aid for aid in top if ASSETS.get(aid, {}).get("area") == area}
            top = in_area or top
        if len(top) == 1:
            return top.pop(), 0.85 if area else 0.8, "alias_match"
        return sorted(top)[0], 0.5, "alias_ambiguous"
    # fuzzy fallback on aliases
    names = {normalize_text(al): aid for aid, a in ASSETS.items() for al in a["aliases"]}
    cand = difflib.get_close_matches(norm[:30], list(names), n=1, cutoff=0.6)
    if cand:
        return names[cand[0]], 0.45, "fuzzy"
    return None, 0.0, "unresolved"


@lru_cache(maxsize=None)
def _vocab_patterns(kind: str):
    pats = []
    for term_id, spec in VOCAB[kind].items():
        for syn in spec["synonyms"] + [spec["label"]]:
            ns = normalize_text(syn)
            if ns:
                pats.append((term_id, ns, re.compile(r"(?<![\w])" + re.escape(ns) + r"(?![\w])")))
    pats.sort(key=lambda p: -len(p[1]))
    return pats


def match_terms(kind: str, text: str | None) -> list[tuple[str, str]]:
    """All (term_id, matched_phrase) for a vocabulary kind, longest phrases first, deduplicated."""
    norm = normalize_text(text)
    if not norm:
        return []
    found, seen = [], set()
    for term_id, phrase, pat in _vocab_patterns(kind):
        if term_id not in seen and pat.search(norm):
            found.append((term_id, phrase))
            seen.add(term_id)
    return found


def map_term(kind: str, text: str | None) -> str | None:
    m = match_terms(kind, text)
    return m[0][0] if m else None
