import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { toastForError } from "./toasts";
import { applyLogEvent } from "./agentBoard";
import { renderProgress } from "./renderProgress";
import type { AgentBoard, ConfigResponse, Jobs, LogEvent, Stage } from "./types";

/* ---------------- data queries ----------------
   New resources (producers, studio+status, audio, blueprints, logs, evals) are
   plain REST → TanStack Query with refetch-on-focus. Only JOB status rides SSE
   (§9.5). staleTime is inherited from the QueryClient (20s) unless noted. */

export const usePlatforms = () =>
  useQuery({ queryKey: ["platforms"], queryFn: api.platforms, refetchInterval: 15_000 });

export const useContent = (p: string) =>
  useQuery({ queryKey: ["content", p], queryFn: () => api.content(p), enabled: !!p });

export const useConfig = (p: string) =>
  useQuery({ queryKey: ["config", p], queryFn: () => api.config(p), enabled: !!p });

export const useFactors = (p: string) =>
  useQuery({ queryKey: ["factors", p], queryFn: () => api.factors(p), enabled: !!p });

export const useStudio = (p: string) =>
  useQuery({ queryKey: ["studio", p], queryFn: () => api.studio(p), enabled: !!p });

/** Producer-generated reels for the Studio "Renders" tab. Refetched on focus
    and invalidated by useInvalidateOnJobDone when a `render` job settles. */
export const useRenders = (p: string) =>
  useQuery({
    queryKey: ["renders", p],
    queryFn: () => api.renders(p),
    enabled: !!p,
    refetchOnWindowFocus: true,
  });

export const useInsights = () => useQuery({ queryKey: ["insights"], queryFn: api.insights });

export const useProducers = () =>
  useQuery({ queryKey: ["producers"], queryFn: api.producers, refetchOnWindowFocus: true });

export const useTrending = (p: string) =>
  useQuery({
    queryKey: ["trending", p],
    queryFn: () => api.trending(p),
    enabled: !!p,
    refetchOnWindowFocus: true,
  });

export const useAnalysis = (p: string) =>
  useQuery({ queryKey: ["analysis", p], queryFn: () => api.analysis(p), enabled: !!p });

export const useBlueprint = (p: string, id: string | null) =>
  useQuery({
    queryKey: ["blueprint", p, id],
    queryFn: () => api.blueprint(p, id!),
    enabled: !!p && !!id,
    retry: false, // a missing blueprint is an expected empty state, not an error to retry
  });

export const useEvals = (opts?: { agent?: string; target_type?: string; since?: number }) =>
  useQuery({
    queryKey: ["evals", opts?.agent ?? "", opts?.target_type ?? "", opts?.since ?? ""],
    queryFn: () => api.evals(opts),
    refetchOnWindowFocus: true,
  });

export const useLogs = (opts?: { agent?: string; level?: string }) =>
  useQuery({
    queryKey: ["logs", opts?.agent ?? "", opts?.level ?? ""],
    queryFn: () => api.logs(opts),
  });

export const useReferences = (p: string) =>
  useQuery({
    queryKey: ["references", p],
    queryFn: () => api.references(p),
    enabled: !!p,
    refetchOnWindowFocus: true,
  });

/** Discovery candidates (§11.4). Both queries share the ["candidates", p, …]
    key prefix so a single invalidateQueries({queryKey:["candidates", p]}) —
    e.g. after a gate decision — refreshes whichever view is mounted. */
export const useCandidates = (p: string, status?: string) =>
  useQuery({
    queryKey: ["candidates", p, status ?? "all"],
    queryFn: () => api.getCandidates(p, status),
    enabled: !!p,
    refetchOnWindowFocus: true,
  });

export const usePendingCandidates = (p: string) =>
  useQuery({
    queryKey: ["candidates", p, "pending"],
    queryFn: () => api.getPendingCandidates(p),
    enabled: !!p,
    refetchOnWindowFocus: true,
  });

export const useAgentConfig = (agent: string | null) =>
  useQuery({
    queryKey: ["agent-config", agent],
    queryFn: () => api.agentConfig(agent!),
    enabled: !!agent,
  });

