# PCSR under coding-agent workload geometry

A validation round attached to
[EXP-003](../../experiments/exp-003-progressive-shadow-prefill/), not a new
study. It closes one gap the experiment's own
[METHODOLOGY.md](../../experiments/exp-003-progressive-shadow-prefill/METHODOLOGY.md)
names: every earlier round gave the session an idle gap between turns, and
EXP-001 measured that real coding-agent turns leave little or none. Nothing here
measures a speedup, and nothing here is a session-level result.

Two kinds of evidence sit in this directory and they are not interchangeable.

- **Controlled.** `zero-idle-*.csv` are a matched pair: one session shape, one
  seed, one token sequence, two arms differing in the shadow settings and
  nothing else. This is where a comparison is allowed.
- **Observational.** `claude-code-*.csv` are one real Claude Code session
  against the same instance. Its trajectory is its own — tool calls, turn
  count, tool latency and completion path all vary between runs — so nothing in
  it is an effect size. It answers whether the mechanism's state transitions
  occur under naturally occurring agent slack, and no more.

## Workload

The controlled pair runs
[`workloads/shapes/append-heavy-80k.yaml`](../../workloads/shapes/append-heavy-80k.yaml)
at seed 301: five appending turns, each adding roughly 16K of
tool-output-shaped context on top of everything before it, `idle_s: 0` on every
turn. The prompts reach 15,635, 31,238, 46,801, 62,395 and 77,920 tokens.

Token counts are **exact**, not approximated: the generator tokenized with the
tokenizer from the served model's own directory, and the run records carry no
`approx` note. The earlier EXP-003 rounds are not guaranteed the same, and the
figures here are not comparable to theirs token for token.

The observational session is one ordinary coding task — locate a defect in the
harness, read the files around it, change it, run the test suite — given to
Claude Code in a throwaway clone of this repository. The task was chosen before
the instance was configured, no pause was inserted, and compaction was neither
forced nor avoided. The agent finished the task.

## Runtime

