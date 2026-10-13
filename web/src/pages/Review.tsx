import { useEffect, useState } from "react";
import { api, qs, KIND_LABEL, type Fact } from "../api";
import { Empty, FactRow, Select, useApp } from "../components/ui";

interface Queue { items: Fact[]; proposed_total: number; proposed_by_kind: Record<string, number> }
interface Gap { question_id: string; asset: string; failure_mode_label: string; question: string; event_ids: string[]; status: string; answer?: string; answered_by?: string }
interface Drift { proposal_id: string; kind: string; phrase: string; event_ids: string[]; count: number; status: string }

export default function Review() {
  const { dataVersion, meta } = useApp();
  const [tab, setTab] = useState<"facts" | "gaps" | "drift">("facts");
  const [counts, setCounts] = useState({ facts: 0, gaps: 0, drift: 0 });
  useEffect(() => {
    Promise.all([
      api.get<Queue>("/api/review/queue?limit=1"), api.get<{ items: Gap[] }>("/api/gaps"), api.get<{ items: Drift[] }>("/api/drift"),
    ]).then(([q, g, d]) => setCounts({ facts: q.proposed_total, gaps: g.items.length, drift: d.items.length })).catch(() => {});
  }, [dataVersion]);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Review</h1>
          <p>Nothing counts as plant knowledge until a person has checked it. Lowest-confidence facts come first; each one shows the words it was read from.</p>
        </div>
      </div>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "facts"} onClick={() => setTab("facts")}>Facts to check<span className="n">{counts.facts}</span></button>
        <button role="tab" aria-selected={tab === "gaps"} onClick={() => setTab("gaps")}>Questions for technicians<span className="n">{counts.gaps}</span></button>
        <button role="tab" aria-selected={tab === "drift"} onClick={() => setTab("drift")}>New terms<span className="n">{counts.drift}</span></button>
      </div>
      {tab === "facts" && <FactsQueue />}
      {tab === "gaps" && <Gaps />}
      {tab === "drift" && <DriftList vocab={meta?.vocab} />}
    </>
  );
}

function FactsQueue() {
  const { meta, dataVersion, reviewer, toast, bump } = useApp();
  const [kind, setKind] = useState("");
  const [asset, setAsset] = useState("");
  const [q, setQ] = useState<Queue | null>(null);
  const [threshold, setThreshold] = useState(0.95);
  useEffect(() => {
    api.get<Queue>(`/api/review/queue${qs({ kind, asset, limit: 40 })}`).then(setQ).catch(() => {});
  }, [kind, asset, dataVersion]);

  async function bulk() {
    if (!reviewer.trim()) { toast("Enter your name at the top first.", true); return; }
    if (!window.confirm(`Verify every proposed fact with confidence ${Math.round(threshold * 100)}% or higher, in your name?`)) return;
    try {
      const r = await api.post<{ verified: number }>("/api/facts/bulk-approve", { min_confidence: threshold, by: reviewer, kind: kind || undefined });
      toast(`Verified ${r.verified} facts`);
      bump();
    } catch (e) { toast((e as Error).message, true); }
  }

  return (
    <>
      <div className="filters">
        <Select label="Field" value={kind} onChange={setKind}
          options={Object.entries(q?.proposed_by_kind ?? {}).map(([k, n]) => ({ value: k, label: `${KIND_LABEL[k] ?? k} (${n})` }))} />
        <Select label="Asset" value={asset} onChange={setAsset} options={(meta?.assets ?? []).map((a) => ({ value: a.asset_id, label: a.name }))} />
        <span style={{ flex: 1 }} />
        <label className="field"><span>Verify everything at or above</span>
          <div className="row">
            <select value={threshold} onChange={(e) => setThreshold(Number(e.target.value))}>
              {[0.95, 0.9, 0.85].map((t) => <option key={t} value={t}>{Math.round(t * 100)}% confidence</option>)}
            </select>
            <button className="btn" onClick={bulk}>Verify in bulk</button>
          </div>
        </label>
      </div>
      {q && q.items.length === 0 ? (
        <Empty title="Nothing waiting for review">Every extracted fact has been verified or rejected.</Empty>
      ) : (
        <div className="box" style={{ padding: "0 16px" }}>
          {q?.items.map((f) => <FactRow key={f.fact_id + f.status} fact={f} showOrder />)}
        </div>
      )}
      {q && q.proposed_total > q.items.length && <p className="muted small" style={{ marginTop: 10 }}>Showing the {q.items.length} least certain of {q.proposed_total} facts. Verified facts drop off the list.</p>}
    </>
  );
}

