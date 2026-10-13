import { useEffect, useState } from "react";
import { api, qs } from "../api";
import { Empty, Select, fmtNum, useApp } from "../components/ui";

interface Summary { work_orders: number; failure_events: number; downtime_hrs: number; failure_cost: number; verified_share: number; period: { start: string | null; end: string | null } }
interface Rank { key: string; name: string; events: number; share: number; cumulative_share: number; evidence_orders: string[]; [k: string]: unknown }
interface BadActors { metric: string; unit: string; total: number; ranking: Rank[]; insight: string }
interface Recurrence { recurrences: { asset: string; label: string; events_in_window: number; first: string; last: string; cost_rs: number; known_causes: Record<string, number>; evidence_orders: string[] }[] }
interface Spares { recommendations: { material: string; description: string; criticality: string; avg_monthly_use: number; suggested_min_stock: number; uom: string; breakdown_downtime_hrs: number; used_on_assets: string[] }[] }
interface Quality { work_type_mislabels: { count: number }; unresolved_assets: { count: number }; orders_posted_on_multiple_dates: { count: number } }

const METRIC_UNIT: Record<string, string> = { downtime: "hours of breakdown downtime", cost: "rupees of failure cost", frequency: "failure events" };

export default function Overview() {
  const { dataVersion, go, meta, toast, watchJob } = useApp();
  const [sum, setSum] = useState<Summary | null>(null);
  const [metric, setMetric] = useState("downtime");
  const [groupBy, setGroupBy] = useState("asset");
  const [area, setArea] = useState("");
  const [bad, setBad] = useState<BadActors | null>(null);
  const [rec, setRec] = useState<Recurrence | null>(null);
  const [spares, setSpares] = useState<Spares | null>(null);
  const [quality, setQuality] = useState<Quality | null>(null);

  useEffect(() => {
    api.get<Summary>("/api/insights/summary").then(setSum).catch(() => {});
    api.get<Recurrence>("/api/insights/recurrence?window_days=60&min_events=3").then(setRec).catch(() => {});
    api.get<Quality>("/api/insights/quality").then(setQuality).catch(() => {});
  }, [dataVersion]);
  useEffect(() => {
    api.get<BadActors>(`/api/insights/bad-actors${qs({ metric, group_by: groupBy, area, top: 12 })}`).then(setBad).catch(() => {});
    api.get<Spares>(`/api/insights/spares${qs({ area })}`).then(setSpares).catch(() => {});
  }, [metric, groupBy, area, dataVersion]);

  async function loadDemo() {
    try {
      const r = await api.post<{ job: never }>("/api/workspace/demo");
      watchJob(r.job);
      toast("Loading the demo rice plant…");
    } catch (e) { toast((e as Error).message, true); }
  }

  if (sum && sum.work_orders === 0) {
    return (
      <Empty title="No maintenance history yet"
        actions={<>
          <button className="btn primary" onClick={() => go("upload")}>Upload a work-order export</button>
          <button className="btn" onClick={loadDemo}>Load the demo rice plant</button>
        </>}>
        Upload an SAP PM (or any CMMS) export as Excel or CSV. Plant Memory turns it into a knowledge graph of what fails, why, and what fixed it.
      </Empty>
    );
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Where the plant is losing time</h1>
          <p>{sum?.period.start ? `Work orders from ${sum.period.start} to ${sum.period.end}.` : ""} Every figure links back to the work orders behind it.</p>
        </div>
      </div>

      {sum && (
        <div className="figures">
          <div className="figure"><b>{fmtNum(sum.work_orders)}</b><span>work orders</span></div>
          <div className="figure"><b>{fmtNum(sum.failure_events)}</b><span>failure events</span></div>
          <div className="figure"><b>{fmtNum(sum.downtime_hrs, 0)} h</b><span>breakdown downtime</span></div>
          <div className="figure"><b>{Math.round(sum.verified_share * 100)}%</b><span>of facts verified by technicians</span></div>
        </div>
      )}

      <section className="section">
        <h2>Bad actors</h2>
        <p className="lede">{bad?.insight} The dark bars are the few that drive 80% of the total.</p>
        <div className="filters">
          <label className="field"><span>Measure</span>
            <div className="seg" role="group">
              {["downtime", "cost", "frequency"].map((m) => (
                <button key={m} aria-pressed={metric === m} onClick={() => setMetric(m)}>{m === "frequency" ? "Failures" : m[0].toUpperCase() + m.slice(1)}</button>
              ))}
            </div>
          </label>
          <Select label="Group by" value={groupBy} onChange={setGroupBy} any={null}
            options={[{ value: "asset", label: "Asset" }, { value: "failure_mode", label: "Failure mode" }, { value: "asset_failure_mode", label: "Asset and failure mode" }]} />
          <Select label="Process area" value={area} onChange={setArea} options={(meta?.areas ?? []).map((a) => ({ value: a.code, label: a.label }))} />
        </div>
        {bad && bad.ranking.length > 0 ? <Pareto data={bad} unit={METRIC_UNIT[metric]} onPick={() => go("graph")} /> : <p className="muted">No failure events for this selection.</p>}
      </section>

      <div className="two section">
        <section>
          <h2>Repeat failures</h2>
          <p className="lede">The same failure on the same asset three or more times within 60 days.</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Asset</th><th>Failure mode</th><th className="num">Events</th><th>Window</th><th>Known causes</th></tr></thead>
              <tbody>
                {(rec?.recurrences ?? []).slice(0, 10).map((r, i) => (
                  <tr key={i}>
                    <td>{r.asset}</td><td>{r.label}</td><td className="num">{r.events_in_window}</td>
                    <td className="small">{r.first} to {r.last}</td>
                    <td className="small">{Object.keys(r.known_causes).length ? Object.entries(r.known_causes).map(([k, v]) => `${k} (${v})`).join(", ") : <span className="muted">none recorded</span>}</td>
                  </tr>
                ))}
                {rec && rec.recurrences.length === 0 && <tr><td colSpan={5} className="muted">No repeat failures in any 60-day window.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>
        <section>
          <h2>Spares to keep on the shelf</h2>
          <p className="lede">Minimum stock for a 30-day lead time, ranked by the breakdown downtime each part is linked to.</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Part</th><th className="num">Min stock</th><th className="num">Downtime</th></tr></thead>
              <tbody>
                {(spares?.recommendations ?? []).slice(0, 8).map((s) => (
                  <tr key={s.material}>
                    <td>{s.description}<div className="muted small">{s.used_on_assets.slice(0, 2).join(", ")}</div></td>
                    <td className="num">{s.suggested_min_stock} {s.uom}</td>
                    <td className="num">{fmtNum(s.breakdown_downtime_hrs, 1)} h</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {quality && (
        <section className="section">
          <h2>Data quality</h2>
          <p className="lede">Problems found in the export itself. Fixing them in SAP makes every future analysis more reliable.</p>
          <div className="row">
            <span className="chip">{quality.work_type_mislabels.count} breakdowns recorded as Preventive work</span>
            <span className="chip">{quality.unresolved_assets.count} work orders with no recognised asset</span>
            <span className="chip">{quality.orders_posted_on_multiple_dates.count} orders posted on several dates</span>
            <button className="btn small" onClick={() => go("data")}>Open work orders</button>
          </div>
        </section>
      )}
    </>
  );
}

function Pareto({ data, unit, onPick }: { data: BadActors; unit: string; onPick: () => void }) {
  const rows = data.ranking;
  const W = 900, H = 320, L = 70, R = 46, T = 14, B = 112;
  const iw = W - L - R, ih = H - T - B;
  const max = Math.max(...rows.map((r) => Number(r[data.metric]) || 0), 1);
  const bw = iw / rows.length;
  const y = (v: number) => T + ih - (v / max) * ih;
  const yc = (c: number) => T + ih - c * ih;
  const cut = rows.findIndex((r) => r.cumulative_share >= 0.8);
  const path = rows.map((r, i) => `${i ? "L" : "M"}${L + bw * i + bw / 2},${yc(r.cumulative_share)}`).join(" ");
  return (
    <div className="box" style={{ padding: "8px 8px 0" }}>
      <svg className="pareto" viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`Pareto chart of ${unit}`}>
        <line className="axis" x1={L} x2={W - R} y1={T + ih} y2={T + ih} />
        {[0, 0.5, 1].map((t) => <text key={t} x={L - 8} y={y(max * t) + 4} textAnchor="end">{fmtNum(max * t)}</text>)}
        {[0.5, 0.8, 1].map((t) => <text key={t} x={W - R + 6} y={yc(t) + 4}>{t * 100}%</text>)}
        <line className="eighty" x1={L} x2={W - R} y1={yc(0.8)} y2={yc(0.8)} />
        {rows.map((r, i) => {
          const v = Number(r[data.metric]) || 0;
          const x = L + bw * i + bw * 0.15;
          return (
            <g key={r.key} style={{ cursor: "pointer" }} onClick={onPick}>
              <rect className={`bar ${i <= cut ? "top" : ""}`} x={x} y={y(v)} width={bw * 0.7} height={T + ih - y(v)} rx={2}>
                <title>{`${r.name}: ${fmtNum(v, 1)} (${Math.round(r.share * 100)}%), ${r.events} events. Orders ${r.evidence_orders.slice(0, 3).join(", ")}`}</title>
              </rect>
              <text x={L + bw * i + bw / 2} y={T + ih + 14} textAnchor="end" transform={`rotate(-35 ${L + bw * i + bw / 2} ${T + ih + 14})`}>
                {r.name.length > 26 ? r.name.slice(0, 25) + "…" : r.name}
              </text>
            </g>
          );
        })}
        <path className="cum" d={path} />
        {rows.map((r, i) => <circle key={r.key} cx={L + bw * i + bw / 2} cy={yc(r.cumulative_share)} r={2.5} fill="currentColor" />)}
      </svg>
      <p className="muted small" style={{ padding: "0 8px 10px" }}>Bars: {unit}. Line: cumulative share. Dashed line: 80%.</p>
    </div>
  );
}
