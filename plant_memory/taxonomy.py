"""Controlled vocabulary for the Plant Memory ontology.

Failure modes and mechanisms follow the spirit of ISO 14224 (failure mechanism
codes in brackets), extended for rice processing equipment. Every term has an
id, a label, and synonyms used by the rule-based extractor and the normalizer.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

COMPONENTS: dict[str, dict] = {
    "bearing": {"label": "Bearing", "synonyms": ["bearing", "brg", "brng", "bearings", "22215", "1212 k", "6309", "6205", "6306"], "default_mode": "bearing_wear"},
    "plummer_block": {"label": "Plummer block", "synonyms": ["plummer block", "plmbr blck", "plumber block", "plummer", "plumber", "pedestal"], "default_mode": "bearing_wear"},
    "bearing_sleeve": {"label": "Adapter sleeve", "synonyms": ["sleeve", "adapter sleeve", "he 317", "he-312", "he-315"], "default_mode": "bearing_wear"},
    "v_belt": {"label": "V-belt", "synonyms": ["v belt", "v-belt", "vbelt", "belt dr", "c-106", "b93", "b134", "b 137"], "default_mode": "belt_wear"},
    "elevator_belt": {"label": "Elevator belt", "synonyms": ["elevator belt", "elev belt", "bucket belt", "elevator belting"], "default_mode": "belt_wear"},
    "conveyor_belt": {"label": "Conveyor belt", "synonyms": ["conveyor belt", "conv belt", "belt conveyor"], "default_mode": "belt_wear"},
    "sprocket": {"label": "Sprocket", "synonyms": ["sprocket", "sprkt", "sprocket wheel"], "default_mode": "sprocket_tooth_wear"},
    "chain": {"label": "Roller chain", "synonyms": ["roller chain", "chain"], "default_mode": "chain_elongation"},
    "chain_coupling": {"label": "Chain coupling", "synonyms": ["chain coupling", "chain cplng"], "default_mode": "coupling_failure"},
    "coupling": {"label": "Coupling", "synonyms": ["coupling", "cplng", "f70-b", "tyre coupling"], "default_mode": "coupling_failure"},
    "shaft": {"label": "Shaft", "synonyms": ["shaft", "shft"], "default_mode": "shaft_fracture"},
    "bucket": {"label": "Elevator bucket", "synonyms": ["bucket", "buckets", "bkt"], "default_mode": "bucket_damage"},
    "screen_deck": {"label": "Screen deck", "synonyms": ["screen", "deck", "sieve", "mesh"], "default_mode": "screen_tear"},
    "cleaning_cup": {"label": "Cleaning cup / ball", "synonyms": ["cup", "cleaning ball", "rubber ball"], "default_mode": "screen_tear"},
    "rubber_roll": {"label": "Husker rubber roll", "synonyms": ["rubber roll", "rubber rolls", "rbr roll", "roll"], "default_mode": "rubber_roll_wear"},
    "whitening_stone": {"label": "Whitening stone", "synonyms": ["stone", "emery", "whitening stone", "grinding stone"], "default_mode": "stone_wear"},
    "polishing_screen": {"label": "Polisher screen", "synonyms": ["polisher screen", "polishing screen", "polish screen"], "default_mode": "screen_tear"},
    "mechanical_seal": {"label": "Mechanical seal", "synonyms": ["mechanical seal", "mech seal", "seal", "gland"], "default_mode": "seal_leakage"},
    "impeller": {"label": "Impeller", "synonyms": ["impeller"], "default_mode": "impeller_erosion"},
    "ejector_valve": {"label": "Ejector valve", "synonyms": ["ejector", "ejector valve", "solenoid", "nozzle"], "default_mode": "valve_sticking"},
    "temperature_sensor": {"label": "Temperature sensor", "synonyms": ["rtd", "pt-100", "pt100", "temperature sensor", "temp sensor", "el t rtd"], "default_mode": "sensor_failure"},
    "motor": {"label": "Motor", "synonyms": ["motor", "mtr"], "default_mode": "motor_overheating"},
    "gearbox": {"label": "Gearbox", "synonyms": ["gearbox", "gear box", "gb", "reducer"], "default_mode": "gear_wear"},
    "carbon_brush": {"label": "Carbon brush", "synonyms": ["carbon brush"], "default_mode": "electrical_fault"},
    "wheel": {"label": "Castor wheel", "synonyms": ["wheel", "castor"], "default_mode": None},
    "electrical_fitting": {"label": "Electrical fitting", "synonyms": ["switch", "socket", "pvc box", "wiring", "electrical"], "default_mode": "electrical_fault"},
}

FAILURE_MODES: dict[str, dict] = {
    "bearing_wear": {"label": "Bearing wear", "iso_mechanism": "2.4 Wear", "synonyms": ["bearing worn", "brg worn", "bearing damaged", "bearing failure", "brg failure", "bearing jam"]},
    "bearing_seizure": {"label": "Bearing seizure", "iso_mechanism": "2.7 Overheating", "synonyms": ["bearing seized", "brg seized", "bearing jammed", "seizure"]},
    "belt_wear": {"label": "Belt wear", "iso_mechanism": "2.4 Wear", "synonyms": ["belt worn", "belt cracked", "belt torn", "belt wear"]},
    "belt_slippage": {"label": "Belt slippage", "iso_mechanism": "1.5 Looseness", "synonyms": ["slip", "slipping", "belt loose", "slippage"]},
    "belt_breakage": {"label": "Belt breakage", "iso_mechanism": "2.5 Breakage", "synonyms": ["belt broken", "belt cut", "belt snapped"]},
    "sprocket_tooth_wear": {"label": "Sprocket tooth wear", "iso_mechanism": "2.4 Wear", "synonyms": ["teeth worn", "tooth worn", "sprocket worn", "teeth broken"]},
    "chain_elongation": {"label": "Chain elongation", "iso_mechanism": "2.4 Wear", "synonyms": ["chain stretched", "chain elongated", "chain jumping"]},
    "coupling_failure": {"label": "Coupling failure", "iso_mechanism": "2.5 Breakage", "synonyms": ["coupling broken", "coupling damaged", "coupling failure"]},
    "shaft_fracture": {"label": "Shaft fracture", "iso_mechanism": "2.6 Fatigue", "synonyms": ["shaft broken", "shaft broke", "shaft crack", "shaft fracture"]},
    "bucket_damage": {"label": "Bucket damage", "iso_mechanism": "1.4 Deformation", "synonyms": ["bucket broken", "bucket damaged", "buckets bent", "bucket fell"]},
    "screen_tear": {"label": "Screen / deck tear", "iso_mechanism": "2.5 Breakage", "synonyms": ["screen torn", "mesh torn", "screen damaged", "deck worn"]},
    "rubber_roll_wear": {"label": "Rubber roll wear", "iso_mechanism": "2.4 Wear", "synonyms": ["roll worn", "rubber roll worn", "roll finished"]},
    "stone_wear": {"label": "Whitening stone wear", "iso_mechanism": "2.4 Wear", "synonyms": ["stone worn", "stone finished", "stone glazed"]},
    "seal_leakage": {"label": "Seal leakage", "iso_mechanism": "1.1 Leakage", "synonyms": ["leak", "leakage", "leaking", "seal leak"]},
    "impeller_erosion": {"label": "Impeller erosion", "iso_mechanism": "2.3 Erosion", "synonyms": ["impeller eroded", "impeller worn"]},
    "valve_sticking": {"label": "Valve sticking", "iso_mechanism": "1.6 Sticking", "synonyms": ["ejector stuck", "valve stuck", "not ejecting"]},
    "sensor_failure": {"label": "Sensor failure", "iso_mechanism": "3.x Instrument failure", "synonyms": ["sensor faulty", "rtd faulty", "wrong reading", "sensor failure"]},
    "motor_overheating": {"label": "Motor overheating", "iso_mechanism": "2.7 Overheating", "synonyms": ["motor heating", "motor hot", "motor tripping"]},
    "gear_wear": {"label": "Gear wear", "iso_mechanism": "2.4 Wear", "synonyms": ["gear worn", "gear teeth worn"]},
    "electrical_fault": {"label": "Electrical fault", "iso_mechanism": "4.x Electrical failure", "synonyms": ["short circuit", "electrical fault", "no supply"]},
}

ROOT_CAUSES: dict[str, dict] = {
    "misalignment": {"label": "Misalignment", "category": "Installation / maintenance", "synonyms": ["misalignment", "mis-alignment", "alignment", "out of line", "not aligned", "alignment off", "alignment out"]},
    "inadequate_lubrication": {"label": "Inadequate lubrication", "category": "Maintenance practice", "synonyms": ["lubrication", "greasing", "grease", "no grease", "dry running bearing", "lube", "grease nahi"]},
    "dust_ingress": {"label": "Dust ingress", "category": "Environment", "synonyms": ["dust", "husk dust", "bran dust", "dirt"]},
    "moisture_ingress": {"label": "Moisture / steam ingress", "category": "Process", "synonyms": ["moisture", "water ingress", "steam", "wet paddy", "condensate"]},
    "normal_wear": {"label": "Normal wear (end of life)", "category": "Age", "synonyms": ["wear and tear", "wear & tear", "life over", "worn out", "normal wear", "aging", "old"]},
    "improper_tensioning": {"label": "Improper belt tension", "category": "Maintenance practice", "synonyms": ["tension", "loose belt", "belt loose", "over tension", "tensioning"]},
    "overload": {"label": "Overload / choke-up", "category": "Operational", "synonyms": ["overload", "choke", "choking", "jam", "excess feed"]},
    "foreign_material": {"label": "Foreign material", "category": "Process", "synonyms": ["stone", "foreign material", "metal piece", "bolt fell", "foreign body", "iron piece"]},
    "material_defect": {"label": "Poor-quality spare", "category": "Material quality", "synonyms": ["local make", "poor quality", "defective", "duplicate", "manufacturing defect"]},
    "improper_installation": {"label": "Improper installation", "category": "Installation / maintenance", "synonyms": ["improper installation", "not tightened", "wrong fitment", "loose fitting", "fitment"]},
    "operator_error": {"label": "Operator setting error", "category": "Operational", "synonyms": ["operator", "wrong setting", "roll gap", "setting wrong"]},
    "cavitation": {"label": "Cavitation / dry running", "category": "Process", "synonyms": ["cavitation", "dry run", "dry running", "suction"]},
    "electrical_supply": {"label": "Electrical supply issue", "category": "Electrical", "synonyms": ["voltage", "single phasing", "supply fluctuation", "power fluctuation"]},
}

ACTIONS: dict[str, dict] = {
    "replace": {"label": "Replace", "synonyms": ["replace", "replaced", "replacement", "change", "changed", "chng", "chg", "new fitted", "fitted new"]},
    "repair": {"label": "Repair", "synonyms": ["repair", "repaired", "rectified", "rectify"]},
    "weld_repair": {"label": "Weld repair", "synonyms": ["weld", "welding", "welded", "electrode"]},
    "realign": {"label": "Realign", "synonyms": ["aligned", "alignment done", "realign", "alignment corrected"]},
    "lubricate": {"label": "Lubricate", "synonyms": ["greased", "greasing done", "lubricated", "oiling"]},
    "adjust": {"label": "Adjust / retension", "synonyms": ["tightened", "adjusted", "tension adjusted", "retension", "gap set"]},
    "clean": {"label": "Clean", "synonyms": ["cleaned", "cleaning", "blow"]},
    "refurbish": {"label": "Refurbish / overhaul", "synonyms": ["refurb", "refurbish", "refurbished", "overhaul", "overhauled"]},
    "modify": {"label": "Modify / fabricate", "synonyms": ["modification", "modificatn", "modify", "fabricate", "fabrication", "prepare", "new"]},
    "inspect": {"label": "Inspect", "synonyms": ["inspection", "checked", "inspect", "maintenance"]},
}

SYMPTOMS: dict[str, dict] = {
    "abnormal_noise": {"label": "Abnormal noise", "synonyms": ["noise", "noisy", "sound", "abnormal sound", "awaz"]},
    "vibration": {"label": "High vibration", "synonyms": ["vibration", "vibrating", "shaking"]},
    "overheating": {"label": "Overheating", "synonyms": ["heating", "hot", "overheat", "temperature high"]},
    "belt_slip": {"label": "Belt slipping", "synonyms": ["slip", "slipping", "belt slip"]},
    "stopped": {"label": "Stopped / breakdown", "synonyms": ["breakdown", "brkdn", "b/d", "stopped", "stuck", "not running", "jam"]},
    "leakage": {"label": "Leakage", "synonyms": ["leak", "leakage", "leaking", "dripping"]},
    "reduced_output": {"label": "Reduced output", "synonyms": ["low output", "capacity drop", "low efficiency", "less husking", "output low"]},
    "quality_issue": {"label": "Product quality issue", "synonyms": ["broken rice", "brokens", "quality", "colour reject high", "high bran"]},
    "tripping": {"label": "Motor tripping", "synonyms": ["trip", "tripping", "tripped"]},
}

VOCAB = {
    "component": COMPONENTS,
    "failure_mode": FAILURE_MODES,
    "root_cause": ROOT_CAUSES,
    "action": ACTIONS,
    "symptom": SYMPTOMS,
}

# Abbreviations used in SAP short texts at the plant.
ABBREVIATIONS = {
    r"\bparb\b\.?": "parboiling",
    r"\bparboilin\b": "parboiling",
    r"\bp2r\b": "paddy-to-rice",
    r"\bbrg\b": "bearing",
    r"\bbrng\b": "bearing",
    r"\bchng\b": "change",
    r"\bchg\b": "change",
    r"\bplmbr\b": "plummer",
    r"\bblck\b": "block",
    r"\belev\b": "elevator",
    r"\belv\b": "elevator",
    r"\bconv\b": "conveyor",
    r"\bmodificatn\b": "modification",
    r"\bbrkdn\b": "breakdown",
    r"\bb/d\b": "breakdown",
    r"\bmtr\b": "motor",
    r"\bcplng\b": "coupling",
    r"\bsprkt\b": "sprocket",
    r"\brbr\b": "rubber",
    r"\bbkt\b": "bucket",
    r"\bmaint\b": "maintenance",
    r"\brefurb\b": "refurbishment",
    r"\bwhtnr\b": "whitener",
    r"\bplshr\b": "polisher",
}


# --- approved ontology extensions (written by drift decisions) ---------------
EXTENSIONS_FILE = Path(__file__).resolve().parents[1] / "ontology" / "extensions.json"


def load_extensions() -> dict:
    if EXTENSIONS_FILE.exists():
        return json.loads(EXTENSIONS_FILE.read_text(encoding="utf-8"))
    return {"terms": {}, "synonyms": {}}


def save_extensions(ext: dict) -> None:
    EXTENSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXTENSIONS_FILE.write_text(json.dumps(ext, indent=2), encoding="utf-8")


def _apply_extensions() -> None:
    ext = load_extensions()
    for kind, terms in ext.get("terms", {}).items():
        for term_id, spec in terms.items():
            VOCAB[kind][term_id] = {**spec, "extension": True}
    for kind, terms in ext.get("synonyms", {}).items():
        for term_id, syns in terms.items():
            if term_id in VOCAB[kind]:
                VOCAB[kind][term_id]["synonyms"] = list(dict.fromkeys(VOCAB[kind][term_id]["synonyms"] + syns))


_BASE_VOCAB = {kind: {k: dict(v, synonyms=list(v.get("synonyms", []))) for k, v in terms.items()}
               for kind, terms in VOCAB.items()}
_apply_extensions()


def reload_extensions() -> None:
    """Re-read ontology/extensions.json after an edit (UI or drift decision)."""
    for kind, terms in VOCAB.items():
        terms.clear()
        terms.update({k: dict(v, synonyms=list(v["synonyms"])) for k, v in _BASE_VOCAB[kind].items()})
    _apply_extensions()
    norm = sys.modules.get(__package__ + ".normalize")
    if norm is not None and hasattr(norm, "clear_caches"):
        norm.clear_caches()


def label(kind: str, term_id: str | None) -> str | None:
    if term_id is None:
        return None
    return VOCAB[kind].get(term_id, {}).get("label", term_id)


# --- editing (UI / API). Built-in terms are fixed; workspace additions live in extensions.json ---
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


def add_term(kind: str, label_text: str, synonyms: list[str] | None = None, term_id: str | None = None,
             components: list[str] | None = None, category: str | None = None) -> dict:
    if kind not in VOCAB:
        raise ValueError(f"Unknown kind '{kind}'")
    tid = term_id or _slug(label_text)
    if not tid:
        raise ValueError("A term needs a label")
    if tid in VOCAB[kind] and not VOCAB[kind][tid].get("extension"):
        raise ValueError(f"'{tid}' is a built-in {kind}; add synonyms to it instead")
    spec = {"label": label_text.strip(), "synonyms": [s.strip() for s in (synonyms or []) if s.strip()]}
    if kind == "failure_mode":
        spec.update({"iso_mechanism": "to be classified", "components": components or []})
    if kind == "root_cause":
        spec["category"] = category or "Unclassified"
    if kind == "component":
        spec["default_mode"] = None
    ext = load_extensions()
    ext.setdefault("terms", {}).setdefault(kind, {})[tid] = spec
    save_extensions(ext)
    reload_extensions()
    return {"kind": kind, "id": tid, **spec}


def add_synonym(kind: str, term_id: str, synonym: str) -> dict:
    if term_id not in VOCAB.get(kind, {}):
        raise ValueError(f"Unknown {kind} '{term_id}'")
    ext = load_extensions()
    if term_id in ext.get("terms", {}).get(kind, {}):
        ext["terms"][kind][term_id]["synonyms"] = list(dict.fromkeys(ext["terms"][kind][term_id]["synonyms"] + [synonym.strip()]))
    else:
        lst = ext.setdefault("synonyms", {}).setdefault(kind, {}).setdefault(term_id, [])
        if synonym.strip() not in lst:
            lst.append(synonym.strip())
    save_extensions(ext)
    reload_extensions()
    return {"kind": kind, "id": term_id, "synonyms": VOCAB[kind][term_id]["synonyms"]}


def remove_extension(kind: str, term_id: str, synonym: str | None = None) -> bool:
    """Delete a workspace-added term, or a workspace-added synonym. Built-in vocabulary cannot be deleted."""
    ext = load_extensions()
    changed = False
    if synonym is None:
        changed = ext.get("terms", {}).get(kind, {}).pop(term_id, None) is not None
    else:
        term = ext.get("terms", {}).get(kind, {}).get(term_id)
        if term and synonym in term["synonyms"]:
            term["synonyms"].remove(synonym)
            changed = True
        lst = ext.get("synonyms", {}).get(kind, {}).get(term_id, [])
        if synonym in lst:
            lst.remove(synonym)
            changed = True
    if changed:
        save_extensions(ext)
        reload_extensions()
    return changed


def is_builtin_synonym(kind: str, term_id: str, synonym: str) -> bool:
    return synonym in _BASE_VOCAB.get(kind, {}).get(term_id, {}).get("synonyms", [])
