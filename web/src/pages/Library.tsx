import { useEffect, useState } from "react";
import { api, KIND_LABEL, type Job } from "../api";
import { useApp } from "../components/ui";

interface Asset { asset_id: string; name: string; area: string; asset_class: string; aliases: string[]; work_orders: number; failure_events: number }
interface Term { id: string; label: string; synonyms: string[]; builtin: boolean; editable_synonyms: string[]; category?: string; iso_mechanism?: string }
type Taxonomy = Record<string, Term[]>;
const EMPTY = { asset_id: "", name: "", area: "", asset_class: "", aliases: "" };

export default function Library() {
  const [tab, setTab] = useState<"assets" | "terms">("assets");
  const [changed, setChanged] = useState(false);
  const { watchJob, toast, job } = useApp();
  async function rebuild() {
    try { watchJob(await api.post<Job>("/api/rebuild")); setChanged(false); } catch (e) { toast((e as Error).message, true); }
  }
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Assets and vocabulary</h1>
          <p>The asset register and the words Plant Memory understands. Edit them to match your plant; changes apply to every work order on the next rebuild.</p>
        </div>
      </div>
      {changed && (
        <div className="notice warn row" style={{ marginBottom: 14 }}>
          <span style={{ flex: 1 }}>You changed the register or vocabulary. Rebuild the graph so existing work orders are read again.</span>
          <button className="btn small primary" onClick={rebuild} disabled={job?.state === "running"}>Rebuild now</button>
        </div>
      )}
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "assets"} onClick={() => setTab("assets")}>Asset register</button>
        <button role="tab" aria-selected={tab === "terms"} onClick={() => setTab("terms")}>Vocabulary</button>
      </div>
      {tab === "assets" ? <Assets onChange={() => setChanged(true)} /> : <Terms onChange={() => setChanged(true)} />}
    </>
  );
}

