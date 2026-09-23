# pcsr-foreground-adoption-grain

Three CSVs, 151 rows, behind
[Canonical-state publication and foreground adoption as separate control planes](../../research-threads/pcsr-foreground-adoption-grain.md).
Three two-arm matched pairs, not an experiment with its own number:

- the first pair, on a research-only instrument (`visibility-grain-ab.csv`);
- a second round of two pairs on the setting as it was later implemented, one
  at the first pair's size and one longer (`adoption-grain-two-regimes.csv`),
  with the recovery job's publications timed
  (`adoption-grain-publications.csv`).

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

## `adoption-grain-two-regimes.csv` — 68 rows

One row per foreground request: two regimes, two arms each, 17 requests per
arm. The columns are those of `visibility-grain-ab.csv`, with `regime` added in
front, and they are read the same way. The `visible_frontier` column is the
adopted frontier and `durable_match` is the published one.

The build is the hpcp11 release (#3793, #3840 and #3842 integrated) plus the
adoption commit as it was then proposed for #3793. That commit adds a per-model
setting, `canonical_state_adoption_grain_blocks`. The build also carries one
research-only log line, which reports both frontiers and the recovery job's
counters for each request. Each arm set the grain through the model settings
file, and the arm stopped unless its first request's log line reported that
grain. Nothing else differed from the first pair: same settings, same seed
5793, same step, gap and output length, with a fresh server and a fresh prefix
cache per arm. Production was stopped for the whole window.

| regime | arm | `grain_blocks` | prompt tokens | first send – last response (local clock, 2026-09-23) |
|---|---|---|---|---|
| `R1` | `B` | 4 | 36,027 → 44,208 | 15:30–15:47 |
| `R1` | `A` | 1 | 36,027 → 44,208 | 15:48–16:05 |
| `R2` | `A` | 1 | 60,024 → 68,213 | 16:07–16:32 |
| `R2` | `B` | 4 | 60,024 → 68,213 | 16:33–16:56 |

`R1` is the first pair's workload regenerated; its hash matches the first
pair's arm A. `R2` starts the same generator at 60,000 tokens. Every arm ran a
fixed 17 requests, rather than stopping at a transition count as the first
pair's arm B did.

## `adoption-grain-publications.csv` — 49 rows

One row per `CanonicalRecovery: published` line in each arm's log segment of
the second round.

- **Observed:** the published prefix, `published_tokens`.
- **Derived:** `elapsed_s` is the line's timestamp minus the arm's first send
  time, and `before_last_request` compares it with the arm's last send.
  `arm_last_send_elapsed_s` repeats that send time on every row, so that
  durable progress can be compared at equal elapsed time. Arms that ran for
  different lengths are otherwise not comparable at their last request.

Caveats for the second round, in addition to the three above:

- The neural-engine prefill path was again unavailable to the research
  process.
- Each regime is one pair, arms run one after the other: `R1` B first, `R2` A
  first.
