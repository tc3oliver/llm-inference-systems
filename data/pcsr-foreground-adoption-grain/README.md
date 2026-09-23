# pcsr-foreground-adoption-grain

One CSV, 34 rows, behind
[Canonical-state publication and foreground adoption as separate control planes](../../research-threads/pcsr-foreground-adoption-grain.md).
A two-arm matched pair, not an experiment with its own number.

## `visibility-grain-ab.csv` — 34 rows

One row per foreground request, 17 per arm. Both arms ran on a research build
of the hpcp11 release (#3793, #3840 and #3842 integrated) carrying one
research-only setting. The setting rounds down the canonical prefix that an
ordinary foreground restore adopts to a multiple of N cache blocks. Recovery's
own restore is excluded, and PCSR recovery, durable publication, the budget,
the execution slice and the draft cache are unchanged.

| arm | `grain_blocks` | ran (local clock, 2026-09-23) |
|---|---|---|
| `B` | 4 | 08:14–08:31 |
| `A` | 1 (the unmodified behaviour) | 08:33–08:51 |

The production instance was stopped for the whole window. Each arm had a fresh
server and a fresh prefix cache, the same settings as production (block 1,024,
recovery budget 10%, execution slice 512, keep 0.2, threshold 8,192), and the
same workload:

- a deterministic append-only chat, one user message of generated code, seed
  5793, `workloads/generator.py`, kind `code`;
- 36,027 prompt tokens at turn 0, and each turn appends about 512 tokens at a
  line end;
- each request is sent 30 s after the previous one completed, with
  temperature 0 and 4 output tokens.

B ran first and stopped at its third visible-frontier change (turn 16). A then
ran the same 17 turns. The prompts are never written out.

Columns:

- **Observed.** Every column except the four named below is the runtime's own
  log line for that request: `VisibleFrontier` (research build),
  `SpecPrefill: scored`, `SpecPrefill: sparse prefill`, and
  `CanonicalRecovery: published`. Lines are matched to turns by order within
  each arm's log segment, one foreground request at a time.
  - `durable_match` is what the ordinary lookup matched.
  - `visible_frontier` is what the request adopted.
  - `pcsr_processed` and `pcsr_committed` are the recovery job's counters at
    request time.
- **Derived.** `visible_delta` and `visible_changed` compare a row with the
  previous one in its arm. `score_window_start` is
  `max(target_cached, system_end)`. `foreground_cost_s` is
  `draft_scoring_s + target_sparse_prefill_s`.
- `ttft_s` is the client's time to the first streamed token.
- `durable_frontier_before` and `publications_since_prev` are counted from
  publication log timestamps against the client send time.

The rows end at each arm's last request, so a publication after it is not
counted. A's twelfth, at 12,288 tokens, is one of those; the log shows both
arms' jobs stopping with 12,288 committed.

Turn 0 is cold in both arms and is left out of the arm totals.

Caveats that apply to every row:

- The neural-engine prefill path was unavailable to the research process, so
  target prefill ran on the GPU in both arms. Production uses the neural
  engine for part of it.
- `draft_scoring_s` is printed to one decimal place.
- A turn 5 has a 54.5 s TTFT and a 33.7 s sparse prefill with no frontier
  change and no recovery warning in the log. It is unexplained and is kept.
  The thread reports totals with and without it.
