"""Asset register and materials master.

The defaults below describe the synthetic rice processing plant used for the demo.

In a real deployment this comes from SAP functional locations (IH06) and the
material master (MM03). Here it is hand-built to resemble an FMCG parboiled
rice unit.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

DEFAULT_PROCESS_AREAS = {
    "PARB": {"label": "Parboiling", "text_prefixes": ["Parb-", "Parb.-", "Parb.- ", "PARB ", "Parboiling-"]},
    "P2R": {"label": "Paddy-to-Rice (milling)", "text_prefixes": ["P2R-", "P2R ", "P2R- ", "Mill-"]},
    "UTL": {"label": "Utilities", "text_prefixes": ["UTL-", "Utility-", "Boiler-"]},
    "PCK": {"label": "Packing", "text_prefixes": ["PCK-", "Packing-"]},
    "WH": {"label": "Warehouse", "text_prefixes": ["Warehouse-", "WH-"]},
}

# asset_id: name, area, class, aliases as they appear in short texts
DEFAULT_ASSETS = {
    "PARB-PCL-01": {"name": "Paddy cleaner", "area": "PARB", "class": "vibro_screen", "aliases": ["Paddy cleaner", "paddy clnr", "Pre cleaner"]},
    "PARB-HODI-ELV-01": {"name": "Hodi elevator", "area": "PARB", "class": "bucket_elevator", "aliases": ["Hodi Elevator", "Hodi Elev", "Hodi elv"]},
    "PARB-ELV-01": {"name": "Elevator 1 (parboiling)", "area": "PARB", "class": "bucket_elevator", "aliases": ["Elevator1", "Elev1", "Elevator 1", "Elev 1"]},
    "PARB-ELV-02": {"name": "Elevator 2 (parboiling)", "area": "PARB", "class": "bucket_elevator", "aliases": ["Elevator2", "Elev2", "Elevator 2", "Elev 2"]},
    "PARB-DRY-ELV-01": {"name": "Dryer elevator", "area": "PARB", "class": "bucket_elevator", "aliases": ["Dryer elevator", "Dryer Elev", "Drier elevator", "LSU elev"]},
    "PARB-HODI-CNV-01": {"name": "Hodi conveyor", "area": "PARB", "class": "belt_conveyor", "aliases": ["Hodi Conveyor", "Hodi conv", "Hodi belt conv"]},
    "PARB-DRYER-01": {"name": "LSU dryer", "area": "PARB", "class": "dryer", "aliases": ["LSU dryer", "Dryer", "LSU"]},
    "P2R-HSK-01": {"name": "Husker No.1", "area": "P2R", "class": "husker", "aliases": ["Husker No.1", "Husker 1", "Husker no 1", "Huller 1"]},
    "P2R-HSK-02": {"name": "Husker No.2", "area": "P2R", "class": "husker", "aliases": ["Husker No.2", "Husker 2", "Husker no 2", "Huller 2"]},
    "P2R-ELV-12": {"name": "Elevator 12 (milling)", "area": "P2R", "class": "bucket_elevator", "aliases": ["Elevator 12", "ELevator 12", "Elev 12", "Elev12"]},
    "P2R-WHT-01": {"name": "Whitener 1", "area": "P2R", "class": "whitener", "aliases": ["Whitener 1", "Whtnr 1", "Whitener no.1"]},
    "P2R-PLS-01": {"name": "Polisher 1", "area": "P2R", "class": "polisher", "aliases": ["Polisher 1", "Plshr 1", "Silky polisher"]},
    "P2R-SRT-01": {"name": "Colour sorter", "area": "P2R", "class": "color_sorter", "aliases": ["Sortex", "Colour sorter", "Color sorter"]},
    "P2R-BRN-CNV-01": {"name": "Bran room screw conveyor", "area": "P2R", "class": "belt_conveyor", "aliases": ["BRAN ROOM", "Bran room conv", "Bran conveyor"]},
    "UTL-DM-PMP-01": {"name": "DM inlet pump", "area": "UTL", "class": "pump", "aliases": ["DM INLET PUMP", "DM pump", "DM inlet pmp"]},
    "UTL-BFP-01": {"name": "Boiler feed pump", "area": "UTL", "class": "pump", "aliases": ["Boiler feed pump", "BFP", "Feed pump"]},
    "PCK-PKM-01": {"name": "Packing machine 1", "area": "PCK", "class": "packing_machine", "aliases": ["Packing m/c 1", "Packing machine 1", "FFS 1"]},
    "WH-TRL-01": {"name": "Rice loading trolleys", "area": "WH", "class": "trolley", "aliases": ["rice loading trolley", "hand trolley", "trolley"]},
}

# Materials master: code, description (SAP style), unit cost in Rs, UoM, component
DEFAULT_MATERIALS = {
    "1683942313": ("SCREEN;M:SI 70,FOWLER WES;VBRTNG,5.5MM", 29080.00, "EA", "screen_deck"),
    "1683942343": ("CUP;M:S.AS.SLM.708.AFG,FOWLER WES", 1837.50, "EA", "cleaning_cup"),
    "5545012833": ("WHEEL;6IN,1.5IN,PU,RBR,CAP:100-500KG", 265.00, "EA", "wheel"),
    "5801100093": ("BRG BALL MTR,RAD,63092Z,45MM,100MM", 694.15, "EA", "bearing"),
    "5801100205": ("BRG BALL,RAD,6205 2RS,25MM,52MM", 212.40, "EA", "bearing"),
    "5802047683": ("BEARING 1212 K", 2120.50, "EA", "bearing"),
    "5802059553": ("BEARING 22215 EK/C3", 4747.16, "EA", "bearing"),
    "5802840723": ("BEARING SLEEVE HE 317", 295.00, "EA", "bearing_sleeve"),
    "5802841023": ("BEARING SLEEVE HE-312", 152.54, "EA", "bearing_sleeve"),
    "5802841083": ("BEARING SLEEVE,HE-315", 2091.60, "EA", "bearing_sleeve"),
    "5802990011": ("PLUMMER BLOCK SN 512,CI", 1840.00, "EA", "plummer_block"),
    "5814181773": ("BELT DR,V-BELT,B93", 389.93, "EA", "v_belt"),
    "5814182553": ("V-BELT C-106", 702.45, "EA", "v_belt"),
    "5814220023": ("BELT DR,V,B134,16MM,11MM,3350MM", 578.70, "EA", "v_belt"),
    "5814250763": ("BELT DR,V,B 137,17MM,11MM,3479MM", 419.23, "EA", "v_belt"),
    "5814300101": ("BELT,ELEVATOR,RBR,6IN,4PLY", 1150.00, "M", "elevator_belt"),
    "5814300202": ("BELT,CONVEYOR,RBR,24IN,3PLY", 2350.00, "M", "conveyor_belt"),
    "5870973783": ("COUPLING,F70-B", 2790.55, "EA", "coupling"),
    "5870975183": ('CHAIN COUPLING 5/8" 18 TEETH', 825.00, "EA", "chain_coupling"),
    "5872063993": ("SPROCKET,SIMPLEX,18TH,25MM", 681.07, "EA", "sprocket"),
    "5872070011": ('CHAIN,ROLLER,SIMPLEX,1",ANSI 80', 3120.00, "M", "chain"),
    "5873100045": ("BUCKET,ELEVATOR,HDPE,6X4IN", 185.00, "EA", "bucket"),
    "5880011001": ("ROLL,RUBBER,HUSKER,10IN,SATAKE TYPE", 4825.00, "PR", "rubber_roll"),
    "5880022002": ("STONE,WHITENING,EMERY,#36", 9450.00, "EA", "whitening_stone"),
    "5880033003": ("SCREEN,POLISHER,SS,SLOTTED", 6120.00, "EA", "polishing_screen"),
    "5890044004": ("SEAL MECH,25MM,CARBON/SIC", 3380.00, "EA", "mechanical_seal"),
    "5890055005": ("IMPELLER,CF PUMP,SS304,160MM", 7840.00, "EA", "impeller"),
    "6044046033": ("EL T RTD,PT-100,350MM,6MM,150DEGC,1/2IN", 1956.32, "EA", "temperature_sensor"),
    "6044051122": ("VALVE,EJECTOR,COLOUR SORTER,24V", 2650.00, "EA", "ejector_valve"),
    "6423751283": ("ELCTRD WELD,2.5MM", 595.72, "PAC", "shaft"),
    "6496032443": ("PVC BOX(SURFACE MOUNTED)OPEN W 6 MODULE", 83.87, "EA", "electrical_fitting"),
    "6496033133": ("CARBON BRUSH BOSCH GRINDER,MM:24-230", 75.00, "EA", "carbon_brush"),
    "6542025923": ("WALL SOCKET WITH DIN RAIL 16/20A,HENSEL", 221.00, "EA", "electrical_fitting"),
    "6792768333": ("SWITCH MODULAR TYPE 10A", 25.63, "EA", "electrical_fitting"),
    "7010010001": ("GREASE,LITHIUM,EP2", 310.00, "KG", "bearing"),
    "7010020002": ("OIL,GEAR,EP 220", 265.00, "L", "gearbox"),
    "7010030003": ("OIL SEAL,GEARBOX,55X80X10", 145.00, "EA", "gearbox"),
}

# Which materials a component typically consumes (by asset class where it matters)
COMPONENT_MATERIALS = {
    "bearing": {"bucket_elevator": ["5802059553", "5802047683"], "belt_conveyor": ["5802047683"], "husker": ["5801100093"],
                "whitener": ["5801100093"], "polisher": ["5801100093"], "pump": ["5801100205"], "dryer": ["5801100093"],
                "vibro_screen": ["5801100093"], "packing_machine": ["5801100205"]},
    "bearing_sleeve": {"*": ["5802840723", "5802841023", "5802841083"]},
    "plummer_block": {"*": ["5802990011"]},
    "v_belt": {"*": ["5814182553", "5814220023", "5814250763", "5814181773"]},
    "elevator_belt": {"*": ["5814300101"]},
    "conveyor_belt": {"*": ["5814300202"]},
    "sprocket": {"*": ["5872063993"]},
    "chain": {"*": ["5872070011"]},
    "chain_coupling": {"*": ["5870975183"]},
    "coupling": {"*": ["5870973783"]},
    "shaft": {"*": ["6423751283"]},
    "bucket": {"*": ["5873100045"]},
    "screen_deck": {"*": ["1683942313"]},
    "cleaning_cup": {"*": ["1683942343"]},
    "rubber_roll": {"*": ["5880011001"]},
    "whitening_stone": {"*": ["5880022002"]},
    "polishing_screen": {"*": ["5880033003"]},
    "mechanical_seal": {"*": ["5890044004"]},
    "impeller": {"*": ["5890055005"]},
    "temperature_sensor": {"*": ["6044046033"]},
    "ejector_valve": {"*": ["6044051122"]},
    "gearbox": {"*": ["7010020002", "7010030003"]},
    "wheel": {"*": ["5545012833"]},
    "electrical_fitting": {"*": ["6496032443", "6792768333", "6542025923"]},
}


def materials_for(component: str, asset_class: str) -> list[str]:
    m = COMPONENT_MATERIALS.get(component, {})
    return m.get(asset_class) or m.get("*") or []


# --- workspace catalog (what the running system uses) -----------------------------------
# A workspace file overrides the demo defaults. The dicts below are mutated in place so every
# module that imported them sees changes made through the UI or API.
CATALOG_FILE = Path(__file__).resolve().parents[1] / "data" / "workspace" / "catalog.json"

PROCESS_AREAS: dict = {}
ASSETS: dict = {}
MATERIALS: dict = {}


def _defaults() -> dict:
    return {"areas": copy.deepcopy(DEFAULT_PROCESS_AREAS), "assets": copy.deepcopy(DEFAULT_ASSETS),
            "materials": {k: list(v) for k, v in DEFAULT_MATERIALS.items()}}


def reload() -> None:
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8")) if CATALOG_FILE.exists() else _defaults()
    PROCESS_AREAS.clear()
    PROCESS_AREAS.update(data.get("areas", {}))
    ASSETS.clear()
    ASSETS.update(data.get("assets", {}))
    MATERIALS.clear()
    MATERIALS.update({k: tuple(v) for k, v in data.get("materials", {}).items()})
    norm = sys.modules.get(__package__ + ".normalize")  # matching caches depend on the catalog
    if norm is not None and hasattr(norm, "clear_caches"):
        norm.clear_caches()


def save() -> None:
    CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_FILE.write_text(json.dumps({"areas": PROCESS_AREAS, "assets": ASSETS,
                                         "materials": {k: list(v) for k, v in MATERIALS.items()}}, indent=2),
                            encoding="utf-8")
    reload()


def reset_to_demo() -> None:
    if CATALOG_FILE.exists():
        CATALOG_FILE.unlink()
    reload()


def clear() -> None:
    """An empty workspace: no areas, assets or materials (for a real plant's data)."""
    PROCESS_AREAS.clear()
    ASSETS.clear()
    MATERIALS.clear()
    save()


def slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", str(text).upper()).strip("-")[:40] or "ASSET"


def upsert_asset(asset_id: str, name: str, area: str, asset_class: str = "unclassified",
                 aliases: list[str] | None = None) -> dict:
    asset_id = asset_id.strip().upper()
    area = (area or "GEN").strip().upper()
    if area not in PROCESS_AREAS:
        PROCESS_AREAS[area] = {"label": area.title(), "text_prefixes": [f"{area}-", f"{area} "]}
    ASSETS[asset_id] = {"name": name.strip() or asset_id, "area": area, "class": (asset_class or "unclassified").strip(),
                        "aliases": [a.strip() for a in (aliases or []) if a and a.strip()]}
    save()
    return {"asset_id": asset_id, **ASSETS[asset_id]}


def delete_asset(asset_id: str) -> bool:
    found = ASSETS.pop(asset_id.upper(), None) is not None
    if found:
        save()
    return found


def upsert_area(code: str, label: str, text_prefixes: list[str] | None = None) -> dict:
    code = code.strip().upper()
    PROCESS_AREAS[code] = {"label": label.strip() or code, "text_prefixes": text_prefixes or [f"{code}-", f"{code} "]}
    save()
    return {"code": code, **PROCESS_AREAS[code]}


def material_component(code: str, description: str | None = None) -> str | None:
    """Component a material realises: from the materials master, else read from its description."""
    rec = MATERIALS.get(str(code))
    if rec:
        return rec[3]
    if description:
        from .normalize import map_term
        return map_term("component", description)
    return None


reload()
