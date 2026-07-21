import { useTheme } from "../lib/theme";
import { agentState } from "../lib/jobs";
import { usePageVisible } from "../lib/hooks";
import { SeamStatus } from "./Seam";
import { Button } from "./ui";
import { IconMoon, IconSun } from "./icons";
import type { Jobs, PlatformSummary } from "../lib/types";
import type { ViewKey } from "./Sidebar";
import { cx } from "../lib/cx";

const VIEW_TITLE: Record<ViewKey, { title: string; eyebrow: string }> = {
  dashboard: { title: "The Board", eyebrow: "Discover → Studio" },
  discover: { title: "The Scouting Bench", eyebrow: "Candidates & the gate" },
  corpus: { title: "The Corpus", eyebrow: "Mined & measured" },
  sounds: { title: "The Sound Rack", eyebrow: "Rising audio" },
  proposals: { title: "The Studio", eyebrow: "Proposals & the gate" },
  producers: { title: "The Floor", eyebrow: "Producers & config" },
  activity: { title: "The Floor Log", eyebrow: "Live agent events" },
  evals: { title: "The Cutting Score", eyebrow: "Eval trends" },
  playbook: { title: "The Playbook", eyebrow: "What travels" },
  config: { title: "The Bench", eyebrow: "Weights & watchlist" },
  agent: { title: "Agent Desk", eyebrow: "Live workflow board" },
};

export function Header({
  view,
  platforms,
  platform,
  onPlatform,
  jobs,
  connected,
  onNavigate,
}: {
  view: ViewKey;
  platforms: PlatformSummary[];
  platform: string;
  onPlatform: (p: string) => void;
  jobs: Jobs;
  connected: boolean;
  /** lets the failure status open the Floor Log — optional so the header still renders
      in isolation (tests, storybook) without a router. */
  onNavigate?: (v: ViewKey) => void;
}) {
  const { theme, toggle } = useTheme();
  const agent = agentState(jobs, platform);
  const heading = VIEW_TITLE[view];
  const visible = usePageVisible();

  return (
    <header className="app-header">
      <div className="app-header__title">
        <div className="eyebrow">{heading.eyebrow}</div>
        <h1 className="font-display text-[19px] leading-none tracking-tight">{heading.title}</h1>
      </div>

      {/* The floor status. On failure this is the only always-visible surface in the app,
          so it carries the failing stage's own last line — the stages write good ones
          ("no scraped data — scrape first") — and clicking it opens the Floor Log with the
          full output. Before, a failed run said "Snapped · analyze" and nothing else, and
          the reason was reachable only as a 90-character truncation inside a board node. */}
      <div className="app-header__center">
        {agent.state === "error" && onNavigate ? (
          <button
            type="button"
            className="app-header__status app-header__status--error"
            onClick={() => onNavigate("activity")}
            title={agent.detail ? `${agent.detail} — click for the full log` : "Click for the log"}
          >
            <SeamStatus state={agent.state} label={agent.label} flowOn={visible} />
            {agent.detail && <span className="app-header__status-why">{agent.detail}</span>}
          </button>
        ) : (
          <SeamStatus state={agent.state} label={agent.label} flowOn={visible} />
        )}
      </div>

      <div className="app-header__right">
        <div
          className={cx("conn-dot", connected ? "conn-dot--on" : "conn-dot--off")}
          title={connected ? "Live · connected" : "Reconnecting…"}
        >
          <span />
          <span className="font-mono text-[10px] uppercase tracking-wide">
            {connected ? "live" : "…"}
          </span>
        </div>

        <div className="platform-switch" role="tablist" aria-label="Platform">
          {(platforms.length ? platforms : [{ platform, has_data: true } as PlatformSummary]).map(
            (p) => (
              <button
                key={p.platform}
                role="tab"
                aria-selected={p.platform === platform}
                disabled={!p.has_data && p.platform !== platform}
                onClick={() => onPlatform(p.platform)}
                className={cx(
                  "platform-switch__btn",
                  p.platform === platform && "platform-switch__btn--active",
                )}
                title={p.has_data ? `${p.items} items` : "No data yet"}
              >
                {p.platform === "instagram" ? "Instagram" : p.platform === "x" ? "X" : "YouTube"}
                {!p.has_data && <span className="platform-switch__empty">·</span>}
              </button>
            ),
          )}
        </div>

        <Button
          variant="ghost"
          size="icon"
          onClick={toggle}
          aria-label={theme === "dark" ? "Switch to light" : "Switch to dark"}
        >
          {theme === "dark" ? <IconSun size={17} /> : <IconMoon size={17} />}
        </Button>
      </div>
    </header>
  );
}
