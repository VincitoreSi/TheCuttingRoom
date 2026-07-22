import type {
  AgentBoard,
  AgentConfigResponse,
  Blueprint,
  Candidate,
  CascadeRow,
  ConfigResponse,
  EvalRecord,
  FactorsResponse,
  HubIdentity,
  Insight,
  LogEvent,
  PlatformSummary,
  Producer,
  AgentRosterEntry,
  Proposal,
  Reel,
  ReferenceItem,
  RenderRecord,
  ScheduleRow,
  SecretStatus,
  Stage,
  TrendingSound,
} from "./types";

// Same-origin in production (the hub serves this build); the dev server
// proxies /api + /media to the hub. So a bare relative base works in both.
const BASE = "";

/** An HTTP failure carrying what the hub actually said.

    The hub answers a refused run with `{"detail": "No creators on the watchlist. Add a
    handle in Config first."}` — a sentence written to be read by the person who clicked.
    Flattening that into "409 Conflict — /api/pipeline/…: {\"detail\":\"No creators…\"}"
    threw away the only useful part, so `detail` is kept separate and shown on its own. */
export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, statusText: string, path: string, body: string) {
    const detail = parseDetail(body);
    super(detail || `${status} ${statusText} — ${path}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function parseDetail(body: string): string {
  try {
    const j = JSON.parse(body);
    if (typeof j?.detail === "string") return j.detail;
    // FastAPI validation errors arrive as a list of {loc, msg, type}
    if (Array.isArray(j?.detail)) return j.detail.map((d: { msg?: string }) => d?.msg).join("; ");
  } catch {
    /* not json — fall through to the raw body */
  }
  return body.slice(0, 200);
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, res.statusText, path, body);
  }
  return res.json() as Promise<T>;
}

export const api = {
  platforms: () => fetchJson<PlatformSummary[]>("/api/platforms"),

  /** Which checkout this hub is, and whether it still runs the code on disk. */
  hub: () => fetchJson<HubIdentity>("/api/hub"),

  content: (p: string) => fetchJson<Reel[]>(`/api/content/${p}`),

  config: (p: string) => fetchJson<ConfigResponse>(`/api/config/${p}`),
  putConfig: (p: string, body: Partial<ConfigResponse>) =>
    fetchJson<{ ok: boolean }>(`/api/config/${p}`, { method: "PUT", body: JSON.stringify(body) }),

  factors: (p: string) => fetchJson<FactorsResponse>(`/api/corpus/${p}/factors`),
  brief: (p: string) => fetchJson<{ brief: string }>(`/api/corpus/${p}/brief`),

  studio: (p: string, opts?: { status?: string; agent?: string }) => {
    const qs = new URLSearchParams();
    if (opts?.status) qs.set("status", opts.status);
    if (opts?.agent) qs.set("agent", opts.agent);
    const q = qs.toString();
    return fetchJson<Proposal[]>(`/api/studio/${p}${q ? `?${q}` : ""}`);
  },
  saveProposal: (p: string, filename: string, text: string) =>
    fetchJson<{ ok: boolean; file: string }>(`/api/studio/${p}`, {
      method: "POST",
      body: JSON.stringify({ filename, text }),
    }),
  // Human gate: record an approve/reject decision on a studio item.
  setStudioStatus: (p: string, file: string, status: string, note?: string) =>
    fetchJson<{ ok: boolean; file: string; status: string }>(
      `/api/studio/${p}/${encodeURIComponent(file)}/status`,
      { method: "POST", body: JSON.stringify({ status, note }) },
    ),

  // Producer-generated reels (the Studio "Renders" tab), newest first.
  renders: (p: string, opts?: { file?: string; agent?: string; kind?: string }) => {
    const qs = new URLSearchParams();
    if (opts?.file) qs.set("file", opts.file);
    if (opts?.agent) qs.set("agent", opts.agent);
    if (opts?.kind) qs.set("kind", opts.kind);
    const q = qs.toString();
    return fetchJson<RenderRecord[]>(`/api/renders/${p}${q ? `?${q}` : ""}`);
  },
  // Kick a render for one APPROVED studio item. 409 if it isn't approved, 400
  // if the producing agent isn't registered renderable, 404 if the file is gone.
  renderStudioItem: (p: string, file: string, force?: boolean) =>
    fetchJson<{ job_id: string; already_running: boolean }>(
      `/api/studio/${p}/${encodeURIComponent(file)}/render`,
      { method: "POST", body: JSON.stringify({ force: !!force }) },
    ),

  // Analysis blueprints (schema v2).
  analysis: (p: string) => fetchJson<Blueprint[]>(`/api/analysis/${p}`),
  blueprint: (p: string, id: string) =>
    fetchJson<Blueprint>(`/api/analysis/${p}/${encodeURIComponent(id)}`),

  // Audio intelligence.
  trending: (p: string) => fetchJson<TrendingSound[]>(`/api/audio/${p}/trending`),
  sound: (p: string, id: string) =>
    fetchJson<TrendingSound>(`/api/audio/${p}/sound/${encodeURIComponent(id)}`),

  // Producer registry + per-agent config/secrets.
  producers: () => fetchJson<Producer[]>("/api/producers"),
  /* Registered AND unregistered agents, with live key status. See AgentRosterEntry. */
  agents: () => fetchJson<AgentRosterEntry[]>("/api/agents"),
  agentBoard: (name: string, platform?: string): Promise<AgentBoard> =>
    fetchJson<AgentBoard>(
      `/api/agents/${encodeURIComponent(name)}/board${platform ? `?platform=${encodeURIComponent(platform)}` : ""}`,
    ),
  schedule: () => fetchJson<Record<string, ScheduleRow>>("/api/schedule"),
  putSchedule: (platform: string, body: Partial<ScheduleRow>) =>
    fetchJson<ScheduleRow>(`/api/schedule/${platform}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  agentConfig: (agent: string) => fetchJson<AgentConfigResponse>(`/api/config/agent/${agent}`),
  putAgentConfig: (agent: string, config: Record<string, unknown>) =>
    fetchJson<{ ok: boolean } & Record<string, unknown>>(`/api/config/agent/${agent}`, {
      method: "PUT",
      body: JSON.stringify({ config }),
    }),
  secretsStatus: (agent: string) =>
    fetchJson<SecretStatus[]>(`/api/config/agent/${agent}/secrets/status`),

  // Discovery candidates (§11) — the human gate on AutoSearch's finds.
  getCandidates: (p: string, status?: string) =>
    fetchJson<Candidate[]>(
      `/api/discovery/${p}${status ? `?status=${encodeURIComponent(status)}` : ""}`,
    ),
  getPendingCandidates: (p: string) => fetchJson<Candidate[]>(`/api/discovery/${p}/pending`),
  setCandidateStatus: (p: string, candidateId: string, status: string, note?: string) =>
    fetchJson<{ ok: boolean; candidate_id: string; status: string }>(
      `/api/discovery/${p}/${encodeURIComponent(candidateId)}/status`,
      { method: "POST", body: JSON.stringify({ status, note }) },
    ),

  // Reference / template ingestion (only consumer: the template agent).
  references: (p: string) => fetchJson<ReferenceItem[]>(`/api/reference/${p}`),
  addReference: (p: string, url: string, note?: string) =>
    fetchJson<Record<string, unknown>>(`/api/reference/${p}`, {
      method: "POST",
      body: JSON.stringify({ url, note }),
    }),

  // Platform-wide observability (§10).
  logs: (opts?: { agent?: string; level?: string; run_id?: string }) => {
    const qs = new URLSearchParams();
    if (opts?.agent) qs.set("agent", opts.agent);
    if (opts?.level) qs.set("level", opts.level);
    if (opts?.run_id) qs.set("run_id", opts.run_id);
    const q = qs.toString();
    return fetchJson<LogEvent[]>(`/api/logs${q ? `?${q}` : ""}`);
  },
  evals: (opts?: { agent?: string; target_type?: string; since?: number }) => {
    const qs = new URLSearchParams();
    if (opts?.agent) qs.set("agent", opts.agent);
    if (opts?.target_type) qs.set("target_type", opts.target_type);
    if (opts?.since != null) qs.set("since", String(opts.since));
    const q = qs.toString();
    return fetchJson<EvalRecord[]>(`/api/evals${q ? `?${q}` : ""}`);
  },

  insights: () => fetchJson<Insight[]>("/api/insights"),
  addInsight: (body: { platform: string; kind: string; text: string; tags?: string[] }) =>
    fetchJson<Insight>("/api/insights", { method: "POST", body: JSON.stringify(body) }),

  runStage: (p: string, stage: Stage) =>
    fetchJson<{ job_id: string }>(`/api/pipeline/${p}/${stage}`, { method: "POST" }),

  stopStage: (p: string, stage: Stage) =>
    fetchJson<{ job_id: string; signalled: boolean; halting_run: string | null }>(
      `/api/pipeline/${p}/${stage}/stop`,
      { method: "POST" },
    ),

  runAll: (p: string) =>
    fetchJson<{ run_id: string; stages: string[] }>(`/api/pipeline/${p}/run-all`, {
      method: "POST",
    }),

  pipelineStatus: () => fetchJson<Record<string, unknown>>("/api/pipeline/status"),

  cascade: () => fetchJson<Record<string, CascadeRow>>("/api/cascade"),

  putCascade: (platform: string, body: Partial<CascadeRow>) =>
    fetchJson<CascadeRow>(`/api/cascade/${platform}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
};
