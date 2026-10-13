"""Load a SAP PM export (xlsx/csv) and merge material lines into maintenance events."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .catalog import material_component
from .normalize import normalize_text, resolve_asset

COLUMN_MAP = {
    "order": "order", "posting date": "posting_date", "work description": "description",
    "material description": "material_description", "cost (in rs)": "cost", "cost": "cost",
    "quantity": "quantity", "base unit of measure": "uom", "g/l account": "gl_account",
    "cost center": "cost_center", "planning department": "planning_department", "work type": "work_type",
    "movement type": "movement_type", "material": "material", "storage location": "storage_location",
    "functional location": "functional_location", "equipment id": "functional_location",
    "symptom": "symptom", "problem description": "symptom", "root cause": "root_cause", "rca": "root_cause",
    "cause category": "cause_category", "action taken": "action_taken", "downtime (hrs)": "downtime_hrs",
    "downtime": "downtime_hrs", "breakdown": "breakdown", "technician remarks": "remarks", "remarks": "remarks",
    "technician": "technician", "technician assigned": "technician",
}
# Fields Plant Memory understands: (key, label, required, help)
CANONICAL_FIELDS = [
    ("order", "Work order number", True, "Groups material lines into one maintenance event"),
    ("posting_date", "Posting / completion date", True, "When the work happened"),
    ("description", "Work description (short text)", True, "Free text: asset, problem, action"),
    ("material", "Material code", False, "Spare part code; the description is used if missing"),
    ("material_description", "Material description", False, "Spare part name"),
    ("quantity", "Quantity", False, ""),
    ("uom", "Unit of measure", False, ""),
    ("cost", "Cost", False, "Numbers or text like 'Rs 1,234.00'"),
    ("work_type", "Work type", False, "Preventive / Corrective / ..."),
    ("planning_department", "Planning department", False, ""),
    ("functional_location", "Functional location / equipment ID", False, "Best source for asset identity"),
    ("symptom", "Symptom / problem", False, ""),
    ("root_cause", "Root cause (RCA)", False, ""),
    ("cause_category", "Cause category", False, ""),
    ("action_taken", "Action taken", False, ""),
    ("downtime_hrs", "Downtime (hours)", False, ""),
    ("breakdown", "Breakdown flag", False, "Yes / No"),
    ("remarks", "Technician remarks", False, "Tacit knowledge lives here"),
    ("technician", "Technician", False, ""),
    ("movement_type", "Movement type", False, ""),
    ("cost_center", "Cost center", False, ""),
    ("gl_account", "G/L account", False, ""),
    ("storage_location", "Storage location", False, ""),
]
CANONICAL_KEYS = [f[0] for f in CANONICAL_FIELDS]

HEADER_FIELDS = ["functional_location", "symptom", "root_cause", "cause_category", "action_taken",
                 "downtime_hrs", "breakdown", "remarks", "technician"]


@dataclass
class Event:
    event_id: str
    posting_date: str
    last_posting_date: str
    description: str
    normalized_text: str
    work_type: str | None
    planning_department: str | None
    header: dict
    lines: list[dict] = field(default_factory=list)
    asset_id: str | None = None
    asset_confidence: float = 0.0
    asset_method: str = ""
    work_type_flag: str | None = None

    @property
    def total_cost(self) -> float:
        return round(sum(l["cost"] or 0 for l in self.lines), 2)


def _parse_cost(v) -> float:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v))
    return float(s) if s not in {"", "-", "."} else 0.0


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


def _id(v) -> str | None:
    v = _clean(v)
    if v is None:
        return None
    if isinstance(v, float):
        v = int(v)
    return str(v).strip()


def read_table(path: Path, nrows: int | None = None) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() in {".csv", ".txt"}:
        try:
            return pd.read_csv(path, nrows=nrows)
        except UnicodeDecodeError:
            return pd.read_csv(path, nrows=nrows, encoding="latin-1")
    return pd.read_excel(path, nrows=nrows)


def suggest_mapping(columns: list[str]) -> dict[str, str]:
    """Guess which Plant Memory field each source column is ('' = ignore)."""
    import difflib
    labels = {f[1].lower(): f[0] for f in CANONICAL_FIELDS} | {k.replace("_", " "): k for k in CANONICAL_KEYS}
    out, used = {}, set()
    for col in columns:
        c = str(col).strip().lower()
        key = COLUMN_MAP.get(c) or labels.get(c)
        if not key:
            hit = difflib.get_close_matches(c, list(COLUMN_MAP) + list(labels), n=1, cutoff=0.8)
            key = (COLUMN_MAP.get(hit[0]) or labels.get(hit[0])) if hit else ""
        if key and key in used:
            key = ""
        out[str(col)] = key or ""
        if key:
            used.add(key)
    return out


def _date_order(values) -> bool:
    """True if dates look day-first (an 'a/b/yyyy' value with a > 12)."""
    for v in values:
        m = re.match(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-]\d{2,4}", str(v))
        if m and int(m.group(1)) > 12:
            return True
        if m and int(m.group(2)) > 12:
            return False
    return False


def load_export(path: Path, mapping: dict[str, str] | None = None, source_name: str | None = None) -> pd.DataFrame:
    """Read an export. `mapping` maps source columns to Plant Memory fields (see CANONICAL_FIELDS)."""
    path = Path(path)
    df = read_table(path)
    if mapping:
        keep = {c: mapping[str(c)] for c in df.columns if mapping.get(str(c))}
        df = df[list(keep)].rename(columns=keep)
    else:
        df.columns = [COLUMN_MAP.get(str(c).strip().lower(), str(c).strip().lower()) for c in df.columns]
        df = df.loc[:, ~pd.Index(df.columns).duplicated()]
    for col in CANONICAL_KEYS:
        if col not in df.columns:
            df[col] = None
    df["source_row"] = df.index + 2  # spreadsheet row number (header is row 1)
    df["source_file"] = source_name or path.name
    raw_dates = df["posting_date"]
    if pd.api.types.is_datetime64_any_dtype(raw_dates):
        parsed = raw_dates
    else:
        parsed = pd.to_datetime(raw_dates, errors="coerce", dayfirst=_date_order(raw_dates.dropna().head(200)))
    df["posting_date"] = parsed.dt.date.astype(str)
    df["cost"] = df["cost"].map(_parse_cost)
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0).abs()
    return df


def build_events(df: pd.DataFrame) -> tuple[list[Event], list[dict]]:
    """Group lines by order. Rows without an order (e.g. MB21 reservations) are consumption-only."""
    events, consumption = [], []
    for _, r in df[df["order"].isna()].iterrows():
        consumption.append(_line(r, None))
    for order, g in df[df["order"].notna()].groupby("order", sort=False):
        oid = _id(order)
        g = g.sort_values("source_row")
        desc = Counter([d for d in g["description"] if _clean(d)]).most_common(1)
        desc = desc[0][0] if desc else ""
        header = {}
        for f in HEADER_FIELDS:
            vals = [_clean(v) for v in g[f] if _clean(v) is not None]
            header[f] = vals[0] if vals else None
        wt = Counter([w for w in g["work_type"] if _clean(w)]).most_common(1)
        dates = sorted(d for d in g["posting_date"] if d and d != "NaT")
        ev = Event(event_id=oid, posting_date=dates[0] if dates else "", last_posting_date=dates[-1] if dates else "",
                   description=str(desc), normalized_text=normalize_text(desc), work_type=wt[0][0] if wt else None,
                   planning_department=_clean(g["planning_department"].iloc[0]), header=header)
        ev.lines = [_line(r, oid) for _, r in g.iterrows()
                    if _clean(r["material"]) is not None or _clean(r["material_description"]) is not None]
        ev.asset_id, ev.asset_confidence, ev.asset_method = resolve_asset(_id(header.get("functional_location")), desc)
        ev.work_type_flag = _work_type_flag(ev)
        events.append(ev)
    return events, consumption


def _line(r, event_id) -> dict:
    mat = _id(r["material"]) or _clean(r["material_description"])
    return {"event_id": event_id, "source_row": int(r["source_row"]), "source_file": r.get("source_file"),
            "posting_date": r["posting_date"],
            "material": mat, "material_description": _clean(r["material_description"]),
            "component": material_component(mat, _clean(r["material_description"])) if mat else None, "quantity": float(r["quantity"] or 0),
            "uom": _clean(r["uom"]), "cost": float(r["cost"] or 0), "movement_type": _id(r["movement_type"]),
            "cost_center": _clean(r["cost_center"])}


def _work_type_flag(ev: Event) -> str | None:
    bd = str(ev.header.get("breakdown") or "").lower() == "yes"
    text_bd = any(w in ev.normalized_text for w in ["breakdown", "broken", "broke"])
    if (bd or text_bd) and (ev.work_type or "").lower() == "preventive":
        return "Breakdown recorded with work type Preventive"
    return None
