import { createContext, useContext, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { Sidebar, type ViewKey } from "./components/Sidebar";
import { Header } from "./components/Header";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { usePipelineEvents, usePlatforms } from "./lib/hooks";
import { useInvalidateOnJobDone, useReducedMotion } from "./lib/hooks";
import type { Jobs } from "./lib/types";
import { Dashboard } from "./views/Dashboard";
import { DiscoveryView } from "./views/DiscoveryView";
import { Corpus } from "./views/Corpus";
import { ConfigView } from "./views/ConfigView";
import { StudioView } from "./views/StudioView";
import { PlaybookView } from "./views/PlaybookView";
import { SoundsView } from "./views/SoundsView";
import { ProducersView } from "./views/ProducersView";
import { ActivityView } from "./views/ActivityView";
import { EvalsView } from "./views/EvalsView";
import { AgentBoardView } from "./views/AgentBoardView";

/* platform + live pipeline shared across the shell */
interface Shell {
  platform: string;
  setPlatform: (p: string) => void;
  jobs: Jobs;
  connected: boolean;
  selectedAgent: string | null;
  openAgent: (name: string) => void;
}
const ShellCtx = createContext<Shell>(null!);
export const useShell = () => useContext(ShellCtx);

export function App() {
  const [view, setView] = useState<ViewKey>("dashboard");
  const [platform, setPlatform] = useState("instagram");
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const { jobs, connected } = usePipelineEvents();
  const reduced = useReducedMotion();
  // when a stage finishes, refresh the REST resources it touched (§9.5)
  useInvalidateOnJobDone(jobs, platform);

  function openAgent(name: string) {
    setSelectedAgent(name);
    setView("agent");
  }

  const shell = useMemo<Shell>(
    () => ({ platform, setPlatform, jobs, connected, selectedAgent, openAgent }),
    [platform, jobs, connected, selectedAgent],
  );

  const platformsQ = usePlatforms();

  return (
    <ShellCtx.Provider value={shell}>
      <div className="app-shell">
        <Sidebar view={view} onNavigate={setView} />
        <div className="app-main">
          <Header
            view={view}
            platforms={platformsQ.data ?? []}
            platform={platform}
            onPlatform={setPlatform}
            jobs={jobs}
            connected={connected}
          />
          <main className="app-scroll" id="main" role="main">
            {/* Enter-only fade, keyed by view. No exit/mode="wait" so the new
                view mounts immediately — snappy, never a blank hold. */}
            <motion.div
              key={view}
              initial={reduced ? false : { opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
              className="app-view"
            >
              {/* Keyed by view: a crash is contained to the view that caused it, and
                  navigating away and back gives a clean remount rather than a stuck panel. */}
              <ErrorBoundary key={view}>
                {view === "dashboard" && <Dashboard onNavigate={setView} />}
                {view === "discover" && <DiscoveryView />}
                {view === "corpus" && <Corpus />}
                {view === "sounds" && <SoundsView />}
                {view === "proposals" && <StudioView />}
                {view === "producers" && <ProducersView />}
                {view === "activity" && <ActivityView />}
                {view === "evals" && <EvalsView onNavigate={setView} />}
                {view === "playbook" && <PlaybookView />}
                {view === "config" && <ConfigView />}
                {view === "agent" && selectedAgent && <AgentBoardView name={selectedAgent} />}
              </ErrorBoundary>
            </motion.div>
          </main>
        </div>
      </div>
    </ShellCtx.Provider>
  );
}
