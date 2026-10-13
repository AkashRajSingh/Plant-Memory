import { useEffect, useRef, useState } from "react";
import { api, type Job } from "../api";
import { JobBar, useApp } from "../components/ui";

interface UploadMeta { upload_id: string; filename: string; rows: number; columns: string[]; suggested_mapping: Record<string, string>; sample: Record<string, string | null>[] }
interface Candidate { asset_id: string; name: string; area: string; asset_class: string; aliases: string[]; work_orders: number; examples: string[]; from: string; include?: boolean }
interface Discovery { work_orders: number; resolved: number; unresolved: number; known_assets: number; areas: string[]; candidates: Candidate[] }
interface Source { id: string; filename: string; rows: number; added_at: string }
interface SettingsResp { active: { mode: string; label: string; enabled: boolean } }

const FIELDS: [string, string, boolean][] = [
  ["order", "Work order number", true], ["posting_date", "Posting / completion date", true], ["description", "Work description (short text)", true],
  ["material", "Material code", false], ["material_description", "Material description", false], ["quantity", "Quantity", false],
  ["uom", "Unit of measure", false], ["cost", "Cost", false], ["work_type", "Work type", false], ["planning_department", "Planning department", false],
  ["functional_location", "Functional location / equipment ID", false], ["symptom", "Symptom / problem", false], ["root_cause", "Root cause (RCA)", false],
  ["cause_category", "Cause category", false], ["action_taken", "Action taken", false], ["downtime_hrs", "Downtime (hours)", false],
  ["breakdown", "Breakdown flag", false], ["remarks", "Technician remarks", false], ["technician", "Technician", false],
  ["movement_type", "Movement type", false], ["cost_center", "Cost center", false], ["gl_account", "G/L account", false], ["storage_location", "Storage location", false],
];
const STEPS = ["Choose a file", "Match columns", "Confirm assets", "Build the graph"];

