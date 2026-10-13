import { useEffect, useState } from "react";
import { api, qs, type Status } from "../api";
import { Select, WorkOrderDrawer, fmtMoney, fmtNum, useApp } from "../components/ui";

interface Cell { id: string; label: string; status: Status; confidence: number; fact_id: string }
interface Row {
  order: string; date: string; description: string; asset: string | null; area: string | null; work_type: string | null;
  cost: number; downtime_hrs: number | null; flag: string | null;
  failure_mode: Cell | null; root_cause: Cell | null; action: Cell | null; facts_total: number; facts_verified: number;
}
interface Page { total: number; page: number; page_size: number; items: Row[] }

export default function Data() {
  const { meta, dataVersion } = useApp();
  const [f, setF] = useState({ q: "", area: "", asset: "", failure_mode: "", root_cause: "", review: "", failures_only: false, sort: "-date" });
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page | null>(null);
  const [order, setOrder] = useState<string | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => { const t = window.setTimeout(() => { setF((x) => ({ ...x, q })); setPage(1); }, 300); return () => window.clearTimeout(t); }, [q]);
  useEffect(() => { api.get<Page>(`/api/events${qs({ ...f, page, page_size: 50 })}`).then(setData).catch(() => {}); }, [f, page, dataVersion]);

  const set = (patch: Partial<typeof f>) => { setF((x) => ({ ...x, ...patch })); setPage(1); };
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const sortBtn = (key: string, label: string) => (
    <button className="btn link" onClick={() => set({ sort: f.sort === `-${key}` ? key : `-${key}` })}>
      {label}{f.sort.replace("-", "") === key ? (f.sort.startsWith("-") ? " ↓" : " ↑") : ""}
    </button>
  );
  const cell = (c: Cell | null) => c
    ? <span>{c.label}{c.status !== "verified" && <span className={`rdot ${c.status}`} title={c.status === "proposed" ? "Needs review" : "Rejected"} aria-label={c.status === "proposed" ? "needs review" : "rejected"} />}</span>
    : <span className="muted">–</span>;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Work orders</h1>
          <p>Every work order with what was understood from it. The SAP records are read-only evidence; you change the knowledge by verifying, correcting or rejecting facts.</p>
        </div>
      </div>
      <div className="filters">
        <label className="field"><span>Search</span><input type="search" placeholder="Order, description, remark…" value={q} onChange={(e) => setQ(e.target.value)} /></label>
        <Select label="Process area" value={f.area} onChange={(v) => set({ area: v, asset: "" })} options={(meta?.areas ?? []).map((a) => ({ value: a.code, label: a.label }))} />
        <Select label="Asset" value={f.asset} onChange={(v) => set({ asset: v })} options={(meta?.assets ?? []).filter((a) => !f.area || a.area === f.area).map((a) => ({ value: a.asset_id, label: a.name }))} />
        <Select label="Failure mode" value={f.failure_mode} onChange={(v) => set({ failure_mode: v })} options={(meta?.vocab.failure_mode ?? []).map((o) => ({ value: o.id, label: o.label }))} />
        <Select label="Root cause" value={f.root_cause} onChange={(v) => set({ root_cause: v })} options={(meta?.vocab.root_cause ?? []).map((o) => ({ value: o.id, label: o.label }))} />
        <Select label="Review" value={f.review} onChange={(v) => set({ review: v })} options={[{ value: "needs_review", label: "Needs review" }, { value: "verified", label: "Fully verified" }]} />
        <label className="check"><input type="checkbox" checked={f.failures_only} onChange={(e) => set({ failures_only: e.target.checked })} /> Failures only</label>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{sortBtn("date", "Date")}</th><th>Order</th><th>{sortBtn("asset", "Asset")}</th><th>Description</th>
              <th>Failure mode</th><th>Root cause</th><th className="num">{sortBtn("cost", "Cost")}</th><th className="num">{sortBtn("downtime", "Downtime")}</th><th title="Verified facts / all facts">Verified</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((r) => (
              <tr key={r.order} className="clickable" onClick={() => setOrder(r.order)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setOrder(r.order)}>
                <td className="small nowrap">{r.date}</td>
                <td className="order">{r.order}</td>
                <td>{r.asset ?? <span className="muted">unknown</span>}</td>
                <td className="small">{r.description}{r.flag && <div className="muted small">⚑ {r.flag}</div>}</td>
                <td className="small">{cell(r.failure_mode)}</td>
                <td className="small">{cell(r.root_cause)}</td>
                <td className="num">{fmtMoney(r.cost)}</td>
                <td className="num">{r.downtime_hrs ? `${fmtNum(r.downtime_hrs, 1)} h` : ""}</td>
                <td className="small">{r.facts_total ? `${r.facts_verified}/${r.facts_total}` : "–"}</td>
              </tr>
            ))}
            {data && data.items.length === 0 && <tr><td colSpan={9} className="muted">No work orders match.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="pager">
        {data && <span>{data.total.toLocaleString()} work orders</span>}
        <button className="btn small" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
        <span>Page {page} of {pages}</span>
        <button className="btn small" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
      </div>
      {order && <WorkOrderDrawer order={order} onClose={() => setOrder(null)} />}
    </>
  );
}
