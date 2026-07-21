// Flat ESLint config (ESLint 9+/10). Conservative rule set tuned to match the
// existing hand-rolled style in this codebase — this is a hygiene net, not a
// style rewrite. Type-aware linting is intentionally not enabled (keeps `npm
// run lint` fast and dependency-light); `tsc --noEmit` already covers types.
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
    },
    rules: {
      // Only the two long-standing, uncontroversial hooks rules — not the
      // full v7 "recommended" bundle, which pulls in React-Compiler-oriented
      // rules (set-state-in-effect, purity, immutability, …) that flag
      // several already-deliberate patterns in this codebase (e.g. syncing
      // query data into local editable state in ConfigView/AgentConfigForm).
      // That's a design conversation, not a hygiene fix.
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "react-refresh/only-export-components": "off",

      // Unused vars: allow the common `_`-prefixed intentional-discard idiom,
      // but keep the check on (the codebase is already clean).
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],

      // The codebase leans on `Record<string, unknown>` + narrow casts instead
      // of `any` — keep that honest without blocking on the rare escape hatch.
      "@typescript-eslint/no-explicit-any": "warn",

      // Non-null assertions (`!`) are used deliberately in a few spots (e.g.
      // `document.getElementById("root")!`) where the runtime guarantee is
      // documented in context — don't fight that pattern.
      "@typescript-eslint/no-non-null-assertion": "off",

      // Empty catch blocks are a used-and-commented pattern here (silent
      // clipboard/localStorage fallbacks) — allow but require they at least
      // be intentionally empty, not swallowing a typo.
      "no-empty": ["warn", { allowEmptyCatch: true }],
    },
  },
);