export const useSecrets = (agent: string | null) =>
  useQuery({
    queryKey: ["secrets", agent],
    queryFn: () => api.secretsStatus(agent!),
    enabled: !!agent,
  });

/* ---------------- mutations ---------------- */

export function useRunStage(p: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (stage: Stage) => api.runStage(p, stage),
    onSuccess: () => {
      // status arrives via SSE; nudge platform counts once the job likely finished
      setTimeout(() => qc.invalidateQueries({ queryKey: ["platforms"] }), 2500);
    },
    // The hub refuses an unrunnable stage with a written reason. Without this the refusal
    // was discarded and the Run click looked like it had done nothing.
    onError: (e, stage) => toastForError(`Could not run ${stage}`, e),
  });
}

/** One-click whole-pipeline run: scrape -> analyze -> media -> analysis-engine, run
    sequentially by the backend. Mirrors useRunStage — progress arrives via SSE; we nudge
    platform counts once the run has likely made progress. */
export function useRunAll(platform: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.runAll(platform),
    onSuccess: () => {
      setTimeout(() => qc.invalidateQueries({ queryKey: ["platforms"] }), 2500);
    },
    onError: (e) => toastForError("Could not start the full pipeline", e),
  });
}

/** The Fix Queue's re-run action. Wraps the EXISTING useRunStage — there is
    no per-target re-analyze route, so this re-runs the whole analysis-engine
    stage for the record's platform (coarser, but real and idempotent).
    useInvalidateOnJobDone (App.tsx) already invalidates ["evals"] the moment
    that job finishes for the currently-selected shell platform; the extra
    invalidate here is belt-and-suspenders for a Fix Queue row whose own
    platform isn't the one currently selected in the header. */
export function useReRunEval(platform: string | null | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (stage: Stage) => api.runStage(platform!, stage),
    onSuccess: () => {
      setTimeout(() => qc.invalidateQueries({ queryKey: ["evals"] }), 4000);
    },
  });
}

export function useSaveConfig(p: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<ConfigResponse>) => api.putConfig(p, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config", p] });
      // A watchlist edit changes /api/platforms too — it carries the watchlist count the
      // Board's Sources node shows and the readiness map that decides whether Scrape is
      // runnable at all. Without this the Board sat on a stale summary and adding the
      // first handle appeared to do nothing anywhere outside the Config view.
      qc.invalidateQueries({ queryKey: ["platforms"] });
    },
    // A failed PUT used to leave the handle sitting in the list as though it had saved,
    // so the watchlist on screen disagreed with pages.txt until the next refetch.
    onError: (e) => {
      toastForError("Could not save the watchlist", e);
      qc.invalidateQueries({ queryKey: ["config", p] });
    },
  });
}

/** Record a human-gate decision, then refresh the studio list. */
export function useSetStudioStatus(p: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { file: string; status: string; note?: string }) =>
      api.setStudioStatus(p, v.file, v.status, v.note),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["studio", p] }),
    onError: (e, v) => toastForError(`Could not mark ${v.file} ${v.status}`, e),
  });
}

/** Kick a render for one approved studio item. Progress arrives over SSE on the
    `${platform}:render:${file}` job key; the render record itself lands when the
    job settles (useInvalidateOnJobDone), so this only nudges the list optimistically. */
export function useRenderStudioItem(p: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { file: string; force?: boolean }) => api.renderStudioItem(p, v.file, v.force),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["renders", p] }),
  });
}

/** Record a discovery gate decision (approve → pages.txt / reject), then refresh
    the candidate lists + the config (pages.txt changed) + platform counts. */
export function useSetCandidateStatus(p: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { candidateId: string; status: string; note?: string }) =>
      api.setCandidateStatus(p, v.candidateId, v.status, v.note),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["candidates", p] });
      qc.invalidateQueries({ queryKey: ["config", p] });
      qc.invalidateQueries({ queryKey: ["platforms"] });
    },
  });
}

