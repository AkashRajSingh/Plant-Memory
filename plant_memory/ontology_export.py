"""Export the ontology (schema + taxonomy + assets + events + reified facts) as RDF/Turtle.

Verified facts are also asserted directly (event pm:hasRootCause term) so plain
SPARQL queries see trusted knowledge; proposed facts exist only in reified form.
"""
from __future__ import annotations

from pathlib import Path

from rdflib import RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef

from .catalog import ASSETS, MATERIALS, PROCESS_AREAS
from .config import ONTOLOGY_EXPORT, ONTOLOGY_SCHEMA
from .taxonomy import COMPONENTS, FAILURE_MODES, ROOT_CAUSES, VOCAB

PM = Namespace("https://plantmemory.dev/ontology#")
D = Namespace("https://plantmemory.dev/data/")
CLASS_OF = {"component": PM.Component, "failure_mode": PM.FailureMode, "root_cause": PM.RootCause,
            "action": PM.Action, "symptom": PM.Symptom}
PRED = {"component": PM.involvesComponent, "failure_mode": PM.hasFailureMode, "root_cause": PM.hasRootCause,
        "action": PM.resolvedBy, "symptom": PM.observedSymptom, "asset": PM.onAsset}
STATUS = {"proposed": PM.Proposed, "verified": PM.Verified, "rejected": PM.Rejected}


def term_uri(kind: str, term_id: str) -> URIRef:
    return D[f"{kind}/{term_id}"]


def export(repo, out: Path | None = None) -> dict:
    g = Graph()
    g.bind("pm", PM)
    g.bind("d", D)
    if ONTOLOGY_SCHEMA.exists():
        g.parse(ONTOLOGY_SCHEMA, format="turtle")

    for kind, terms in VOCAB.items():
        for tid, spec in terms.items():
            u = term_uri(kind, tid)
            g.add((u, RDF.type, CLASS_OF[kind]))
            g.add((u, RDFS.label, Literal(spec["label"])))
            for syn in spec.get("synonyms", []):
                g.add((u, PM.synonym, Literal(syn)))
            if kind == "failure_mode":
                g.add((u, PM.isoMechanism, Literal(spec.get("iso_mechanism", ""))))
            if kind == "root_cause" and spec.get("category"):
                cat = D[f"cause_category/{spec['category'].replace(' ', '_').replace('/', '_')}"]
                g.add((cat, RDF.type, PM.CauseCategory))
                g.add((cat, RDFS.label, Literal(spec["category"])))
                g.add((u, PM.inCategory, cat))
    for cid, spec in COMPONENTS.items():
        if spec["default_mode"] and spec["default_mode"] in FAILURE_MODES:
            g.add((term_uri("failure_mode", spec["default_mode"]), PM.typicallyAffects, term_uri("component", cid)))

    for code, area in PROCESS_AREAS.items():
        g.add((D[f"area/{code}"], RDF.type, PM.ProcessArea))
        g.add((D[f"area/{code}"], RDFS.label, Literal(area["label"])))
    for aid, a in ASSETS.items():
        u = D[f"asset/{aid}"]
        g.add((u, RDF.type, PM.Asset))
        g.add((u, RDFS.label, Literal(a["name"])))
        g.add((u, PM.locatedIn, D[f"area/{a['area']}"]))
        g.add((u, PM.instanceOfClass, D[f"asset_class/{a['class']}"]))
        g.add((D[f"asset_class/{a['class']}"], RDF.type, PM.AssetClass))
        for al in a["aliases"]:
            g.add((u, PM.synonym, Literal(al)))
    for code, (desc, _, _, comp) in MATERIALS.items():
        u = D[f"material/{code}"]
        g.add((u, RDF.type, PM.SparePart))
        g.add((u, RDFS.label, Literal(desc)))
        g.add((u, PM.sapMaterial, Literal(code)))
        g.add((u, PM.realizes, term_uri("component", comp)))

    n_events = 0
    for e in repo.events():
        u = D[f"event/{e['event_id']}"]
        n_events += 1
        g.add((u, RDF.type, PM.MaintenanceEvent))
        g.add((u, RDFS.label, Literal(e["description"] or "")))
        g.add((u, PM.sapOrder, Literal(e["event_id"])))
        if e["posting_date"]:
            g.add((u, PM.postingDate, Literal(e["posting_date"], datatype=XSD.date)))
        g.add((u, PM.costRs, Literal(round(e["total_cost"] or 0, 2), datatype=XSD.decimal)))
        if e["downtime_hrs"] is not None:
            g.add((u, PM.downtimeHours, Literal(e["downtime_hrs"], datatype=XSD.decimal)))
        if e["work_type"]:
            g.add((u, PM.workType, Literal(e["work_type"])))
        g.add((u, PM.isBreakdown, Literal(str(e["breakdown"] or "").lower() == "yes", datatype=XSD.boolean)))
        if e["technician"]:
            g.add((u, PM.performedBy, D[f"technician/{e['technician']}"]))
            g.add((D[f"technician/{e['technician']}"], RDF.type, PM.Technician))
    for l in (l for l in repo.lines() if l["event_id"] and l["material"]):
        g.add((D[f"event/{l['event_id']}"], PM.consumed, D[f"material/{l['material']}"]))

    n_facts = 0
    for f in repo.facts():
        f = {**f, "field": f["evidence_field"], "snippet": f["evidence_snippet"]}
        kind = f["object_kind"]
        subj = D[f"event/{f['event_id']}"] if f["event_id"] else D[f"asset/{f['asset_id']}"]
        obj = D[f"asset/{f['object_id']}"] if kind == "asset" else term_uri(kind, f["object_id"])
        fu = D[f"fact/{f['fact_id']}"]
        n_facts += 1
        g.add((fu, RDF.type, PM.Fact))
        g.add((fu, PM.subject, subj))
        g.add((fu, PM.predicate, PRED.get(kind, PM.hasRootCause)))
        g.add((fu, PM.object, obj))
        g.add((fu, PM.confidence, Literal(f["confidence"], datatype=XSD.decimal)))
        g.add((fu, PM.method, Literal(f["method"])))
        g.add((fu, PM.status, STATUS[f["status"]]))
        if f["verified_by"]:
            g.add((fu, PM.verifiedBy, D[f"technician/{f['verified_by'].replace(' ', '_')}"]))
            g.add((fu, PM.verifiedAt, Literal(f["verified_at"], datatype=XSD.dateTime)))
        if f["snippet"] is not None:
            ev = D[f"evidence/{f['fact_id']}"]
            g.add((ev, RDF.type, PM.Evidence))
            g.add((ev, PM.sourceField, Literal(f["field"] or "")))
            g.add((ev, PM.snippet, Literal(f["snippet"])))
            g.add((fu, PM.supportedBy, ev))
        if f["status"] == "verified" and f["event_id"]:
            g.add((subj, PRED.get(kind, PM.hasRootCause), obj))

    out = Path(out or ONTOLOGY_EXPORT)
    out.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(out, format="turtle")
    return {"file": str(out), "triples": len(g), "events": n_events, "facts": n_facts}
