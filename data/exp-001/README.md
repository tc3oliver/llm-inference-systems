# Trace B — clean SpecPrefill-only request-level trace

The primary mechanism evidence for EXP-001. One continuous coding-agent
session against a single server process with SpecPrefill, an attention-based
sparse prefill mechanism, enabled and **no background densification code in
the build**, so nothing
competes to explain the behaviour.

Twenty consecutive prefix-cache restores. The reusable dense checkpoint
advances normally for ten requests, collapses at request 11, and never
recovers; the uncached suffix then grows monotonically for the rest of the
session while the scorer re-engages on every request.

The collapse at request 11 is a restore event: the cache layer rejected a
partial prefix match, and the 17,060-token miss it left is what first crossed
the 8192-token threshold and engaged SpecPrefill. SpecPrefill did not cause
the cliff; it is the reason the cliff was never repaired.

## Files

| file | rows | contents |
|---|---:|---|
| `trace-b-restores.csv` | 20 | per-request `cached_tokens` and `uncached_suffix` at prefix-cache restore |
| `trace-b-scorer.csv` | 10 | every scorer invocation: tokens scored and seconds spent |
| `trace-b-stores.csv` | 7 | every checkpoint written back to the prefix cache |

Clock values are wall-clock `HH:MM:SS` within the session, retained because
the intervals show the scorer cost growing with the suffix. The session spans
about 11 minutes of restores.

## What the numbers show

Checkpoint: `28,672 -> 32,768 -> 33,792 -> 36,864 -> 37,888`, then back to
`28,672` at request 11 and pinned there for the remaining 10 requests.

Stores end at `44,032`. Nothing is written after that, because sparse prefill
output is not eligible for the prefix cache — the request is served, but the
sparsified suffix does not advance the normal reusable dense prefix state.

Suffix after the cliff: `17,060 -> 33,979`, roughly doubling while the prompt
itself grows far less. That gap is the recomputation the session pays once
the checkpoint stops advancing.

Scorer cost grows with the suffix. Not monotonically — call 2 is `2.4 s` at
16,918 tokens, below call 1 — but from `2.7 s` at 8,535 tokens to `5.7 s` at
33,389 — the optimization's own overhead scales with the debt it created.

## Proximate cause of the cliff

Logged at the transition:

    ArraysCache layer 0: partial prefix match detected (placeholder in last
    matched block). Rejecting cache to prevent stale GDN state. Request will
    reprocess from scratch.

The placeholder is what a sparse prefill leaves in the block it did not fully
compute. The cache layer is correct to reject it; the cost is that the last
good checkpoint is the one from before sparse prefill began.

## Provenance and integrity

Extracted from the session transcript of the original run. The write-up that
accompanied it was lost with a scratch directory; these CSVs are the surviving
record, transcribed from the analysis tool output and cross-checked against
the raw server log lines quoted in the same transcript.

Two row counts appear in the source material and both are correct:

- **20 restores** — every restore the scheduler performed, listed here.
- **19 restores** — the same list excluding request 20, whose cache hit was
  dropped when that request failed for an unrelated runtime reason.

The entire trace **predates** that failure, so no row here is affected by it.
That failure is archived separately as a non-reproducible runtime observation
(4/4 pass on a sanitized reproduction) and is not part of this result.

Nothing in these files is derived, smoothed or interpolated. Where the source
reports a value, it is copied; where it does not, the column is absent.
