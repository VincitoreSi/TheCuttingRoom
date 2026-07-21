# Core method & use-case (shared across all platform agents)

**Goal:** given handpicked creator pages in a niche, quantify which short-form posts
went viral and why — producing ranked, structured data an analyst can act on.

**Virality = percentile-blend of four signals** (each normalized 0–1 across the dataset,
then weighted per platform in that platform's `niche_config.json`):
- `reach_multiplier` = plays / followers — travel past the audience
- `outlier_score`    = plays / creator's median plays — breakout vs their norm
- `engagement_rate`  = (likes+comments+shares+saves) / followers
- `velocity`         = plays / days since posting

**Why it works:** ranking by raw plays buries small-account breakouts. Reach + outlier
surface them regardless of account size. Re-weight per platform, don't change the idea.

**Guardrails:** scrape safely (guest/no-login where possible; respect rate limits &
circuit breakers), keep memory curated (write facts not noise, recency-wins on conflicts).
