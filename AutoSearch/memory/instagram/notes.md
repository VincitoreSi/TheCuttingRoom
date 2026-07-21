# Instagram — discovery craft notes (auto-appended, deduped; composed into the system prompt)

- Guest hydration (`web_profile_info`) is far more reliable than the HTML-fallback regex —
  treat a fallback-only result (no bio/category) as lower-confidence evidence of niche fit.
- `median_plays` from a single reel-sample page is noisy for creators who post rarely — weight
  it alongside `followers`, not instead of it.
- Business/verified accounts in a niche tend to be higher-signal creator candidates than
  personal accounts with the same follower count.