export default function Upload() {
  const { toast, watchJob, job, refreshMeta, dataVersion, go } = useApp();
  const [step, setStep] = useState(0);
  const [up, setUp] = useState<UploadMeta | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [disc, setDisc] = useState<Discovery | null>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [sources, setSources] = useState<Source[]>([]);
  const [privacy, setPrivacy] = useState<SettingsResp["active"] | null>(null);
  const [useLlm, setUseLlm] = useState(true);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.get<{ sources: Source[] }>("/api/sources").then((r) => setSources(r.sources)).catch(() => {});
    api.get<SettingsResp>("/api/settings").then((s) => setPrivacy(s.active)).catch(() => {});
  }, [dataVersion, step]);

  async function sendFile(f: File) {
    setBusy(true);
    const fd = new FormData();
    fd.append("file", f);
    try {
      const meta = await api.post<UploadMeta>("/api/uploads", fd);
      setUp(meta);
      setMapping(meta.suggested_mapping);
      setStep(1);
    } catch (e) { toast((e as Error).message, true); }
    setBusy(false);
  }

  const used = new Set(Object.values(mapping).filter(Boolean));
  const missing = FIELDS.filter(([k, , req]) => req && !used.has(k));

  async function toAssets() {
    if (!up) return;
    setBusy(true);
    try {
      const d = await api.post<Discovery>(`/api/uploads/${up.upload_id}/discover`, { mapping });
      d.candidates.forEach((c) => (c.include = c.work_orders >= 2));
      setDisc(d);
      setStep(2);
    } catch (e) { toast((e as Error).message, true); }
    setBusy(false);
  }

  async function build() {
    if (!up || !disc) return;
    setBusy(true);
    try {
      const assets = disc.candidates.filter((c) => c.include);
      const r = await api.post<{ job: Job }>(`/api/uploads/${up.upload_id}/ingest`, { mapping, assets, llm: useLlm ? "auto" : "off" });
      watchJob(r.job);
      refreshMeta();
      setStep(3);
    } catch (e) { toast((e as Error).message, true); }
    setBusy(false);
  }

  const editCand = (i: number, patch: Partial<Candidate>) =>
    setDisc((d) => d && { ...d, candidates: d.candidates.map((c, j) => (j === i ? { ...c, ...patch } : c)) });

  async function removeSource(id: string) {
    try { await api.del(`/api/sources/${id}`); toast("File removed. Rebuild to update the graph."); setSources((s) => s.filter((x) => x.id !== id)); }
    catch (e) { toast((e as Error).message, true); }
  }
  async function rebuild() {
    try { const j = await api.post<Job>("/api/rebuild", { llm: useLlm ? "auto" : "off" }); watchJob(j); } catch (e) { toast((e as Error).message, true); }
  }
  async function workspace(kind: "demo" | "fresh") {
    if (kind === "fresh" && !window.confirm("Start an empty workspace? This removes all files, assets and graph data (the audit log is kept).")) return;
    try {
      const r = await api.post<{ job?: Job }>(`/api/workspace/${kind}`);
      if (r.job) watchJob(r.job);
      refreshMeta();
      toast(kind === "demo" ? "Loading the demo rice plant…" : "Empty workspace ready. Upload your export.");
      setStep(0); setUp(null); setDisc(null);
    } catch (e) { toast((e as Error).message, true); }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Upload work orders</h1>
          <p>Add an Excel or CSV export from SAP PM or any CMMS. New files are added to the existing history; when the same work order appears twice, the newer file wins.</p>
        </div>
      </div>

      <div className="steps" aria-label="Upload progress">
        {STEPS.map((s, i) => (
          <div key={s} className={`step ${i === step ? "on" : i < step ? "done" : ""}`} aria-current={i === step ? "step" : undefined}>
            <b>{i < step ? "✓" : i + 1}</b>{s}
          </div>
        ))}
      </div>

      {step === 0 && (
        <div className="stack">
          {privacy && (
            <div className={`notice ${privacy.mode === "api" && privacy.enabled ? "warn" : ""}`}>
              {privacy.mode === "off" && <>Extraction mode: <b>Local only</b>. Nothing leaves this machine; work orders are read with plant-specific rules.</>}
              {privacy.mode === "local" && <>Extraction mode: <b>Local LLM</b> ({privacy.label}). Text stays on your machine or network.</>}
              {privacy.mode === "api" && <>Extraction mode: <b>LLM API</b> ({privacy.label}). Work-order text will be sent to this provider. </>}
              {" "}<button className="btn link" onClick={() => go("settings")}>Change privacy mode</button>
            </div>
          )}
          <div className={`drop ${over ? "over" : ""}`} role="button" tabIndex={0}
            onClick={() => fileRef.current?.click()} onKeyDown={(e) => e.key === "Enter" && fileRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) sendFile(f); }}>
            <h3>{busy ? "Reading the file…" : "Drop an .xlsx or .csv export here"}</h3>
            <p className="muted">or click to choose a file. One row per material line or per work order both work.</p>
            <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv" hidden onChange={(e) => e.target.files?.[0] && sendFile(e.target.files[0])} />
          </div>
        </div>
      )}

      {step === 1 && up && (
        <>
          <p style={{ marginBottom: 12 }}><b>{up.filename}</b>: {up.rows.toLocaleString()} rows, {up.columns.length} columns. Check what each column means. Columns set to “Ignore” are not used.</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Column in your file</th><th>Sample values</th><th>Means</th></tr></thead>
              <tbody>
                {up.columns.map((c) => (
                  <tr key={c}>
                    <td><b>{c}</b></td>
                    <td className="muted small">{up.sample.slice(0, 3).map((r) => r[c]).filter(Boolean).join("  |  ").slice(0, 90)}</td>
                    <td>
                      <select value={mapping[c] ?? ""} onChange={(e) => setMapping({ ...mapping, [c]: e.target.value })} aria-label={`Meaning of ${c}`}>
                        <option value="">Ignore</option>
                        {FIELDS.map(([k, lbl, req]) => (
                          <option key={k} value={k} disabled={used.has(k) && mapping[c] !== k}>{lbl}{req ? " (required)" : ""}</option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            {missing.length > 0 && <span className="req">Still needed: {missing.map(([, l]) => l).join(", ")}</span>}
            <span style={{ flex: 1 }} />
            <button className="btn" onClick={() => setStep(0)}>Choose another file</button>
            <button className="btn primary" disabled={missing.length > 0 || busy} onClick={toAssets}>{busy ? "Checking assets…" : "Continue to assets"}</button>
          </div>
        </>
      )}

      {step === 2 && disc && (
        <>
          <p style={{ marginBottom: 12 }}>
            {disc.resolved} of {disc.work_orders} work orders match an asset you already have.
            {disc.candidates.length > 0 ? " These look like equipment that is not in your register yet. Tick the ones to add, and fix names or areas if needed." : " Every work order matches a known asset."}
          </p>
          {disc.candidates.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Add</th><th>Asset ID</th><th>Name</th><th>Area</th><th>Also written as</th><th className="num">Orders</th></tr></thead>
                <tbody>
                  {disc.candidates.map((c, i) => (
                    <tr key={c.asset_id + i}>
                      <td><input type="checkbox" checked={!!c.include} onChange={(e) => editCand(i, { include: e.target.checked })} aria-label={`Add ${c.name}`} /></td>
                      <td><input type="text" value={c.asset_id} onChange={(e) => editCand(i, { asset_id: e.target.value })} style={{ width: 170 }} /></td>
                      <td><input type="text" value={c.name} onChange={(e) => editCand(i, { name: e.target.value })} style={{ width: 190 }} /></td>
                      <td><input type="text" value={c.area} onChange={(e) => editCand(i, { area: e.target.value.toUpperCase() })} style={{ width: 70 }} list="areas" /></td>
                      <td className="small muted" title={c.examples.join("\n")}>{c.aliases.join(", ")}</td>
                      <td className="num">{c.work_orders}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <datalist id="areas">{disc.areas.map((a) => <option key={a} value={a} />)}</datalist>
            </div>
          )}
          <div className="row" style={{ marginTop: 14 }}>
            <label className="check"><input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} disabled={privacy?.mode === "off"} />
              Use the LLM as a second reader {privacy?.mode === "off" ? "(off in local-only mode)" : `(${privacy?.label})`}</label>
            <span style={{ flex: 1 }} />
            <button className="btn" onClick={() => setStep(1)}>Back</button>
            <button className="btn primary" disabled={busy} onClick={build}>Add {disc.candidates.filter((c) => c.include).length} assets and build</button>
          </div>
        </>
      )}

      {step === 3 && (
        <div className="box stack">
          <h2>{job?.state === "done" ? "The knowledge graph is up to date" : job?.state === "failed" ? "The build failed" : "Building the knowledge graph"}</h2>
          <JobBar job={job} />
          {job?.state === "running" && job.stage === "llm" && <p className="muted small">LLM extraction runs in batches and respects free-tier rate limits. Results are cached, so a rebuild later is fast.</p>}
          {job && job.log.length > 0 && <pre className="small muted" style={{ whiteSpace: "pre-wrap", margin: 0, maxHeight: 160, overflow: "auto" }}>{job.log.slice(-6).join("\n")}</pre>}
          {job?.state === "done" && (
            <div className="row">
              <button className="btn primary" onClick={() => go("review")}>Review what was learned</button>
              <button className="btn" onClick={() => go("graph")}>Open the graph</button>
              <button className="btn" onClick={() => { setStep(0); setUp(null); setDisc(null); }}>Upload another file</button>
            </div>
          )}
        </div>
      )}

      <section className="section">
        <h2>Files in this workspace</h2>
        <p className="lede">The graph is built from all of these together. Raw rows are kept as evidence and never edited.</p>
        <div className="table-wrap">
          <table>
            <thead><tr><th>File</th><th className="num">Rows</th><th>Added</th><th /></tr></thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id}>
                  <td>{s.filename}</td><td className="num">{s.rows.toLocaleString()}</td><td className="small">{s.added_at.replace("T", " ")}</td>
                  <td className="num"><button className="btn small bad" onClick={() => removeSource(s.id)}>Remove</button></td>
                </tr>
              ))}
              {sources.length === 0 && <tr><td colSpan={4} className="muted">No files yet. The command-line demo data is used until you add one.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn" onClick={rebuild} disabled={job?.state === "running"}>Rebuild the graph</button>
          <button className="btn" onClick={() => workspace("demo")}>Load the demo rice plant</button>
          <button className="btn bad" onClick={() => workspace("fresh")}>Start an empty workspace</button>
        </div>
      </section>
    </>
  );
}
