# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities.

Instead, use GitHub's private reporting: go to the **Security** tab → **Report a
vulnerability** (GitHub Private Vulnerability Reporting). If that is unavailable,
open a minimal issue asking a maintainer to contact you privately — without
disclosing details.

We aim to acknowledge reports within a few days and to ship a fix or mitigation
as quickly as practical.

## Scope and handling secrets

This project drives social-platform scraping and third-party AI APIs, so a few
rules matter for everyone running it:

- **Never commit secrets.** API keys and login cookies belong only in per-agent
  `.env` files and `session.txt`, which are gitignored. Only `.env.example`
  templates (declaring variable *names*, never values) are tracked.
- **Rotate anything that leaks.** If a key or session cookie is ever committed,
  pushed, or shared, treat it as compromised and rotate it immediately.
- The pipeline reads secrets by environment-variable **name** only; the hub
  never stores a secret value. Keep it that way in contributions.
- Use **burner accounts** for any platform session used by the scraper — never a
  personal or primary account.

## Known exception: `demo-data/` contains real scraped data

`demo-data/` is a deliberate, documented exception to the "examples stay synthetic" rule.
It ships a real captured snapshot — **real Instagram creator handles (9 accounts), real
captions, and real engagement metrics** — so that a fresh clone opens on a populated
dashboard instead of empty states.

Signed CDN links were stripped at capture time (all `thumbnail_url` / `media_url` values are
blank; no `_nc_ohc` / `oh=` / `oe=` / `_nc_gid` parameter survives), and no `.env`,
`session.txt`, `content.db`, or raw scrape dump is included. Those properties are verified,
but they do **not** make the directory publishable.

**This is acceptable only while the repository is PRIVATE.** Publishing it would republish
third-party content and personal data without consent. Before flipping the repo to public,
follow the remediation in `demo-data/README.md` — either drop the dataset (and purge it from
history with `git filter-repo`, since deleting it in a later commit leaves the blobs on
GitHub forever) or regenerate it synthetically via `scripts/capture-demo.py`.

## Threat model / trust boundary of the local hub

The hub (`ReelScraper/api/app.py`) is designed as a **single-user localhost tool**. It binds
`127.0.0.1` by default and has **no authentication of any kind**. Every `/api/*` route is
therefore trusted to anything that can issue an HTTP request to that port.

- Do **not** set `HUB_HOST=0.0.0.0` or otherwise expose the port. That publishes the entire
  unauthenticated API — corpus, studio, gate, config, and command execution — to the network.
- Adding authentication is a prerequisite for any multi-user or exposed deployment.

An open browser tab sits *inside* that trust perimeter, which is why the two paths from "a web
page you happened to visit" to "code running on your machine" are closed explicitly.

### CORS is restricted to loopback origins

```python
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$",
    allow_methods=["*"], allow_headers=["*"],
)
```

Not `allow_origins=["*"]`. A page on an arbitrary origin can no longer read hub responses, and
any request needing a preflight (the JSON `Content-Type` every write route uses) is refused
outright. A regex rather than an enumerated list because neither port is fixed — `cli.py start`
falls back to a free port, and the Dashboard dev server picks its own.

Credentials are not enabled, and production needs no CORS at all: the hub serves the built
Dashboard same-origin.

> **This is a mitigation, not auth.** CORS governs what a *browser* will let a page read. A
> simple no-preflight request can still be *sent* cross-origin even though its response cannot
> be read. Binding to loopback remains the primary boundary.

### `render_cmd` is allowlisted, not trusted

`POST /api/producers/register` is unauthenticated and `ProducerManifest` sets `extra="allow"`,
so a producer's `render_cmd` is caller-supplied — and it becomes argv for `subprocess.run`.
Unconstrained, that is remote code execution for anything that can reach the port. Two
independent constraints apply, in `_validate_render_cmd` and `_producer_dir`:

| Constraint | Rule |
|---|---|
| Launcher | `argv[0]` must be one of `uv`, `python`, `python3`, `node`, `npm` |
| Arguments | every element must match `^[A-Za-z0-9._/=:-]{1,120}$` — no shell metacharacters, no whitespace-smuggled extra words |
| Paths | no argument may start with `/` or contain `..` |
| Directory | the declared `dir` must resolve to a **direct sibling** of the hub repo, and exist |

`subprocess.run` is never called with `shell=True`. The two constraints are both required and
are not redundant: `dir` pins only the working directory, never the command.

Uploaded render assets are constrained separately — name shape, extension allowlist, a 64 MB
cap, and outright rejection of names matching the scraped-corpus `content_id` pattern, so a
producer cannot overwrite real corpus media with its own output.

## Supported versions

This is a young project; security fixes target the `main` branch and the latest
release. Older tags are not maintained.
