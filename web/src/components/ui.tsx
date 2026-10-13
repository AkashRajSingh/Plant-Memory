import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, KIND_LABEL, type Fact, type Job, type Meta, type Status } from "../api";

/* ---------- app-wide context: reviewer name, metadata, toasts, job ---------- */
export interface AppCtx {
  reviewer: string;
  meta: Meta | null;
  refreshMeta: () => void;
  toast: (msg: string, err?: boolean) => void;
  job: Job | null;
  watchJob: (job: Job) => void;
  dataVersion: number;
  bump: () => void;
  go: (page: string) => void;
}
export const Ctx = createContext<AppCtx>(null as unknown as AppCtx);
export const useApp = () => useContext(Ctx);

/* ---------- review status tag ---------- */
const TAG_TEXT: Record<Status, string> = { proposed: "Needs review", verified: "Verified", rejected: "Rejected" };
export function Tag({ status, children }: { status: Status | "neutral"; children?: ReactNode }) {
  return <span className={`tag ${status}`}>{children ?? (status === "neutral" ? "" : TAG_TEXT[status])}</span>;
}

export function Conf({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <span className={`conf ${value < 0.7 ? "low" : ""}`} title={`Confidence ${pct}%`}>
      <span className="bar"><i style={{ width: `${pct}%` }} /></span>
      {pct}%
    </span>
  );
}

export function Empty({ title, children, actions }: { title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="empty">
      <h2>{title}</h2>
      {children && <p>{children}</p>}
      {actions && <div className="btns">{actions}</div>}
    </div>
  );
}

