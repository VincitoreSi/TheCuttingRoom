/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The chart stack, by top-level package name. recharts' weight is mostly not recharts: it is
// the d3 scale/shape/array family, victory-vendor, and — new in recharts 3 — a redux store.
// All of them are used by nothing else here, so they belong in the chart chunk rather than in
// the shell the first paint waits on.
const CHART_PKGS =
  /^(recharts|victory-vendor|internmap|decimal\.js-light|@reduxjs|redux|redux-thunk|reselect|immer|react-redux|use-sync-external-store|react-smooth|react-transition-group|dom-helpers|d3-.*|eventemitter3|fast-equals|es-toolkit|tiny-invariant)$/;

export default defineConfig(({ mode }) => {
  // The hub (FastAPI) serves this build in production from ReelScraper/frontend/dist —
  // so prod is same-origin and port-agnostic. Dev is the only place the hub's address is
  // needed, and the hub does not always own 8787: `cli.py start` falls back to a free port
  // when that one is busy, printing the real one as `HUB_URL=…`. So read it from the
  // environment, and `BACKEND_API=http://127.0.0.1:9123 npm run dev` just works.
  //
  // loadEnv (rather than process.env) because it is Vite's own API and needs no
  // @types/node; the empty prefix opts in to unprefixed vars like BACKEND_API.
  const env = loadEnv(mode, ".", "");
  const HUB = env.BACKEND_API || "http://127.0.0.1:8787";

  return {
    plugins: [react(), tailwindcss()],
    server: {
      proxy: {
        "/api": { target: HUB, changeOrigin: true },
        "/media": { target: HUB, changeOrigin: true },
        // producer-generated reels live in their own namespace (never /media,
        // which is the scraped corpus) — the Studio Renders tab plays them inline.
        "/renders": { target: HUB, changeOrigin: true },
      },
    },
    build: {
      outDir: "dist",
      // Recharts + framer are large; split them so the shell paints fast.
      //
      // The FUNCTION form, not the `{name: [pkg]}` object form. Vite 8 bundles with Rolldown,
      // which dropped the object form entirely — and it failed as a type error rather than a
      // silent no-op, so this is a real port and not a cosmetic one. The function is also
      // more honest about what it splits: the object form quietly hauled each package's
      // exclusive dependencies along with it, whereas here the chart stack's heavy transitive
      // deps (d3-*, victory-vendor, the redux store recharts 3 now runs on) have to be named.
      rollupOptions: {
        output: {
          manualChunks(id: string) {
            if (!id.includes("node_modules")) return;
            const pkg = id.split("node_modules/").pop()?.split("/")[0] ?? "";
            if (CHART_PKGS.test(pkg)) return "charts";
            if (pkg === "framer-motion" || pkg === "motion-dom" || pkg === "motion-utils")
              return "motion";
            if (pkg === "@tanstack") return "query";
          },
        },
      },
    },
    test: {
      environment: "node",
      include: ["src/**/*.test.ts"],
    },
  };
});
