import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, qs, type Fact } from "../api";
import { Empty, FactRow, Select, WorkOrderDrawer, useApp } from "../components/ui";

interface GNode { id: string; type: string; label: string; count: number; ref?: string; date?: string; description?: string }
interface GEdge { id: string; source: string; target: string; kind: string; count: number; verified: number; proposed: number; orders: string[]; value: number; fact_id?: string; status?: string; confidence?: number }
interface GraphResp { view: string; work_orders: number; truncated: boolean; nodes: GNode[]; edges: GEdge[] }

const TYPE_STYLE: Record<string, { color: string; shape: string; label: string }> = {
  asset: { color: "#2f4a52", shape: "round-rectangle", label: "Asset" },
  failure_mode: { color: "#1f6f78", shape: "ellipse", label: "Failure mode" },
  root_cause: { color: "#7a5c8f", shape: "diamond", label: "Root cause" },
  action: { color: "#4a6fa5", shape: "round-triangle", label: "Action" },
  part: { color: "#a9804a", shape: "round-hexagon", label: "Spare part" },
  event: { color: "#8a9a9d", shape: "ellipse", label: "Work order" },
};
const EDGE_TEXT: Record<string, string> = {
  FAILED_WITH: "failed with", CAUSED_BY: "caused by", FIXED_BY: "fixed by", CONSUMED: "used",
  ON_ASSET: "on", HAS_FAILURE_MODE: "failure mode", HAS_ROOT_CAUSE: "root cause", RESOLVED_BY: "action",
};