export function Select({ label, value, onChange, options, any = "All" }: {
  label: string; value: string; onChange: (v: string) => void;
  options: { value: string; label: string }[]; any?: string | null;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {any !== null && <option value="">{any}</option>}
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}

export function Drawer({ title, sub, onClose, children }: { title: ReactNode; sub?: ReactNode; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <>
      <div className="drawer-bg" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="drawer-head">
          <div style={{ flex: 1 }}>
            <h2>{title}</h2>
            {sub && <div className="muted small" style={{ marginTop: 4 }}>{sub}</div>}
          </div>
          <button className="btn small" onClick={onClose}>Close</button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>
  );
}

const METHOD: Record<string, string> = {
  structured: "Read from an SAP column", "structured+llm": "Read from an SAP column; the LLM agrees", rules: "Read by plant rules",
  inferred: "Inferred from the component, no direct evidence", llm: "Suggested by the LLM", "llm+rules": "Rules and LLM agree",
  technician: "Stated by a technician", functional_location: "From the functional location", alias_match: "Matched from the description",
};
const methodText = (m: string) => {
  const base = m.replace("+edited", "");
  return (METHOD[base] ?? `Found by ${base.replace(/_/g, " ")}`) + (m.endsWith("+edited") ? " (corrected by a person)" : "");
};

/* ---------- one reviewable fact: approve / correct / reject ---------- */
export function FactRow({ fact, onChanged, showOrder = false }: { fact: Fact; onChanged?: (f: Fact) => void; showOrder?: boolean }) {
  const { reviewer, meta, toast, bump, job } = useApp();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(fact.object_id);
  const [busy, setBusy] = useState(false);
  const kind = fact.object_kind as keyof Meta["vocab"];
  const options = meta?.vocab[kind] ?? [];
  const locked = !!job && (job.state === "running" || job.state === "queued");

  async function decide(status: "verified" | "rejected", newValue?: string) {
    if (!reviewer.trim()) { toast("Enter your name at the top first, so decisions are credited.", true); return; }
    setBusy(true);
    try {
      const f = await api.post<Fact>(`/api/facts/${encodeURIComponent(fact.fact_id)}/decision`,
        { status, by: reviewer, value: newValue && newValue !== fact.object_id ? newValue : undefined });
      toast(status === "verified" ? (newValue && newValue !== fact.object_id ? "Corrected and verified" : "Verified") : "Rejected");
      setEditing(false);
      bump();
      onChanged?.({ ...fact, ...f });
    } catch (e) { toast((e as Error).message, true); }
    setBusy(false);
  }

  const lbl = fact.label ?? options.find((o) => o.id === fact.object_id)?.label ?? fact.object_id;
  return (
    <div className="fact">
      <div className="kind">{KIND_LABEL[fact.object_kind] ?? fact.object_kind}</div>
      <div className="val">
        {editing ? (
          <select value={value} onChange={(e) => setValue(e.target.value)} autoFocus>
            {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
        ) : (
          <span>{lbl}</span>
        )}
        <Tag status={fact.status} />
        <Conf value={fact.confidence} />
      </div>
      <div className="acts">
        {fact.status !== "verified" && !editing && (
          <button className="btn small good" disabled={busy || locked} onClick={() => decide("verified")}>Verify</button>
        )}
        {kind in (meta?.vocab ?? {}) && (editing ? (
          <>
            <button className="btn small primary" disabled={busy} onClick={() => decide("verified", value)}>Save correction</button>
            <button className="btn small" onClick={() => setEditing(false)}>Cancel</button>
          </>
        ) : (
          <button className="btn small" disabled={busy || locked} onClick={() => setEditing(true)}>Correct</button>
        ))}
        {fact.status !== "rejected" && !editing && (
          <button className="btn small bad" disabled={busy || locked} onClick={() => decide("rejected")}>Reject</button>
        )}
      </div>
      <div className="meta">
        {showOrder && fact.event_id && (
          <span className="small"><span className="order">{fact.event_id}</span> · {fact.posting_date} · {fact.description}</span>
        )}
        {(fact.evidence_snippet || fact.evidence) && (
          <span className="quote">{fact.evidence_field ? `${fact.evidence_field}: ` : ""}“{fact.evidence_snippet ?? fact.evidence}”</span>
        )}
        <span className="muted small">
          {methodText(fact.method)}{fact.verified_by ? ` · ${fact.status} by ${fact.verified_by}` : ""}
          {fact.note ? ` · ${fact.note}` : ""}
        </span>
      </div>
    </div>
  );
}

/* ---------- a work order with its raw SAP lines (read-only) and facts ---------- */
interface WorkOrder {
  work_order: {
    event_id: string; posting_date: string; description: string; asset_id: string | null; work_type: string | null;
    total_cost: number; downtime_hrs: number | null; breakdown: string | null; remarks: string | null;
    symptom_text: string | null; root_cause_text: string | null; action_text: string | null; technician: string | null;
    work_type_flag: string | null;
    sap_lines: { source_row: number; source_file?: string; posting_date: string; material: string; material_description: string; quantity: number; uom: string; cost: number }[];
    facts: Fact[];
  };
}

export function WorkOrderDrawer({ order, onClose }: { order: string; onClose: () => void }) {
  const { meta, dataVersion } = useApp();
  const [data, setData] = useState<WorkOrder | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api.get<WorkOrder>(`/api/events/${encodeURIComponent(order)}`).then(setData).catch((e) => setErr(e.message));
  }, [order, dataVersion]);
  const w = data?.work_order;
  const asset = meta?.assets.find((a) => a.asset_id === w?.asset_id);
  return (
    <Drawer title={<>Work order <span className="order">{order}</span></>} sub={w && `${w.posting_date} · ${asset?.name ?? w.asset_id ?? "Unknown asset"} · ${w.work_type ?? ""}`} onClose={onClose}>
      {err && <div className="notice err">{err}</div>}
      {w && (
        <>
          <h3>{w.description}</h3>
          {w.work_type_flag && <div className="notice warn" style={{ marginTop: 10 }}>{w.work_type_flag}</div>}
          <div className="raw-label">What the system understood (review each fact)</div>
          <div className="box" style={{ padding: "0 14px" }}>
            {w.facts.filter((f) => f.object_kind !== "asset").map((f) => <FactRow key={f.fact_id} fact={f} />)}
            {w.facts.length === 0 && <p className="muted" style={{ padding: 14 }}>No facts were extracted from this work order.</p>}
          </div>
          <div className="raw-label">Recorded in SAP: raw evidence, read-only</div>
          <div className="table-wrap raw">
            <table>
              <tbody>
                {([["Symptom", w.symptom_text], ["Root cause", w.root_cause_text], ["Action taken", w.action_text],
                  ["Technician remarks", w.remarks], ["Downtime", w.downtime_hrs != null ? `${w.downtime_hrs} h` : null],
                  ["Breakdown", w.breakdown], ["Technician", w.technician]] as [string, unknown][]).filter(([, v]) => v).map(([k, v]) => (
                  <tr key={k}><td className="muted" style={{ width: 170 }}>{k}</td><td>{String(v)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="raw-label">Material lines</div>
          <div className="table-wrap raw">
            <table>
              <thead><tr><th>Row</th><th>Date</th><th>Material</th><th className="num">Qty</th><th className="num">Cost</th></tr></thead>
              <tbody>
                {w.sap_lines.map((l) => (
                  <tr key={`${l.source_file}-${l.source_row}`}>
                    <td className="muted" title={l.source_file}>{l.source_row}</td>
                    <td>{l.posting_date}</td>
                    <td>{l.material_description}<div className="muted small">{l.material}</div></td>
                    <td className="num">{l.quantity} {l.uom}</td>
                    <td className="num">{fmtMoney(l.cost)}</td>
                  </tr>
                ))}
                {w.sap_lines.length === 0 && <tr><td colSpan={5} className="muted">Labour only: no materials issued.</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Drawer>
  );
}

export const fmtMoney = (n: number | null | undefined) =>
  n == null ? "" : "₹" + Math.round(n).toLocaleString("en-IN");
export const fmtNum = (n: number | null | undefined, d = 0) =>
  n == null ? "" : n.toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });

export function JobBar({ job }: { job: Job | null }) {
  if (!job) return null;
  const pct = job.total ? Math.round((job.done / job.total) * 100) : job.state === "done" ? 100 : 5;
  const stage: Record<string, string> = {
    queued: "Queued", reading: "Reading files", llm: "LLM extraction", rules: "Extracting",
    writing: "Writing the graph", done: "Graph updated",
  };
  if (job.state === "failed") return <div className="jobbar failed" role="status">Build failed: {job.error}</div>;
  if (job.state === "done") return <div className="jobbar" role="status">Graph updated · {String(job.result?.work_orders ?? "")} work orders</div>;
  return (
    <div className="jobbar" role="status" aria-live="polite">
      <span>{stage[job.stage] ?? job.stage}{job.total > 1 ? ` ${job.done}/${job.total}` : ""}</span>
      <span className="track"><span className="fill" style={{ width: `${pct}%` }} /></span>
    </div>
  );
}
