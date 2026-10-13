"""Deterministic extractor: structured columns > technician remarks > short text > materials.

Always runs. It is the fallback when no LLM key is set and the second opinion
when one is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .catalog import ASSETS

from .ingest import Event
from .normalize import map_term, match_terms, normalize_text
from .taxonomy import COMPONENTS, FAILURE_MODES

# Which components a failure mode can plausibly apply to (recomputed so taxonomy edits apply at once)
_FM_EXTRA = {
    "belt_slippage": {"v_belt", "elevator_belt", "conveyor_belt"},
    "belt_breakage": {"v_belt", "elevator_belt", "conveyor_belt"},
    "bearing_seizure": {"bearing", "plummer_block"},
    "bearing_wear": {"bearing", "plummer_block", "bearing_sleeve"},
    "coupling_failure": {"coupling", "chain_coupling"},
    "electrical_fault": {"electrical_fitting", "motor", "carbon_brush", "ejector_valve"},
    "screen_tear": {"screen_deck", "polishing_screen", "cleaning_cup"},
    "chain_elongation": {"chain", "sprocket"},
}


def fm_components() -> dict[str, set]:
    out: dict[str, set] = {}
    for c, spec in COMPONENTS.items():
        if spec.get("default_mode"):
            out.setdefault(spec["default_mode"], set()).add(c)
    for fm, cs in _FM_EXTRA.items():
        out.setdefault(fm, set()).update(cs)
    for fm, spec in FAILURE_MODES.items():  # extension terms declare their components
        out.setdefault(fm, set()).update(spec.get("components", []))
    return out


NON_FAILURE_ACTIONS = {"lubricate", "modify", "inspect"}
# components that are consumables of a bigger fix; don't pick them as primary when something else is present
SECONDARY = {"bearing_sleeve", "cleaning_cup", "electrical_fitting"}


@dataclass
class Value:
    term: str
    confidence: float
    method: str
    field: str
    snippet: str


@dataclass
class Extraction:
    event_id: str
    is_failure: bool = True
    values: dict[str, Value] = field(default_factory=dict)
    drift: list[tuple[str, str, str]] = field(default_factory=list)  # (kind, phrase, field)
    notes: list[str] = field(default_factory=list)


def _structured(ex: Extraction, kind: str, raw, fieldname: str) -> None:
    if not raw:
        return
    term = map_term(kind, str(raw))
    if term:
        ex.values[kind] = Value(term, 0.95, "structured", fieldname, str(raw))
    else:
        ex.drift.append((kind, normalize_text(str(raw)), fieldname))


def _strip_asset(desc: str, asset_id: str | None) -> str:
    """Remove the asset's name and aliases so 'Hodi belt conv' is not read as a conveyor-belt component."""
    norm = normalize_text(desc)
    if asset_id and asset_id in ASSETS:
        for alias in sorted(ASSETS[asset_id]["aliases"] + [ASSETS[asset_id]["name"]], key=len, reverse=True):
            na = normalize_text(alias)
            norm = re.sub(r"(?<![\w])" + re.escape(na) + r"(?![\w])", " ", norm)
    return re.sub(r"\s+", " ", norm).strip()


def extract(ev: Event) -> Extraction:
    ex = Extraction(ev.event_id)
    h = ev.header
    desc, remarks = ev.description or "", str(h.get("remarks") or "")

    # 1. structured columns entered in SAP
    _structured(ex, "symptom", h.get("symptom"), "Symptom")
    _structured(ex, "root_cause", h.get("root_cause"), "Root Cause")
    _structured(ex, "action", h.get("action_taken"), "Action Taken")

    # 2. action from the short text
    if "action" not in ex.values:
        m = match_terms("action", desc)
        if m:
            ex.values["action"] = Value(m[0][0], 0.7, "rules", "Work Description", desc)

    # 3. failure vs. planned/non-failure work
    action = ex.values.get("action").term if "action" in ex.values else None
    wt = (ev.work_type or "").lower()
    if wt == "modification" or (action in NON_FAILURE_ACTIONS and "symptom" not in ex.values):
        ex.is_failure = False

    # 4. component: short text (minus the asset's own name), cross-checked with the materials issued
    desc_core = _strip_asset(desc, ev.asset_id)
    line_comps = [l["component"] for l in sorted(ev.lines, key=lambda l: -(l["cost"] or 0)) if l["component"]]
    desc_comps = [c for c, _ in match_terms("component", desc_core)]
    primary_lines = [c for c in line_comps if c not in SECONDARY] or line_comps
    if desc_comps and desc_comps[0] in line_comps:
        ex.values["component"] = Value(desc_comps[0], 0.92, "rules", "Work Description + Material", desc)
    elif desc_comps:
        ex.values["component"] = Value(desc_comps[0], 0.75, "rules", "Work Description", desc)
    elif primary_lines:
        mat = next(l for l in ev.lines if l["component"] == primary_lines[0])
        ex.values["component"] = Value(primary_lines[0], 0.75, "rules", "Material", mat["material_description"] or "")

    # 5. symptom from text if no column
    if "symptom" not in ex.values:
        for fieldname, text in (("Work Description", desc), ("Technician Remarks", remarks)):
            m = match_terms("symptom", text)
            if m:
                ex.values["symptom"] = Value(m[0][0], 0.7 if fieldname == "Work Description" else 0.65, "rules", fieldname, text)
                break

    # 6. root cause from technician remarks (never from the short text: 'stone' etc. are components there)
    if "root_cause" not in ex.values and remarks:
        m = match_terms("root_cause", remarks)
        if m:
            ex.values["root_cause"] = Value(m[0][0], 0.75, "rules", "Technician Remarks", remarks)

    # 7. failure mode: explicit phrase compatible with the component, else the component's default
    if ex.is_failure:
        comp = ex.values["component"].term if "component" in ex.values else None
        fmc = fm_components()
        explicit = []
        for fieldname, text in (("Work Description", desc_core), ("Technician Remarks", remarks), ("Symptom", str(h.get("symptom") or ""))):
            explicit += [(t, p, fieldname, text) for t, p in match_terms("failure_mode", text)]
        if "symptom" in ex.values and ex.values["symptom"].term == "belt_slip":
            explicit.append(("belt_slippage", "slip", "Symptom", ex.values["symptom"].snippet))
        fit = [e for e in explicit if comp is None or comp in fmc.get(e[0], set())]
        if fit:
            t, _, fieldname, text = fit[0]
            ex.values["failure_mode"] = Value(t, 0.78, "rules", fieldname, text)
        elif explicit and comp:
            # evidence of a failure the taxonomy has no mode for on this component -> drift
            t, phrase, fieldname, text = explicit[0]
            mode_word = FAILURE_MODES[t]["label"].split()[-1].lower()  # seal leakage -> leakage
            ex.drift.append(("failure_mode", f"{COMPONENTS.get(comp, {}).get('label', comp).lower()} {mode_word}", fieldname))
            ex.notes.append(f"'{phrase}' does not fit component {comp}")
        elif comp and COMPONENTS.get(comp, {}).get("default_mode"):
            ex.values["failure_mode"] = Value(COMPONENTS[comp]["default_mode"], 0.55, "inferred",
                                              "Inferred from component", COMPONENTS[comp]["label"])
    return ex