| | |
|---|---|
| build | the research branch behind [omlx#3793](https://github.com/tc3oliver/omlx/pull/3793), commit `9c136170` |
| relation to the pull request | the pull request's head `7dd0b6fb` carries the same recovery mechanism with no instrumentation at all: no `shadow_` usage counters, no trace, no log line. The counters and the trace this directory is built from exist only on the research branch, which is why the round was run there |
| model | `Qwen3.8-27B-oQ4e-mtp`, cache block size 4096, context 131,072 |
| drafter | `mlx-community/Qwen3.5-0.8B-MLX-4bit`, keep 20%, sparse-prefill threshold 8,192 |
| recovery budget | 10% of wall time, 30 s tumbling window |
| multi-token prediction | off in the controlled pair, on in the observational session — the latter mirrors the production instance this machine actually serves Claude Code from |

**The budget is a server-level setting, not a per-model one.** The engine pool
owns one `ShadowBudget` for the whole process and builds it from
`scheduler.shadow_prefill_global_budget_pct`, which defaults to `0.0`, and
`ShadowBudget.allows()` returns `False` at a non-positive percentage. A
per-model `shadow_prefill_budget_pct` is written onto the scheduler config by
`apply_shadow_prefill_settings` and then never read once the pool owns the
budget. A first attempt at the PCSR arm here set only the per-model value and
every recovery candidate was declined with `reason=no_budget`; that run is kept
in the session scratchpad and is not published, because it measures a
misconfiguration and not the mechanism.

## `zero-idle-turns.csv` — 10 rows

Columns: `arm`, `turn`, `prompt_tokens`, `cached_tokens`,
`uncached_tail_tokens`, `foreground_route`, `ttft_s`, `prefill_s`, `e2e_s`,
`output_tokens`, `output_sha`, `context_growth_tokens`, `recovery_service_s`,
`recovery_processed_tokens`, `canonical_committed_tokens`,
`canonical_progress_tokens`, `canonical_debt_tokens`, `publications`,
`recovery_between_turns_tokens`.

Five turns per arm. The `spec` rows leave every recovery column empty rather
than zero, because a build reporting no counters and a build reporting zeros
are different things.

Every turn of both arms took the `specprefill` route, and `cached_tokens` is
`0` on all five `spec` turns: the sparse route writes nothing back, so the
control re-reads its whole prompt every turn. The `pcsr` arm restores 0, 4,096,
4,096, 8,192 and 12,288 tokens.

## `zero-idle-publications.csv` — 3 rows

Columns: `publish_seq`, `boundary_tokens`, `restore_seq`,
`restore_matched_tokens`, `restore_prompt_tokens`, `restore_remaining_tokens`.

One row per publication, paired with the first later fetch that matched its
boundary. Both ends are the runtime's own trace records, keyed on the trace's
own monotonic sequence number, so "later" is a comparison and not an inference.
All three publications were restored.

## `zero-idle-summary.csv` — 2 rows

Columns: `arm`, `turns`, `final_prompt_tokens`, `cumulative_foreground_s`,
`total_recovery_service_s`, `total_recovered_tokens`, `total_publications`,
`turns_restoring_published_state`, `growth_vs_recovery`.

The runtime's recovery counters are cumulative, so a session total is the last
reading and not a sum over the turns.

**On the latency column.** It is in the table and it does not lead, because one
run per arm cannot carry an effect size. What can be said is narrower and is
worth saying: arm `spec` was run twice, under server settings differing only in
a recovery ceiling that is inert while shadow prefill is off, and its turns 1
through 4 reproduced to within 0.09 s. Against a repeat that tight, the `pcsr`
arm's per-turn differences on those same turns — +10.33, −3.24, +7.67 and
+5.10 s — are real and not noise, and they are a cost: the arm that prefilled
12,288 fewer tokens by turn 4 spent longer doing it, because the recovery
slices are charged to the same accelerator. Turn 0 is excluded: it is identical
work in both arms and the two `spec` runs disagree on it by 7.12 s, so nothing
about turn 0 is measured here. The first-pass `spec` run is in the session scratchpad and is
not published; it is a repeat of a control, not a third arm.

## `claude-code-requests.csv` — 24 rows

Columns: `req`, `arrival_s`, `prompt_tokens`, `restored_prefix_tokens`,
`uncached_tail_tokens`, `inter_request_gap_s`, `recovery_service_s`,
`recovery_slices`, `recovery_tokens`, `publications`,
`canonical_committed_tokens`, `reused_a_publication`.

One row per foreground request of the observational session. `arrival_s` is the
runtime's monotonic clock, not a wall clock, and is kept only so the gaps can
be checked against the slices. `inter_request_gap_s` is the interval between
two arrivals and therefore contains the previous request's own service time; it
is not a measure of slack. `reused_a_publication` is the weak test — some
earlier publication's boundary is at or below what this request matched — and
once the ordinary write-back path is ahead of the recovery it is true of every
later request. `claude-code-publications.csv` carries the strict test.

## `claude-code-publications.csv` — 2 rows

Columns: `publish_seq`, `boundary_tokens`, `next_fetch_seq`,
`next_fetch_prompt_tokens`, `next_fetch_matched_tokens`,
`next_fetch_remaining_tokens`, `attributable`.

Each publication against the **very next** fetch. A restore that matches a
boundary exactly is one the recovery is the only plausible source of; a restore
that overshoots it is one the ordinary write-back may have produced on its own,
and is marked not attributable rather than counted.

The first publication, at 36,864 tokens, was matched exactly by the next
request, whose uncached tail was 5,423 tokens. The second, at 40,960, was
followed by a fetch that matched 49,152 — the ordinary path was already past
it, and that publication's contribution is **not established**.

## `claude-code-runtime-summary.csv` — 1 row

Columns: `foreground_requests`, `gaps`, `gaps_with_recovery`, `gap_min_s`,
`gap_median_s`, `gap_max_s`, `total_recovery_service_s`,
`total_recovery_tokens`, `total_publications`,
`publications_restored_attributably`,
`publications_reached_by_a_later_restore`, `lineage_resets`.

**The session's limiting factor was not slack.** The gaps were 9.6 s to 160.9 s
with a median of 36.2 s, which is ample. Of the 24 requests, **23 had their
recovery candidate declined with `reason=not_sparse`**: the ordinary prefix
cache kept their uncached tails below the 8,192-token sparse-prefill threshold,
so the foreground never took the sparse route and no canonical debt arose to
repay. The one request that did take it — a turn where the context jumped from
31,892 to 42,075 tokens, leaving a 9,307-token tail — produced the whole chain.

`lineage_resets` is 0 and no fetch ever matched less than an earlier one, so
**compaction interaction was not exercised** in this session.

## Privacy and redaction

The runtime's shadow trace records token counts, block hashes and a monotonic
clock. It carries no prompt text, no tool result, no file content and no
completion. The derived CSVs here carry a strict subset of it. **The raw trace
and the server log from the observational session are not committed**, because
neither was written with publication in mind and neither is needed to check the
tables. `DATA_POLICY.md` governs as everywhere else in this repository.

## Limitations

- One machine, one model family, one quantization.
- The controlled pair is one run per arm, with one repeat of the control. It is
  evidence level 3 for the session and level 5 for the mechanism, on the same
  reading as EXP-003.
- The observational session is **one** session. A count out of it is a count,
  never a rate. Its gaps are as long as they are partly because the model is a
  27B local one; a faster server would leave less.
- The instrumented build is a superset of the pull request. What was measured
  is the mechanism the pull request carries; the numbers are the research
  build's.
- Output hashes differ between the two controlled arms on every turn. Sparse
  prefill scores the uncached tail, and the two arms had different tails from
  turn 1 on, so a different selection follows by construction. It is worth
  recording anyway, because it is the first round in this program where it
  happens: EXP-003's four-arm round found `spec`, `shadow-end` and `pass`
  bitwise identical on every turn, and its own README says why — `cached_tokens`
  was `0` on every sparse turn there, so PCSR restored nothing mid-session and
  the three arms were fed the same tail. Here PCSR restored 4,096 to 12,288
  tokens and the output moved. Whether the moved output is worse is **not
  established**; that PCSR can no longer be described as transparent to the
  foreground on this workload is measured. Turn 0 is not comparable at all: the
  two `spec` runs disagree with each other on it.