function Assets({ onChange }: { onChange: () => void }) {
  const { dataVersion, toast, refreshMeta, meta } = useApp();
  const [items, setItems] = useState<Asset[]>([]);
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);
  const load = () => api.get<{ assets: Asset[] }>("/api/assets").then((r) => setItems(r.assets)).catch(() => {});
  useEffect(() => { load(); }, [dataVersion]);

  async function save() {
    const body = { ...form, asset_id: form.asset_id.trim().toUpperCase(), aliases: form.aliases.split(",").map((s) => s.trim()).filter(Boolean) };
    try {
      if (editing) await api.put(`/api/assets/${encodeURIComponent(editing)}`, body);
      else await api.post("/api/assets", body);
      toast(editing ? "Asset updated" : "Asset added");
      setForm(EMPTY); setEditing(null); load(); refreshMeta(); onChange();
    } catch (e) { toast((e as Error).message, true); }
  }
  async function remove(a: Asset) {
    if (!window.confirm(`Delete ${a.name} from the register? Its work orders stay as evidence but will no longer be linked to it after a rebuild.`)) return;
    try { await api.del(`/api/assets/${encodeURIComponent(a.asset_id)}`); toast("Asset deleted"); load(); refreshMeta(); onChange(); }
    catch (e) { toast((e as Error).message, true); }
  }
  const edit = (a: Asset) => { setEditing(a.asset_id); setForm({ asset_id: a.asset_id, name: a.name, area: a.area, asset_class: a.asset_class, aliases: a.aliases.join(", ") }); };

  return (
    <>
      <div className="box" style={{ marginBottom: 14 }}>
        <h3 style={{ marginBottom: 10 }}>{editing ? `Edit ${editing}` : "Add an asset"}</h3>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <label className="field"><span>Asset ID (functional location)</span><input type="text" value={form.asset_id} disabled={!!editing} onChange={(e) => setForm({ ...form, asset_id: e.target.value })} placeholder="PARB-DRY-ELV-01" /></label>
          <label className="field"><span>Name</span><input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Dryer elevator" /></label>
          <label className="field"><span>Area</span><input type="text" value={form.area} list="area-list" onChange={(e) => setForm({ ...form, area: e.target.value.toUpperCase() })} style={{ width: 90 }} /></label>
          <label className="field"><span>Equipment type</span><input type="text" value={form.asset_class} onChange={(e) => setForm({ ...form, asset_class: e.target.value })} placeholder="bucket_elevator" style={{ width: 150 }} /></label>
          <label className="field" style={{ flex: 1, minWidth: 220 }}><span>How people write it (comma-separated)</span><input type="text" value={form.aliases} onChange={(e) => setForm({ ...form, aliases: e.target.value })} placeholder="Dryer Elev, LSU elev" /></label>
          <button className="btn primary" disabled={!form.asset_id.trim() || !form.name.trim()} onClick={save}>{editing ? "Save changes" : "Add asset"}</button>
          {editing && <button className="btn" onClick={() => { setEditing(null); setForm(EMPTY); }}>Cancel</button>}
        </div>
        <datalist id="area-list">{meta?.areas.map((a) => <option key={a.code} value={a.code}>{a.label}</option>)}</datalist>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Asset ID</th><th>Name</th><th>Area</th><th>Type</th><th>Also written as</th><th className="num">Orders</th><th className="num">Failures</th><th /></tr></thead>
          <tbody>
            {items.map((a) => (
              <tr key={a.asset_id}>
                <td className="order">{a.asset_id}</td><td>{a.name}</td><td>{a.area}</td><td className="small">{a.asset_class.replace(/_/g, " ")}</td>
                <td className="small muted">{a.aliases.join(", ")}</td>
                <td className="num">{a.work_orders}</td><td className="num">{a.failure_events}</td>
                <td className="num" style={{ whiteSpace: "nowrap" }}>
                  <button className="btn small" onClick={() => edit(a)}>Edit</button>{" "}
                  <button className="btn small bad" onClick={() => remove(a)}>Delete</button>
                </td>
              </tr>
            ))}
            {items.length === 0 && <tr><td colSpan={8} className="muted">The register is empty. Upload an export and confirm the suggested assets, or add them here.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Terms({ onChange }: { onChange: () => void }) {
  const { toast, refreshMeta } = useApp();
  const [tax, setTax] = useState<Taxonomy | null>(null);
  const [kind, setKind] = useState("failure_mode");
  const [newTerm, setNewTerm] = useState({ label: "", synonyms: "" });
  const [syn, setSyn] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState("");
  const load = () => api.get<Taxonomy>("/api/taxonomy").then(setTax).catch(() => {});
  useEffect(() => { load(); }, []);
  const done = (msg: string) => { toast(msg); load(); refreshMeta(); onChange(); };

  async function addTerm() {
    try {
      await api.post(`/api/taxonomy/${kind}`, { label: newTerm.label, synonyms: newTerm.synonyms.split(",").map((s) => s.trim()).filter(Boolean) });
      setNewTerm({ label: "", synonyms: "" }); done("Term added");
    } catch (e) { toast((e as Error).message, true); }
  }
  async function addSyn(t: Term) {
    try { await api.post(`/api/taxonomy/${kind}/${t.id}/synonyms`, { synonym: syn[t.id] }); setSyn({ ...syn, [t.id]: "" }); done("Synonym added"); }
    catch (e) { toast((e as Error).message, true); }
  }
  async function remove(t: Term, synonym?: string) {
    try { await api.del(`/api/taxonomy/${kind}/${t.id}${synonym ? `?synonym=${encodeURIComponent(synonym)}` : ""}`); done(synonym ? "Synonym removed" : "Term removed"); }
    catch (e) { toast((e as Error).message, true); }
  }
  const terms = (tax?.[kind] ?? []).filter((t) => !filter || (t.label + " " + t.synonyms.join(" ")).toLowerCase().includes(filter.toLowerCase()));

  return (
    <>
      <div className="filters">
        <label className="field"><span>Kind</span>
          <div className="seg" role="group">
            {Object.keys(tax ?? {}).map((k) => <button key={k} aria-pressed={kind === k} onClick={() => setKind(k)}>{KIND_LABEL[k]}</button>)}
          </div>
        </label>
        <label className="field"><span>Find</span><input type="search" value={filter} onChange={(e) => setFilter(e.target.value)} /></label>
      </div>
      <div className="box row" style={{ marginBottom: 14, alignItems: "flex-end" }}>
        <label className="field"><span>New {KIND_LABEL[kind]?.toLowerCase()}</span><input type="text" value={newTerm.label} onChange={(e) => setNewTerm({ ...newTerm, label: e.target.value })} placeholder="e.g. Breather choked" /></label>
        <label className="field" style={{ flex: 1 }}><span>Ways people write it (comma-separated)</span><input type="text" value={newTerm.synonyms} onChange={(e) => setNewTerm({ ...newTerm, synonyms: e.target.value })} placeholder="breather blocked, breather jam" /></label>
        <button className="btn primary" disabled={!newTerm.label.trim()} onClick={addTerm}>Add term</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th style={{ width: 220 }}>Term</th><th>Recognised wording</th><th style={{ width: 260 }}>Add wording</th></tr></thead>
          <tbody>
            {terms.map((t) => (
              <tr key={t.id}>
                <td>
                  <b>{t.label}</b>
                  <div className="muted small">{t.builtin ? (t.iso_mechanism ? `ISO 14224: ${t.iso_mechanism}` : t.category ?? "built in") : "added for this plant"}</div>
                  {!t.builtin && <button className="btn small bad" style={{ marginTop: 6 }} onClick={() => remove(t)}>Remove term</button>}
                </td>
                <td className="small">
                  {t.synonyms.map((s) => (
                    <span key={s} className="chip" style={{ margin: "0 4px 4px 0" }}>
                      {s}{t.editable_synonyms.includes(s) && <button aria-label={`Remove ${s}`} onClick={() => remove(t, s)}>×</button>}
                    </span>
                  ))}
                </td>
                <td>
                  <div className="row">
                    <input type="text" value={syn[t.id] ?? ""} onChange={(e) => setSyn({ ...syn, [t.id]: e.target.value })} placeholder="another wording" style={{ width: 150 }}
                      onKeyDown={(e) => e.key === "Enter" && syn[t.id]?.trim() && addSyn(t)} />
                    <button className="btn small" disabled={!syn[t.id]?.trim()} onClick={() => addSyn(t)}>Add</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