export default function GraphPage() {
  const { meta, dataVersion } = useApp();
  const [f, setF] = useState({ area: "", asset: "", failure_mode: "", root_cause: "", status: "all", view: "summary", include_parts: false, include_actions: false });
  const [data, setData] = useState<GraphResp | null>(null);
  const [sel, setSel] = useState<{ node?: GNode; edge?: GEdge } | null>(null);
  const [order, setOrder] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);

  useEffect(() => {
    setLoading(true);
    api.get<GraphResp>(`/api/graph${qs(f)}`).then((d) => { setData(d); setSel(null); }).catch(() => {}).finally(() => setLoading(false));
  }, [f, dataVersion]);

  const elements = useMemo<ElementDefinition[]>(() => {
    if (!data) return [];
    const maxE = Math.max(1, ...data.edges.map((e) => e.count));
    const maxN = Math.max(1, ...data.nodes.map((n) => n.count));
    return [
      ...data.nodes.map((n) => ({
        data: { ...n, size: n.type === "event" ? 14 : data.view === "summary" ? 14 + 20 * Math.sqrt(n.count / maxN) : 22 + 26 * Math.sqrt(n.count / maxN),
                display: n.type === "event" ? n.label.slice(-5) : n.label },
      })),
      ...data.edges.map((e) => ({
        data: {
          ...e, w: 1 + 5 * Math.sqrt(e.count / maxE),
          state: e.fact_id ? e.status : e.proposed === 0 && e.verified > 0 ? "verified" : e.verified === 0 && e.proposed > 0 ? "proposed" : e.verified + e.proposed === 0 ? "plain" : "mixed",
          elabel: "",
        },
      })),
    ];
  }, [data]);

  useEffect(() => {
    if (!box.current) return;
    const css = getComputedStyle(document.documentElement);
    const ink = css.getPropertyValue("--ink").trim() || "#1c2a2e";
    const muted = css.getPropertyValue("--ink-3").trim() || "#758589";
    const yellow = css.getPropertyValue("--tag-yellow").trim() || "#e3a72b";
    const green = css.getPropertyValue("--tag-green").trim() || "#3f7d4e";
    const panel = css.getPropertyValue("--panel").trim() || "#fff";
    const inst = cytoscape({
      container: box.current,
      elements,
      style: [
        { selector: "node", style: {
          "background-color": (n: cytoscape.NodeSingular) => TYPE_STYLE[n.data("type")]?.color ?? "#888",
          shape: ((n: cytoscape.NodeSingular) => TYPE_STYLE[n.data("type")]?.shape ?? "ellipse") as unknown as cytoscape.Css.NodeShape,
          width: "data(size)", height: "data(size)", label: "data(display)", color: ink, "font-size": data?.view === "summary" ? 13 : 11,
          "font-family": "Barlow, system-ui, sans-serif",
          "text-valign": data?.view === "summary" ? "center" : "bottom", "text-halign": data?.view === "summary" ? "right" : "center",
          "text-margin-x": data?.view === "summary" ? 6 : 0, "text-margin-y": data?.view === "summary" ? 0 : 4,
          "text-wrap": "wrap", "text-max-width": data?.view === "summary" ? "170px" : "110px", "text-background-color": panel, "text-background-opacity": 0.85,
          "text-background-padding": "1px", "border-width": 0,
        } },
        { selector: "node[type = 'event']", style: { "font-size": 9, color: muted } },
        { selector: "edge", style: {
          width: "data(w)", "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 0.7,
          "line-color": muted, "target-arrow-color": muted, opacity: 0.75, label: "data(elabel)", "font-size": 10, color: muted,
          "text-background-color": panel, "text-background-opacity": 0.9, "text-background-padding": "1px",
        } },
        { selector: "edge[state = 'verified']", style: { "line-color": green, "target-arrow-color": green } },
        { selector: "edge[state = 'proposed']", style: { "line-style": "dashed", "line-color": yellow, "target-arrow-color": yellow } },
        { selector: "edge[state = 'rejected']", style: { "line-style": "dotted" } },
        { selector: ":selected", style: { "border-width": 3, "border-color": yellow, "line-color": ink, "target-arrow-color": ink, opacity: 1 } },
        { selector: ".faded", style: { opacity: 0.15 } },
      ],
      layout: data?.view === "summary"
        ? { name: "breadthfirst", directed: true, roots: elements.filter((e) => e.data.type === "asset").map((e) => `#${CSS.escape(String(e.data.id))}`).join(","),
            spacingFactor: 1, padding: 30, animate: false, avoidOverlap: true,
            // left-to-right: assets, then failure modes, then causes
            transform: (_n: unknown, pos: { x: number; y: number }) => ({ x: pos.y * 2.4, y: pos.x * 0.95 }) } as unknown as cytoscape.LayoutOptions
        : { name: "cose", animate: false, padding: 24, nodeRepulsion: () => 9000, idealEdgeLength: () => 70 } as cytoscape.LayoutOptions,
    });
    inst.on("tap", "node", (ev) => {
      const n = ev.target;
      inst.elements().addClass("faded");
      n.closedNeighborhood().removeClass("faded");
      setSel({ node: n.data() as GNode });
    });
    inst.on("tap", "edge", (ev) => setSel({ edge: ev.target.data() as GEdge }));
    inst.on("tap", (ev) => { if (ev.target === inst) { inst.elements().removeClass("faded"); setSel(null); } });
    cy.current = inst;
    return () => inst.destroy();
  }, [elements, data?.view]);

  const set = (patch: Partial<typeof f>) => setF((x) => ({ ...x, ...patch }));
  const nodeOrders = (n: GNode) => {
    const s = new Set<string>();
    data?.edges.filter((e) => e.source === n.id || e.target === n.id).forEach((e) => e.orders.forEach((o) => s.add(o)));
    if (n.type === "event") s.add(n.label);
    return [...s].slice(0, 60);
  };
  const nodeName = (id: string) => data?.nodes.find((n) => n.id === id)?.label ?? id;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Knowledge graph</h1>
          <p>Assets, how they fail and why. Line thickness is the number of work orders; green lines are verified, dashed yellow lines still need review. Click anything to see the evidence.</p>
        </div>
      </div>
      <div className="filters">
        <label className="field"><span>Show</span>
          <div className="seg" role="group">
            <button aria-pressed={f.view === "summary"} onClick={() => set({ view: "summary" })}>Patterns</button>
            <button aria-pressed={f.view === "events"} onClick={() => set({ view: "events" })}>Individual work orders</button>
          </div>
        </label>
        <Select label="Process area" value={f.area} onChange={(v) => set({ area: v, asset: "" })} options={(meta?.areas ?? []).map((a) => ({ value: a.code, label: a.label }))} />
        <Select label="Asset" value={f.asset} onChange={(v) => set({ asset: v })}
          options={(meta?.assets ?? []).filter((a) => !f.area || a.area === f.area).map((a) => ({ value: a.asset_id, label: a.name }))} />
        <Select label="Failure mode" value={f.failure_mode} onChange={(v) => set({ failure_mode: v })} options={(meta?.vocab.failure_mode ?? []).map((o) => ({ value: o.id, label: o.label }))} />
        <Select label="Root cause" value={f.root_cause} onChange={(v) => set({ root_cause: v })} options={(meta?.vocab.root_cause ?? []).map((o) => ({ value: o.id, label: o.label }))} />
        <Select label="Review status" value={f.status} onChange={(v) => set({ status: v })} any={null}
          options={[{ value: "all", label: "Everything" }, { value: "verified", label: "Verified only" }, { value: "proposed", label: "Needs review" }]} />
        <label className="check"><input type="checkbox" checked={f.include_parts} onChange={(e) => set({ include_parts: e.target.checked })} /> Spare parts</label>
        <label className="check"><input type="checkbox" checked={f.include_actions} onChange={(e) => set({ include_actions: e.target.checked })} /> Actions</label>
      </div>

      {data && data.nodes.length === 0 && !loading ? (
        <Empty title="Nothing matches these filters">Clear a filter, or upload work orders first.</Empty>
      ) : (
        <div className="graph-layout">
          <div className="graph-canvas">
            <div ref={box} className="cy" aria-label="Knowledge graph" />
            <div className="legend">
              {Object.entries(TYPE_STYLE).filter(([t]) => data?.nodes.some((n) => n.type === t)).map(([t, s]) => (
                <span key={t}><i style={{ background: s.color }} />{s.label}</span>
              ))}
              <span><i className="ln" style={{ borderTopColor: "var(--tag-green)" }} />verified</span>
              <span><i className="ln dash" />needs review</span>
            </div>
          </div>
          <aside className="side" aria-live="polite">
            {!sel && (
              <div className="stack">
                <h3>{data ? `${data.work_orders} work orders shown` : "Loading…"}</h3>
                {data?.truncated && <div className="notice warn">Showing the 250 most recent work orders. Pick an asset to see all of its history.</div>}
                <p className="muted">Click a node to highlight what it connects to. Click a line to see the work orders behind it.</p>
                {f.view === "summary" && <p className="muted">Switch to “Individual work orders” and pick an asset to review single facts on the graph.</p>}
              </div>
            )}
            {sel?.node && (
              <div className="stack">
                <span className="muted small">{TYPE_STYLE[sel.node.type]?.label}</span>
                <h3>{sel.node.type === "event" ? `Work order ${sel.node.label}` : sel.node.label}</h3>
                {sel.node.description && <p>{sel.node.description}<br /><span className="muted small">{sel.node.date}</span></p>}
                {sel.node.type === "asset" && (
                  <div className="row">
                    <button className="btn small" onClick={() => set({ asset: sel.node!.ref!, view: "events" })}>Show its work orders</button>
                    <button className="btn small" onClick={() => set({ asset: sel.node!.ref!, view: "summary" })}>Only this asset</button>
                  </div>
                )}
                {sel.node.type === "failure_mode" && <button className="btn small" onClick={() => set({ failure_mode: sel.node!.ref! })}>Filter to this failure mode</button>}
                {sel.node.type === "event" ? (
                  <button className="btn small primary" onClick={() => setOrder(sel.node!.label)}>Open work order</button>
                ) : (
                  <>
                    <span className="small muted">Work orders</span>
                    <div className="order-list">{nodeOrders(sel.node).map((o) => <button key={o} onClick={() => setOrder(o)}>{o}</button>)}</div>
                  </>
                )}
              </div>
            )}
            {sel?.edge && <EdgePanel edge={sel.edge} name={nodeName} onOrder={setOrder} />}
          </aside>
        </div>
      )}
      {order && <WorkOrderDrawer order={order} onClose={() => setOrder(null)} />}
    </>
  );
}