/** Save a per-agent config from the schema-driven form. */
export function useSaveAgentConfig(agent: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (config: Record<string, unknown>) => api.putAgentConfig(agent, config),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agent-config", agent] }),
  });
}

/** Register a reference/template URL, then refresh the reference list. */
export function useAddReference(p: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { url: string; note?: string }) => api.addReference(p, v.url, v.note),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["references", p] }),
  });
}

/* ---------------- live pipeline (SSE) ----------------
   /api/events streams the whole JOBS dict every ~1s. We keep the latest
   snapshot and a live "connected" flag. Falls back to polling if the
   stream drops. */
export function usePipelineEvents() {
  const [jobs, setJobs] = useState<Jobs>({});
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let stopped = false;
    let poll: ReturnType<typeof setInterval> | null = null;

    function connect() {
      const es = new EventSource("/api/events");
      esRef.current = es;
      es.onopen = () => setConnected(true);
      es.onmessage = (e) => {
        try {
          setJobs(JSON.parse(e.data) as Jobs);
        } catch {
          /* heartbeat / malformed — ignore */
        }
      };
      es.onerror = () => {
        setConnected(false);
        es.close();
        if (!stopped) {
          // fall back to polling, and retry the stream shortly
          if (!poll)
            poll = setInterval(async () => {
              try {
                setJobs((await api.pipelineStatus()) as Jobs);
              } catch {
                /* hub down */
              }
            }, 2000);
          setTimeout(connect, 4000);
        }
      };
    }
    connect();

    return () => {
      stopped = true;
      esRef.current?.close();
      if (poll) clearInterval(poll);
    };
  }, []);

  return { jobs, connected };
}

/* ---------------- live agent-log stream (SSE `log` channel §10.1) ----------
   The same /api/events stream carries named `log` frames alongside the default
   JOBS snapshot. Activity (§10.1) tails them; we keep a bounded ring buffer so
   a long session can't grow unbounded. Falls back to the REST log history on
   mount so the view isn't empty before the first live event. Mirrors
   usePipelineEvents' resilience (S6): on `error`, close the source, fall back to
   polling `api.logs()` every ~2s to keep the ring fresh, and retry `connect()`
   after ~4s; a successful reconnect resumes the SSE tail and stops the poll. */
export function useLogStream(max = 200) {
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let stopped = false;
    let poll: ReturnType<typeof setInterval> | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;

    // seed with recent history so the timeline isn't blank
    api
      .logs()
      .then((rows) => {
        if (stopped) return;
        setEvents((prev) => (prev.length ? prev : rows.slice(-max)));
      })
      .catch(() => {});

    function connect() {
      const es = new EventSource("/api/events");
      esRef.current = es;
      es.addEventListener("open", () => {
        setConnected(true);
        if (poll) {
          clearInterval(poll);
          poll = null;
        }
      });
      const onLog = (e: MessageEvent) => {
        try {
          const ev = JSON.parse(e.data) as LogEvent;
          setEvents((prev) => [...prev, ev].slice(-max));
        } catch {
          /* malformed frame — ignore */
        }
      };
      es.addEventListener("log", onLog as EventListener);
      es.addEventListener("error", () => {
        setConnected(false);
        es.close();
        if (!stopped) {
          // fall back to polling the log history, and retry the stream shortly
          if (!poll)
            poll = setInterval(async () => {
              try {
                const rows = await api.logs();
                setEvents(rows.slice(-max));
              } catch {
                /* hub down */
              }
            }, 2000);
          retry = setTimeout(connect, 4000);
        }
      });
    }
    connect();

    return () => {
      stopped = true;
      esRef.current?.close();
      if (poll) clearInterval(poll);
      if (retry) clearTimeout(retry);
    };
  }, [max]);

  return { events, connected };
}

/** Seed a per-agent board from the reducer endpoint, then patch it live from the
    SSE `log` channel. Live events newer than the snapshot are folded in immutably. */
