# studio/ — generated content proposals

Producer agents (SimilarContent, ProposalContent, AutoContent) write their outputs here,
per platform: `studio/<platform>/<date>-<agent>-<slug>.md`.

These agents READ the corpus through the `core.corpus.Corpus` adapter (top viral
exemplars + virality factors + memory + shared insights) and WRITE proposals here.
Selected/greenlit ideas get logged back to `memory/shared` (kind: idea) so the analysts
can later measure whether produced content actually went viral — closing the loop.
