import { motion } from "framer-motion";
import {
  IconActivity,
  IconBoard,
  IconConfig,
  IconCorpus,
  IconDiscover,
  IconEvals,
  IconInsights,
  IconProducers,
  IconScissors,
  IconSound,
  IconStudio,
} from "./icons";
import { cx } from "../lib/cx";
import { useHub } from "../lib/hooks";

export type ViewKey =
  | "dashboard"
  | "discover"
  | "corpus"
  | "sounds"
  | "proposals"
  | "producers"
  | "activity"
  | "evals"
  | "playbook"
  | "config"
  | "agent";

// Grouped by the pipeline's own shape: scout new creators, read the corpus,
// run the producers, watch the floor, tune the bench.
const NAV: {
  key: ViewKey;
  label: string;
  icon: (p: { size?: number }) => JSX.Element;
  group?: string;
}[] = [
  { key: "dashboard", label: "Board", icon: IconBoard },
  { key: "discover", label: "Discover", icon: IconDiscover },
  { key: "corpus", label: "Corpus", icon: IconCorpus },
  { key: "sounds", label: "Sounds", icon: IconSound },
  { key: "proposals", label: "Studio", icon: IconStudio, group: "make" },
  { key: "producers", label: "Producers", icon: IconProducers },
  { key: "activity", label: "Activity", icon: IconActivity, group: "watch" },
  { key: "evals", label: "Evals", icon: IconEvals },
  { key: "playbook", label: "Playbook", icon: IconInsights },
  { key: "config", label: "Config", icon: IconConfig, group: "tune" },
];

export function Sidebar({ view, onNavigate }: { view: ViewKey; onNavigate: (v: ViewKey) => void }) {
  const hub = useHub();
  return (
    <aside className="app-sidebar" aria-label="Primary">
      <div className="app-brand">
        <span className="app-brand__mark" aria-hidden="true">
          <IconScissors size={18} />
        </span>
        <span className="app-brand__word">
          <span className="font-display">The Cutting Room</span>
          <span className="eyebrow">Virality Studio</span>
        </span>
      </div>

      <nav className="app-nav">
        {NAV.map((n) => {
          const active = view === n.key;
          const Icon = n.icon;
          return (
            <div key={n.key} className="contents">
              {n.group && <span className="nav-sep" aria-hidden="true" />}
              <button
                className={cx("nav-item", active && "nav-item--active")}
                onClick={() => onNavigate(n.key)}
                aria-current={active ? "page" : undefined}
              >
                {active && (
                  <motion.span
                    layoutId="nav-thread"
                    className="nav-item__thread"
                    transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
                  />
                )}
                <Icon size={18} />
                <span className="nav-item__label">{n.label}</span>
              </button>
            </div>
          );
        })}
      </nav>

      <div className="app-sidebar__foot">
        <div className="eyebrow">Local · $0 · offline</div>
        {/* Which checkout you are looking at. One clone per niche is how two niches run at
            the same time, and the two boards are otherwise pixel-identical — so the niche
            name is the only thing in the chrome that tells them apart. Absent on a hub too
            old to serve /api/hub, which is why the host line below stands on its own. */}
        {hub.data?.niche && (
          <div className="text-[11px] text-[var(--ink-muted)] mt-1">{hub.data.niche}</div>
        )}
        {/* Read from the address bar, not hardcoded: the hub falls back to a random free
            port when 8787 is taken, and a footer insisting on 8787 is then simply wrong. */}
        <div className="font-mono text-[10px] text-[var(--ink-faint)] mt-1">
          {typeof window === "undefined" ? "127.0.0.1" : window.location.host}
        </div>
      </div>
    </aside>
  );
}