export function useAgentBoard(name: string, platform: string) {
  const snap = useQuery({
    queryKey: ["agent-board", name, platform],
    queryFn: () => api.agentBoard(name, platform),
    enabled: !!name && !!platform,
    refetchOnWindowFocus: true,
  });
  const { events, connected } = useLogStream(300);
  const board = useMemo(() => {
    if (!snap.data) return null;
    const newest = Math.max(
      0,
      ...snap.data.runs.flatMap((r) => r.items.map((i) => i.updated ?? 0)),
      snap.data.runs[0]?.started ?? 0,
    );
    let liveBoard: AgentBoard = snap.data;
    for (const ev of events) {
      if (ev.agent !== name) continue;
      // match the server reducer, which filters by platform — otherwise an
      // off-platform run of the same agent would transiently pollute this board.
      if (ev.platform && ev.platform !== platform && ev.platform !== "shared") continue;
      if ((ev.ts ?? 0) <= newest) continue; // already reflected in the snapshot
      liveBoard = applyLogEvent(liveBoard, ev);
    }
    return liveBoard;
  }, [snap.data, events, name, platform]);
  return { board, connected, isLoading: snap.isLoading };
}

/** Live per-frame render progress, keyed by studio filename (§10.1).
    Rides the SAME `log` SSE channel every other live view uses — useLogStream is
    the one subscription primitive, and renderProgress is the pure reducer that
    decides what an event means. Returns an empty map when nothing is rendering,
    which leaves ProgressBar on its indeterminate sliver. */
export function useRenderProgress(platform: string, jobs: Jobs) {
  const { events } = useLogStream(300);
  return useMemo(() => renderProgress(events, jobs, platform), [events, jobs, platform]);
}

/** Invalidate REST resources when a related pipeline job finishes, so the new
    blueprint / persisted media / counts show up without a manual refresh
    (§9.5). Keyed on the newest done/error transition we observe. */
export function useInvalidateOnJobDone(jobs: Jobs, platform: string) {
  const qc = useQueryClient();
  const seen = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const [key, job] of Object.entries(jobs)) {
      if (job.platform !== platform) continue;
      if (job.status !== "done" && job.status !== "error") continue;
      const stamp = `${key}:${job.ended ?? ""}:${job.status}`;
      if (seen.current.has(stamp)) continue;
      seen.current.add(stamp);
      // map the finished stage → the resources it touches
      qc.invalidateQueries({ queryKey: ["platforms"] });
      if (job.stage === "scrape" || job.stage === "media") {
        qc.invalidateQueries({ queryKey: ["content", platform] });
        qc.invalidateQueries({ queryKey: ["trending", platform] });
      }
      if (job.stage === "analysis-engine" || job.stage === "analyze") {
        qc.invalidateQueries({ queryKey: ["analysis", platform] });
        qc.invalidateQueries({ queryKey: ["content", platform] });
        qc.invalidateQueries({ queryKey: ["evals"] });
        qc.invalidateQueries({ queryKey: ["references", platform] });
      }
      if (job.stage === "render") {
        // a finished render writes renders/<p>/<id>/ AND stamps the studio
        // markdown with its "## Rendered media" block — refresh both.
        qc.invalidateQueries({ queryKey: ["renders", platform] });
        qc.invalidateQueries({ queryKey: ["studio", platform] });
      }
      if (job.stage === "auto-search" || job.stage === "auto-search-beat") {
        // a Discover run posts new candidates and (on auto-approve, if ever
        // enabled) can touch pages.txt — refresh both.
        qc.invalidateQueries({ queryKey: ["candidates", platform] });
        qc.invalidateQueries({ queryKey: ["config", platform] });
      }
    }
  }, [jobs, platform, qc]);
}

/** Page-visibility flag — data-flow animation pauses when the tab is hidden. */
export function usePageVisible() {
  const [visible, setVisible] = useState(() => !document.hidden);
  useEffect(() => {
    const on = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  return visible;
}

/** A ticking clock (1s) for elapsed timers / relative dates, without pulling
    Date.now into render-time in a hundred places. */
export function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function useReducedMotion() {
  const [reduced, setReduced] = useState(
    () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
  );
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const on = () => setReduced(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return reduced;
}