function Gaps() {
  const { dataVersion, reviewer, toast, bump } = useApp();
  const [items, setItems] = useState<Gap[] | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [showAnswered, setShowAnswered] = useState(false);
  useEffect(() => {
    api.get<{ items: Gap[] }>(`/api/gaps?status=${showAnswered ? "answered" : "open"}`).then((r) => setItems(r.items)).catch(() => {});
  }, [dataVersion, showAnswered]);

  async function answer(g: Gap) {
    if (!reviewer.trim()) { toast("Enter your name at the top first, so the answer is credited.", true); return; }
    try {
      const r = await api.post<{ mapped_root_cause: string | null; drift_proposal: string | null }>(`/api/gaps/${g.question_id}/answer`, { answer: answers[g.question_id], by: reviewer });
      toast(r.mapped_root_cause ? `Recorded as a cause: ${r.mapped_root_cause.replace(/_/g, " ")}` : "Recorded. It names a new cause, so it was added to New terms.");
      bump();
    } catch (e) { toast((e as Error).message, true); }
  }

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <p className="muted" style={{ flex: 1 }}>Where an asset keeps failing the same way and nobody wrote down why, ask the people who know. Answers are recorded as technician knowledge.</p>
        <label className="check"><input type="checkbox" checked={showAnswered} onChange={(e) => setShowAnswered(e.target.checked)} /> Show answered</label>
      </div>
      {items && items.length === 0 && <Empty title={showAnswered ? "No answers yet" : "No open questions"}>{showAnswered ? "" : "Every repeating failure has a recorded cause."}</Empty>}
      <div className="stack">
        {items?.map((g) => (
          <div key={g.question_id} className="box stack">
            <div className="row"><b>{g.asset}</b><span className="muted">{g.failure_mode_label}</span><span className="muted small" style={{ marginLeft: "auto" }}>{g.event_ids.length} work orders</span></div>
            <p>{g.question}</p>
            {g.status === "answered" ? (
              <p className="quote">“{g.answer}” <span className="muted">by {g.answered_by}</span></p>
            ) : (
              <>
                <textarea rows={2} placeholder="e.g. The head pulley shifts after belt jointing; check alignment with a straight edge."
                  value={answers[g.question_id] ?? ""} onChange={(e) => setAnswers({ ...answers, [g.question_id]: e.target.value })} />
                <div><button className="btn primary small" disabled={!answers[g.question_id]?.trim()} onClick={() => answer(g)}>Save answer</button></div>
              </>
            )}
          </div>
        ))}
      </div>
    </>
  );
}

function DriftList({ vocab }: { vocab?: Record<string, { id: string; label: string }[]> }) {
  const { dataVersion, reviewer, toast, bump } = useApp();
  const [items, setItems] = useState<Drift[] | null>(null);
  const [form, setForm] = useState<Record<string, { label: string; synonyms: string; to: string; mode: "new" | "map" }>>({});
  useEffect(() => { api.get<{ items: Drift[] }>("/api/drift").then((r) => setItems(r.items)).catch(() => {}); }, [dataVersion]);

  const f = (d: Drift) => form[d.proposal_id] ?? { label: d.phrase[0].toUpperCase() + d.phrase.slice(1), synonyms: "", to: "", mode: "new" as const };
  const set = (d: Drift, patch: Partial<ReturnType<typeof f>>) => setForm({ ...form, [d.proposal_id]: { ...f(d), ...patch } });

  async function decide(d: Drift, decision: "approve" | "map" | "reject") {
    if (!reviewer.trim()) { toast("Enter your name at the top first.", true); return; }
    const v = f(d);
    try {
      await api.post(`/api/drift/${d.proposal_id}/decision`, {
        decision, by: reviewer, label: v.label, to: decision === "map" ? v.to : undefined,
        synonyms: v.synonyms.split(",").map((s) => s.trim()).filter(Boolean),
      });
      toast(decision === "reject" ? "Dismissed" : "Vocabulary updated. Rebuild the graph to apply it.");
      bump();
    } catch (e) { toast((e as Error).message, true); }
  }

  return (
    <>
      <p className="muted" style={{ marginBottom: 12 }}>Words in new work orders that the vocabulary does not know yet. Add them as a new term, treat them as another way of saying an existing term, or dismiss them.</p>
      {items && items.length === 0 && <Empty title="No new terms">Every work order fits the current vocabulary.</Empty>}
      <div className="stack">
        {items?.map((d) => {
          const v = f(d);
          return (
            <div key={d.proposal_id} className="box stack">
              <div className="row"><b>“{d.phrase}”</b><span className="muted">{KIND_LABEL[d.kind] ?? d.kind}</span><span className="muted small" style={{ marginLeft: "auto" }}>seen in {d.count} work order{d.count === 1 ? "" : "s"}: {d.event_ids.slice(0, 4).join(", ")}</span></div>
              <div className="seg" role="group" style={{ alignSelf: "flex-start" }}>
                <button aria-pressed={v.mode === "new"} onClick={() => set(d, { mode: "new" })}>New term</button>
                <button aria-pressed={v.mode === "map"} onClick={() => set(d, { mode: "map" })}>Same as an existing term</button>
              </div>
              {v.mode === "new" ? (
                <div className="row">
                  <label className="field"><span>Name</span><input type="text" value={v.label} onChange={(e) => set(d, { label: e.target.value })} /></label>
                  <label className="field" style={{ flex: 1 }}><span>Other ways people write it (comma-separated)</span><input type="text" value={v.synonyms} onChange={(e) => set(d, { synonyms: e.target.value })} /></label>
                  <button className="btn primary" style={{ alignSelf: "flex-end" }} onClick={() => decide(d, "approve")}>Add term</button>
                  <button className="btn bad" style={{ alignSelf: "flex-end" }} onClick={() => decide(d, "reject")}>Dismiss</button>
                </div>
              ) : (
                <div className="row">
                  <label className="field"><span>Existing term</span>
                    <select value={v.to} onChange={(e) => set(d, { to: e.target.value })}>
                      <option value="">Choose…</option>
                      {(vocab?.[d.kind] ?? []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                    </select>
                  </label>
                  <button className="btn primary" style={{ alignSelf: "flex-end" }} disabled={!v.to} onClick={() => decide(d, "map")}>Save as synonym</button>
                  <button className="btn bad" style={{ alignSelf: "flex-end" }} onClick={() => decide(d, "reject")}>Dismiss</button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}