function EdgePanel({ edge, name, onOrder }: { edge: GEdge; name: (id: string) => string; onOrder: (o: string) => void }) {
  const [fact, setFact] = useState<Fact | null>(null);
  useEffect(() => {
    setFact(null);
    if (edge.fact_id) api.get<{ fact: Fact }>(`/api/evidence/${encodeURIComponent(edge.fact_id)}`).then((r) => setFact(r.fact)).catch(() => {});
  }, [edge.fact_id]);
  return (
    <div className="stack">
      <span className="muted small">Connection</span>
      <h3>{name(edge.source)} <span className="muted">{EDGE_TEXT[edge.kind] ?? edge.kind}</span> {name(edge.target)}</h3>
      {!edge.fact_id && (
        <p>{edge.count} work order{edge.count === 1 ? "" : "s"}{edge.verified + edge.proposed > 0 ? `: ${edge.verified} verified, ${edge.proposed} need review` : ""}.
          {edge.kind === "FAILED_WITH" && edge.value > 0 ? ` ${edge.value} h of downtime.` : ""}{edge.kind === "CONSUMED" && edge.value > 0 ? ` ₹${Math.round(edge.value).toLocaleString("en-IN")} spent.` : ""}</p>
      )}
      {fact && <div className="box" style={{ padding: "0 12px" }}><FactRow fact={fact} onChanged={setFact} /></div>}
      <span className="small muted">Work orders</span>
      <div className="order-list">{edge.orders.map((o) => <button key={o} onClick={() => onOrder(o)}>{o}</button>)}</div>
    </div>
  );
}
