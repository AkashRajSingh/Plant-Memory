// Thin client for the Plant Memory API.

export type Status = "proposed" | "verified" | "rejected";
export type Kind = "component" | "failure_mode" | "root_cause" | "action" | "symptom";

export interface Fact {
  fact_id: string;
  event_id: string | null;
  asset_id: string | null;
  object_kind: string;
  object_id: string;
  confidence: number;
  method: string;
  status: Status;
  verified_by?: string | null;
  note?: string | null;
  evidence_field?: string | null;
  evidence_snippet?: string | null;
  label?: string;
  description?: string;
  posting_date?: string;
  evidence?: string;
}

export interface Job {
  id: string;
  kind: string;
  state: "queued" | "running" | "done" | "failed";
  stage: string;
  done: number;
  total: number;
  log: string[];
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface Meta {
  areas: { code: string; label: string }[];
  assets: { asset_id: string; name: string; area: string }[];
  vocab: Record<Kind, { id: string; label: string }[]>;
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const r = await fetch(url, init);
  const text = await r.text();
  const data = text ? JSON.parse(text) : null;
  if (!r.ok) throw new Error((data && data.detail) || `${r.status} ${r.statusText}`);
  return data as T;
}

export const qs = (params: Record<string, unknown>) => {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "" && v !== false) p.set(k, String(v));
  });
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const api = {
  get: <T>(url: string) => req<T>("GET", url),
  post: <T>(url: string, body?: unknown) => req<T>("POST", url, body ?? {}),
  put: <T>(url: string, body: unknown) => req<T>("PUT", url, body),
  del: <T>(url: string) => req<T>("DELETE", url),
};

export const KIND_LABEL: Record<string, string> = {
  component: "Component",
  failure_mode: "Failure mode",
  root_cause: "Root cause",
  action: "Action",
  symptom: "Symptom",
  asset: "Asset",
};
