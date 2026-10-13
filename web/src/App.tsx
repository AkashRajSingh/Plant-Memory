import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Job, type Meta } from "./api";
import { Ctx, JobBar } from "./components/ui";
import Overview from "./pages/Overview";
import Upload from "./pages/Upload";
import Review from "./pages/Review";
import GraphPage from "./pages/Graph";
import Data from "./pages/Data";
import Library from "./pages/Library";
import Settings from "./pages/Settings";

interface StatusResp {
  store: string;
  work_orders: number;
  facts_by_status: Record<string, number>;
  llm: { mode: string; label: string; enabled: boolean };
  job: Job | null;
  version: string;
}

const PAGES = [
  { id: "overview", label: "Overview", icon: "M3 13h4v7H3zM10 8h4v12h-4zM17 4h4v16h-4z" },
  { id: "upload", label: "Upload data", icon: "M12 16V4m0 0-5 5m5-5 5 5M4 20h16" },
  { id: "review", label: "Review", icon: "M5 4h10l4 4v12H5zM9 13l2 2 4-4" },
  { id: "graph", label: "Graph", icon: "M6 6a2 2 0 1 0 0 .01M18 6a2 2 0 1 0 0 .01M12 18a2 2 0 1 0 0 .01M7.5 7.5l3.5 9M16.5 7.5l-3.5 9M8 6h8" },
  { id: "data", label: "Work orders", icon: "M4 5h16M4 10h16M4 15h16M4 20h10" },
  { id: "library", label: "Assets & terms", icon: "M5 4h4v16H5zM11 4h3v16h-3zM16 5l3 1-3 14-3-1z" },
  { id: "settings", label: "Privacy & settings", icon: "M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6z" },
] as const;
type PageId = (typeof PAGES)[number]["id"];

const initialPage = (): PageId => {
  const h = window.location.hash.slice(1) as PageId;
  return PAGES.some((p) => p.id === h) ? h : "overview";
};

export default function App() {
  const [page, setPage] = useState<PageId>(initialPage);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [status, setStatus] = useState<StatusResp | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [toastMsg, setToastMsg] = useState<{ m: string; err: boolean } | null>(null);
  const [dataVersion, setDataVersion] = useState(0);
  const [reviewer, setReviewer] = useState(() => {
    try { return localStorage.getItem("pm.reviewer") ?? ""; } catch { return ""; }
  });
  const toastTimer = useRef<number>();

  const go = useCallback((p: string) => {
    setPage(p as PageId);
    window.location.hash = p;
  }, []);
  const toast = useCallback((m: string, err = false) => {
    setToastMsg({ m, err });
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastMsg(null), err ? 6000 : 2600);
  }, []);
  const refreshMeta = useCallback(() => { api.get<Meta>("/api/meta").then(setMeta).catch(() => {}); }, []);
  const bump = useCallback(() => setDataVersion((v) => v + 1), []);

  useEffect(() => { refreshMeta(); }, [refreshMeta, dataVersion]);
  useEffect(() => {
    api.get<StatusResp>("/api/status").then((s) => { setStatus(s); if (s.job && !job) setJob(s.job); }).catch(() => {});
  }, [dataVersion]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const onHash = () => setPage(initialPage());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // poll the running build
  useEffect(() => {
    if (!job || (job.state !== "running" && job.state !== "queued")) return;
    const t = window.setInterval(async () => {
      try {
        const j = await api.get<Job>(`/api/jobs/${job.id}`);
        setJob(j);
        if (j.state === "done") { toast("Knowledge graph updated"); bump(); }
        if (j.state === "failed") toast(`Build failed: ${j.error}`, true);
      } catch { /* server restarting */ }
    }, 1000);
    return () => window.clearInterval(t);
  }, [job, toast, bump]);

  const saveReviewer = (v: string) => {
    setReviewer(v);
    try { localStorage.setItem("pm.reviewer", v); } catch { /* private mode */ }
  };
  const proposed = status ? (status.facts_by_status["proposed"] ?? 0) : 0;
  const ctx = { reviewer, meta, refreshMeta, toast, job, watchJob: setJob, dataVersion, bump, go };
  const llm = status?.llm;

  return (
    <Ctx.Provider value={ctx}>
      <div className="shell">
        <nav className="rail" aria-label="Main">
          <div className="brand"><span className="brand-mark" aria-hidden /><span className="brand-name">Plant Memory</span></div>
          {PAGES.map((p) => (
            <button key={p.id} className="nav-btn" aria-current={page === p.id ? "page" : undefined} onClick={() => go(p.id)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d={p.icon} /></svg>
              {p.label}
              {p.id === "review" && proposed > 0 && <span className="nav-count">{proposed > 999 ? "999+" : proposed}</span>}
            </button>
          ))}
          <div className="rail-foot">
            {status ? <>{status.work_orders} work orders<br />{status.store.split(":")[0] === "neo4j" ? "Neo4j graph" : "SQLite store"}<br />v{status.version}</> : "Connecting…"}
          </div>
        </nav>
        <div className="main">
          <header className="topbar">
            <JobBar job={job} />
            <span className="spacer" />
            {llm && (
              <span className={`chip ${llm.mode === "api" && llm.enabled ? "external" : "private"}`} title="Where work-order text is processed">
                <span className="dot" />
                {llm.mode === "off" ? "Local only" : llm.mode === "local" ? "Local LLM" : "LLM API"}: {llm.mode === "off" ? "no data leaves this machine" : llm.label}
                <button onClick={() => go("settings")}>Change</button>
              </span>
            )}
            <label className="reviewer">
              Reviewer
              <input type="text" placeholder="Your name, e.g. Ramesh (Sr. Tech)" value={reviewer} onChange={(e) => saveReviewer(e.target.value)} />
            </label>
          </header>
          <main className="content" key={page}>
            {page === "overview" && <Overview />}
            {page === "upload" && <Upload />}
            {page === "review" && <Review />}
            {page === "graph" && <GraphPage />}
            {page === "data" && <Data />}
            {page === "library" && <Library />}
            {page === "settings" && <Settings />}
          </main>
        </div>
      </div>
      {toastMsg && <div className={`toast ${toastMsg.err ? "err" : ""}`} role="status">{toastMsg.m}</div>}
    </Ctx.Provider>
  );
}
