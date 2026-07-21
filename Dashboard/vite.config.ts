/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

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
      rollupOptions: {
        output: {
          manualChunks: {
            charts: ["recharts"],
            motion: ["framer-motion"],
            query: ["@tanstack/react-query", "@tanstack/react-virtual"],
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
