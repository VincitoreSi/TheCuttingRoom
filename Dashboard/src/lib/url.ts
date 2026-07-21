// One place that decides whether a URL is safe to put in an href/src. The app
// renders agent-authored and scraped strings as links (markdown proposals,
// discovery samples, reel/sound pages); an unvalidated href lets a
// `javascript:`/`data:` URI execute script in the dashboard origin, which has
// same-origin access to the localhost hub API. Everything that turns untrusted
// text into a link MUST route through here.

const SAFE_SCHEME = /^(https?:|mailto:)/i;

/**
 * Return `url` only if it is a safe absolute http(s)/mailto link or a
 * site-relative path (starts with "/"); otherwise `undefined` so the caller
 * renders inert (no href). Leading whitespace (space/tab/newline/CR) — the
 * classic `\tjavascript:` scheme-check bypass — is stripped before testing.
 */
export function safeUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  const trimmed = url.trimStart();
  if (trimmed.startsWith("/")) return trimmed; // site-relative
  if (SAFE_SCHEME.test(trimmed)) return trimmed;
  return undefined;
}
