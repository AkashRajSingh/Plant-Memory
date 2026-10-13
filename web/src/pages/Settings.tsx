import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../components/ui";

interface SettingsResp {
  llm_mode: "off" | "api" | "local";
  api: { provider: string; base_url: string; model: string; api_key: string; key_source: string };
  local: { base_url: string; model: string };
  batch_size: number;
}

const MODES: { id: SettingsResp["llm_mode"]; title: string; text: string }[] = [
  { id: "off", title: "Local only", text: "Work orders are read with plant-specific rules. Nothing leaves this machine. Best for sensitive data; weakest on unusual wording." },
  { id: "api", title: "LLM API", text: "A hosted model (Groq by default, or any OpenAI-compatible provider) reads work orders as a second opinion. Work-order text is sent to that provider." },
  { id: "local", title: "Local LLM", text: "A model running on this machine or your network, for example Ollama. Text stays in-house; needs a capable computer." },
];

export default function Settings() {
  const { toast, bump } = useApp();
  const [s, setS] = useState<SettingsResp | null>(null);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [store, setStore] = useState("");
  useEffect(() => {
    api.get<SettingsResp>("/api/settings").then(setS).catch(() => {});
    api.get<{ store: string }>("/api/status").then((r) => setStore(r.store)).catch(() => {});
  }, []);
  if (!s) return null;

  const save = async () => {
    try { setS(await api.put<SettingsResp>("/api/settings", s)); toast("Privacy settings saved"); bump(); setResult(null); }
    catch (e) { toast((e as Error).message, true); }
  };
  const test = async () => {
    setTesting(true);
    try { await api.put("/api/settings", s); setResult(await api.post("/api/settings/test")); bump(); }
    catch (e) { setResult({ ok: false, message: (e as Error).message }); }
    setTesting(false);
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Privacy and settings</h1>
          <p>Choose where work-order text is processed. Maintenance records often contain supplier names, costs and staff IDs, so this is the plant's decision.</p>
        </div>
      </div>
      <div className="modes" role="radiogroup" aria-label="Extraction mode">
        {MODES.map((m) => (
          <button key={m.id} className="mode" role="radio" aria-checked={s.llm_mode === m.id} onClick={() => setS({ ...s, llm_mode: m.id })}>
            <h3>{m.title}</h3>
            <p>{m.text}</p>
          </button>
        ))}
      </div>

      {s.llm_mode === "api" && (
        <div className="box section stack">
          <div className="row" style={{ alignItems: "flex-end" }}>
            <label className="field"><span>Provider name</span><input type="text" value={s.api.provider} onChange={(e) => setS({ ...s, api: { ...s.api, provider: e.target.value } })} /></label>
            <label className="field" style={{ flex: 1, minWidth: 260 }}><span>API base URL (OpenAI-compatible)</span><input type="text" value={s.api.base_url} onChange={(e) => setS({ ...s, api: { ...s.api, base_url: e.target.value } })} /></label>
            <label className="field"><span>Model</span><input type="text" value={s.api.model} onChange={(e) => setS({ ...s, api: { ...s.api, model: e.target.value } })} /></label>
          </div>
          <label className="field"><span>API key {s.api.key_source !== "none" ? `(currently from ${s.api.key_source})` : ""}</span>
            <input type="password" value={s.api.api_key} placeholder="Leave empty to use GROQ_API_KEY from .env" onChange={(e) => setS({ ...s, api: { ...s.api, api_key: e.target.value } })} style={{ maxWidth: 420 }} />
          </label>
          <p className="muted small">Keys entered here are stored in data/workspace/settings.json on this machine and never shown again in full.</p>
        </div>
      )}
      {s.llm_mode === "local" && (
        <div className="box section row" style={{ alignItems: "flex-end" }}>
          <label className="field" style={{ flex: 1, minWidth: 260 }}><span>Base URL</span><input type="text" value={s.local.base_url} onChange={(e) => setS({ ...s, local: { ...s.local, base_url: e.target.value } })} /></label>
          <label className="field"><span>Model</span><input type="text" value={s.local.model} onChange={(e) => setS({ ...s, local: { ...s.local, model: e.target.value } })} /></label>
          <p className="muted small" style={{ flexBasis: "100%" }}>With Ollama: install it, run <b>ollama pull llama3.1:8b</b>, keep it running, and use http://localhost:11434/v1.</p>
        </div>
      )}
      {s.llm_mode !== "off" && (
        <div className="row section" style={{ marginTop: 14 }}>
          <label className="field"><span>Work orders per request</span><input type="number" min={1} max={20} value={s.batch_size} onChange={(e) => setS({ ...s, batch_size: Number(e.target.value) })} style={{ width: 90 }} /></label>
        </div>
      )}
      <div className="row" style={{ marginTop: 18 }}>
        <button className="btn primary" onClick={save}>Save settings</button>
        {s.llm_mode !== "off" && <button className="btn" onClick={test} disabled={testing}>{testing ? "Testing…" : "Test connection"}</button>}
      </div>
      {result && <div className={`notice ${result.ok ? "" : "err"}`} style={{ marginTop: 12 }}>{result.message}</div>}

      <section className="section">
        <h2>Storage</h2>
        <p className="lede">The knowledge graph lives in {store.startsWith("neo4j") ? "Neo4j" : "a local SQLite file"} ({store.split(":").slice(1).join(":")}). Set NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD in .env to use Neo4j, then restart.</p>
      </section>
    </>
  );
}
