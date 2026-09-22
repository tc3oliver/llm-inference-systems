# Data

Fifty-nine CSV files, six JSONL files and this README. Twelve of the CSVs
belong to EXP-001 — the nine the study was built on, plus three tables from an
earlier campaign that replicates its finding. Three belong to EXP-002 and
nineteen to EXP-003; both sets have their own README
([EXP-002](exp-002/README.md), [EXP-003](exp-003/README.md)). Eight more are the
zero-idle validation round attached to EXP-003 and have
[their own README](pcsr-agent-validation/README.md) as well, and four are the
SpecPrefill positional-contract control that came out of validating it
([its README](specprefill-position-efficacy/README.md)). The rest support
the research threads.
Everything the figures and the prose are built from is here. Each entry gives
the row count, the columns, and where the numbers came from.

Nothing here is smoothed, interpolated or back-generated. Where the source
reported a value it was copied at the precision it was reported; where the
source did not report one, the cell is empty and the column stays. No row was
reconstructed from the research write-up, and no series was fitted or filled.
Some runtime measurements are transcribed from the contemporaneous commit
messages that first recorded them, which each entry below states explicitly;
being in a CSV does not raise a number's evidence level.

## exp-001

### `cold-prefill.csv` — 3 rows

Columns: `prompt_tokens`, `dense_ttft_s`, `accelerated_ttft_s`,
`dense_pp_tok_s`, `accelerated_pp_tok_s`.

Cold-start time-to-first-token and prefill throughput at three prompt sizes,
dense against accelerated, measured on an empty cache. The 14,300-token row
has TTFT only; throughput was not recorded for it and the two cells are
empty rather than derived. Evidence level 1, microbenchmark. Feeds Figure 1.

### `think-time.csv` — 4 rows

Columns: `think_time_s`, `dense_only_s`, `hybrid_s`, `hybrid_repeat_s`.

Total session wall time for a synthetic multi-turn interactive workload,
swept over the idle gap between turns. Two of the four think-time settings
were run twice; `hybrid_repeat_s` holds the second run where it exists and is
empty where it does not. The dense-only arm is flat at about 108 s across the
sweep, which is the control. The zero-idle row, where hybrid is slower than
dense-only, is part of the result. Evidence level 3. Feeds Figure 5.

### `session-turns.csv` — 6 rows

Columns: `turn`, `dense_hit_pct`, `sparse_hit_pct`, `sparse_scorer_calls`.

Per-turn prefix cache hit rate for one real coding-agent session per arm, and
the number of scorer invocations in the sparse arm. Turns 0-4 and turn 8; the
intervening turns were not captured. One run per arm, and the two agents took
different trajectories through the task, so the arms are not comparable as a
ratio — the useful signal is the shape of each column over turns. Evidence
level 4.

### `trace-b-restores.csv` — 20 rows

Columns: `seq`, `clock`, `cached_tokens`, `uncached_suffix`, `phase`.

Every prefix-cache restore in one continuous session with SpecPrefill, an
attention-based sparse prefill mechanism, enabled and no background
densification in the build. The `phase` column is
my label, applied after the fact from the checkpoint series; the other four
columns are transcribed values. `clock` is wall-clock `HH:MM:SS` within the
session, kept because the intervals show the scorer cost growing with the
suffix. Evidence level 5. Feeds Figure 3.

### `trace-b-scorer.csv` — 10 rows

Columns: `call`, `scored_tokens`, `seconds`.

Every scorer invocation in the same session, with tokens scored and wall
seconds spent. Evidence level 5. Feeds Figure 4.

### `trace-b-stores.csv` — 7 rows

Columns: `seq`, `stored_tokens`.

Every checkpoint written back to the prefix cache in the same session. The
series ends at 44,032 and nothing follows, which is the point of the file.
Evidence level 5.

### `hybrid-runtime.csv` — 6 rows

Columns: `metric`, `before`, `after`, `unit`, `note`.

Runtime measurements from the experimental build that recovers the dense
prefix in the background after a sparse request. That build was designed to
fail closed and a later review found four gaps in it; see [Prototype safety
review](../ENGINEERING.md#prototype-safety-review). Foreground decode
throughput with a background slice running, before and after the scheduler
stopped overlapping slices with decode; the queueing that five already-arrived
requests suffered behind slices the scheduler started while it believed itself
idle (measured before the fix only, so `after` is empty); a cold 16K
time-to-first-token pair from the same corpus as the session runs, which is a
different run from `cold-prefill.csv` and is kept separate for that reason;
the eight-turn session with sparse prefill and no recovery at all; and the two
session times that a 1% change in the measured densification rate moved the
admission decision between, when the estimate charged the first waiting turn's
price. Each value is a single run. Source: the commit message of the
densification change on the experimental branch, which is the only record of
these runs. Evidence level 3 for the session figures, level 1 for the rest.
Feeds no figure; cited in `ENGINEERING.md`.

### `waiting-turn-cost.csv` — 5 rows

Columns: `turn`, `seconds`.

How long each of five consecutive turns waited on the background
densification job as the dense prefix filled in. The sequence declines
because each turn finds more of the prefix already stored. It is the reason
the admission estimate charges the mean of the sequence rather than the first
turn's price. Same source and evidence level as `hybrid-runtime.csv`.

### `session-aggregates.csv` — 17 rows

Columns: `metric`, `value`, `unit`, `note`.

Values that are quoted in the prose but come from session-level aggregates or
single observations rather than a per-request series: the isolated
qualification of the two accelerators stacked, the three session wall times
that prompted the study and are not comparable as a ratio, the third-regime
session, the smallest observed protected-prefix shortfall, the earlier
long-context reference run from a different configuration, and the
neural-engine latch reproduction that lives in the appendix. Several are
approximate and say so. They are collected here so that every number in the
prose has a file, not because a file makes them stronger than they are.
Evidence levels 2, 4 and 6 between them.

### `exp-001/README.md`

Provenance, integrity notes and the proximate cause for the three `trace-b-*`
files, including why both 20 and 19 are correct restore counts and why the
difference does not affect any row. Read it before using the trace; it is not
duplicated here.

## Provenance of the nine EXP-001 files above

The cold-prefill and think-time numbers come from benchmark runs. The
hybrid-runtime and waiting-turn numbers are transcribed from the commit
message that recorded them at the time. The
session-turns and trace-b numbers were extracted from session transcripts of
the original runs and cross-checked against the server log lines quoted in
the same transcripts. All of it is from one machine — Apple silicon, M4 Max,
64GB unified memory, a 27B-class dense model at 4-bit — over a small number of
sessions, and the counts above are the whole population, not a sample. The
replication tables below and the research-thread files after them have their
own provenance, stated in each entry; two of them use different models.

### `replication-b-vs-e-turns.csv` — 87 rows

Columns: `arm`, `task`, `turn`, `prompt_tokens`, `cached_tokens`,
`uncached_tokens`, `output_tokens`, `cache_hit_pct`, `prefill_s`, `total_s`,
`prefill_tps`, `decode_tps`.

Per-turn measurements from a configuration bake-off run in September 2026,
before the rest of EXP-001. Same model and machine as the rest of the study,
on an older build of the server, against a different set of coding-agent
tasks. Two arms across five tasks: dense prefill, and neural-engine prefill
with sparse prefill enabled. Extracted from the campaign's own metrics file,
one row per turn, no derived columns.
Evidence level 4, real agent sessions, one run per arm. Feeds FINDINGS
section 6.

### `replication-b-vs-e-isolated.csv` — 3 rows

Columns: `case`, `dense_s`, `dense_prefill_tok_s`, `accelerated_s`,
`accelerated_prefill_tok_s`, `speedup`.

The isolated fresh-prefill measurements from the same campaign, which is what
made the accelerated configuration look like the right choice before the
session data arrived. The new-session row has no throughput figures recorded
and the two cells are empty rather than derived.

### `replication-b-vs-e-summary.csv` — 4 rows

Columns: `arm`, `task`, `turns`, `cache_hit_first_pct`, `cache_hit_last_pct`,
`cache_hit_median_pct`, `uncached_tokens_total`, `prefill_s_total`,
`wall_s_total`.

The two long tasks of that campaign aggregated per arm. Every value is a sum,
a median or a first/last read of the per-turn table above; nothing new is
measured here.

## exp-002

Three files, documented in full in [`exp-002/README.md`](exp-002/README.md)
rather than here, because the run schema behind them has more columns than a
summary entry can carry honestly.

`runs.csv` — 36 rows, one per measured run, written by `harness/` against a
research instance and validated against `schemas/run.schema.json`. Every metric
is nullable and an unmeasured metric is empty, never `0`. Evidence level 2.

`matched-comparisons.csv` — 11 rows, one per speculative arm paired against the
dense arm of the same cell, written by `analysis/exp002.py` from `runs.csv`.
Feeds Figures 10 and 11. Evidence level 2.

`production-mtp-sequences.csv` — 17 rows, observational, parsed from the served
instance's own log by `harness/mtp_log.py`. Counters and timers only; no prompt
fragment, completion fragment, request identifier or timestamp is carried
across. No matched arm, so it supports no latency claim on its own.

## Research threads

### `speculative-decoding/mtp-sequences.csv` — 431 rows

Columns: `source_dir`, `model`, `task`, `finish`, `output_tokens`, `cycles`,
`tokens_per_cycle`, `accepted`, `drafted`, `accept_pct`, `depth_detail`,
`backbone_ms`, `mtp_head_ms`, `sample_ms`, `cache_ms`.

One row per finished sequence that used the runtime's native draft head,
parsed from the server logs of coding-agent task runs in September 2026,
across two models of the same size class and six task groups. Duplicate lines
were removed where a record appeared in both the log file and captured
standard output. Observational: these were agents doing work, not a
controlled matrix. No matched arm with the mechanism disabled exists.

### `speculative-decoding/mtp-by-model-and-task.csv` — 11 rows

Columns: `model`, `task`, `n`, `accept_pct_median`, `accept_pct_min`,
`accept_pct_max`, `tokens_per_cycle_median`.

The table above grouped by model and task. Medians and ranges only.

### `correctness/cache-restore-output-identity.csv` — 14 rows

Columns: `pass_no`, `case`, `prompt_tokens`, `cached_tokens`, `latency_s`,
`output_sha`, `completion_tokens`.

Seven synthetic prompts issued twice each, cold then warm, with the output
hashed. Tests whether serving a prompt from a restored prefix changes what
the model produces. Prompts were generated for the test and contain no real
content.

### `correctness/attention-route-logit-divergence.csv` — 13 rows

Columns: `run`, `prompt_tokens`, `ttft_s`, `sdpa_routes`, `bounded_routes`,
`first_bounded_chunk`, `logits_sha`, `argmax_token`, `top3_tokens`,
`top3_logits`, `output_sha`.

One 68,034-token prompt run across three builds that differ in how many of
the 256 attention-routing decisions take the bounded path. The prompt itself
is a real captured agent prompt and is **not** published; only its token
count, the hashes and the logit values of the top three candidates appear
here, none of which carry its content.

### `exploratory/native-mtp-dense-baseline.jsonl` — 28 records

One JSON object per run, validated against `schemas/run.schema.json`. The
disabled arm of a controlled speculative-decoding matrix that was stopped
before its enabled arm ran. Kept because the dense numbers are real; it
supports no comparison. The records still carry the identifier of the
abandoned matrix, left unedited rather than rewritten.

### `matched-tail-routing/matched-tail.csv` — 10 rows

Columns: `tail_requested`, `route_requested`, `server_git_sha`,
`warm_prompt_tokens`, `warm_route`, `warm_ttft_s`, `prompt_tokens`,
`canonical_prefix_tokens`, `uncached_tail_tokens`, `route`,
`threshold_tokens`, `ttft_s`, `prefill_s`, `decode_s`, `decode_tps`,
`output_tokens`, `prefill_pauses`, `throttle_notices`.

Five uncached-suffix sizes, each served twice with the foreground route pinned
per request and everything else held: same model, same warm turn, same measured
prompt, a fresh server with the cache directory removed for every cell. The
first comparison of the two prefill routes at a suffix that ran both ways;
before this the admission threshold chose the route, so the two arms occupied
disjoint suffix ranges.

`warm_ttft_s` is the pairing check rather than a result — all ten cells land
within 59 ms of each other, which is what establishes that a pair met the same
state. `prefill_pauses` counts the adaptive-throttle pauses the server logged
inside that request's own window and is kept beside every latency because the
throttle fired on the dense side only; it is not folded away. `decode_tps` is
empty throughout: 24 output tokens against tens of thousands of prefill tokens
gave the runtime no separate decode-rate sample, and the column is kept rather
than filled. One run per cell, except the 2,048 dense cell, which was run twice
across an aborted and a restarted campaign and agreed to 0.7 ms; only the
second is recorded here.

Evidence level 3. Supports
[the SpecPrefill admission economics candidate](../research-threads/specprefill-admission-economics.md).

### `recovery-foreground-qos/collision-summary.csv` — 9 rows, `collision-probes.csv` — 216 rows

Summary columns: `run`, `slice_tokens`, `cell`, `budget_pct`, `recovery`,
`probes`, `probe_ttft_min_s`, `probe_ttft_p50_s`, `probe_ttft_p90_s`,
`probe_ttft_max_s`, `probes_over_1s`, `turn_ttft_s`, `server_git_sha`.
The probe file carries every individual probe: `run`, `slice_tokens`, `cell`,
`budget_pct`, `probe`, `ttft_s`, `wall_s`, `prompt_tokens`.

What a foreground request waits for when background recovery is running. One
large sparse turn queues a recovery job, then twenty-four tiny requests are
fired through the idle window that follows, four seconds apart. The per-probe
file is kept because the distribution is two modes — a probe either lands
inside an uninterruptible recovery unit or it does not — and a summary of a
bimodal distribution describes neither mode.

`run` separates three campaigns on one build: `initial` at the original
work-unit size, `slice-sweep` across three execution-slice sizes, and `confirm`
re-running the original three cells at the selected slice. `slice_tokens` is 0
where recovery ran on the ordinary prefill step size. One run per cell.

### `recovery-foreground-qos/slice-timing.csv` — 3 rows

Columns: `slice_tokens`, `slices`, `tokens_per_slice_median`,
`slices_without_publish`, `duration_median_s`, `duration_max_s`,
`slices_with_publish`, `publish_duration_median_s`, `publish_duration_max_s`,
`publish_cost_median_s`, `ms_per_token`.

The blocking unit timed from inside the runtime, one record per uninterruptible
recovery slice, aggregated here by slice size. Slices that published a
canonical block are separated from those that did not, because the question the
table exists to answer is whether the publication critical section sets a floor
under the foreground wait. Evidence level 5 — these are the runtime's own
timings of its own slices, not a wall-clock inference from outside.

### `recovery-foreground-qos/idle-cost.csv` — 4 rows

Columns: `build`, `cell`, `budget_pct`, `idle_window_s`, `idle_cpu_s`,
`idle_cpu_share`, `blocks_published`, `recovery_service_s`, `rss_start_bytes`,
`rss_end_bytes`, `server_git_sha`.

CPU seconds consumed by the server process across a fixed 150 s idle window,
with a recovery job live. `build` distinguishes two runs of the same source
tree differing by one line, which is what isolates scheduler spin from the
recovery work itself. `blocks_published` is counted from the server's own
publication log lines inside each cell's window and is the column that makes
the two capped cells comparable — both published four. `recovery_service_s` is
empty for three of the four rows: the counter was read under the wrong key
name in that runner and the value is absent rather than zero.

CPU time is the right instrument here and the wrong one for the work: fifty-odd
seconds of accelerator prefill registers under two seconds of CPU, so what
these rows measure is the scheduler loop, which is the thing under test.

### `recovery-foreground-qos/shared-budget-two-model.csv` — 2 rows

Columns: `model`, `role`, `prompt_tokens`, `route`, `uncached_tail_tokens`,
`turn_ttft_s`, `engine_recovery_service_s`, `engine_chunks`,
`engine_publishes`, `engine_committed_tokens`, `budget_pct`, `budget_shared`,
`budget_owners`, `budget_service_share`, `budget_windows`,
`budget_overshoot_s`, `budget_window_service_s`, `idle_window_s`,
`server_git_sha`.

Two models loaded at once, a sparse turn each so both engines carry recovery
debt, then a 150 s idle window, against a 10% aggregate ceiling. One row per
engine.

The columns divide into two kinds and the division is the point. The `engine_`
columns are that scheduler's own counters and differ between the rows. The
`budget_` columns are read from the budget object, and `budget_windows`,
`budget_overshoot_s` and `budget_window_service_s` are identical across the two
rows to six decimal places — which is the evidence that both engines are
reading one object rather than two that happen to agree. `budget_service_share`
differs in the fifth decimal only because the two probes were sent a second
apart and its denominator is elapsed wall time; it is the one budget column
that must not be compared for equality.

One run. Under a per-engine budget each of these engines would have had the
full 10% allowance to itself.

The five files above support
[background work under foreground QoS](../research-threads/background-work-under-foreground-qos.md).
Research instance of the runtime alone on the machine, the long-running local
service stopped; model, greedy sampling, multi-token prediction off, cache
block size 4096. Arms separated by a server restart with the cache directory
removed.

### `recovery-foreground-qos/recovery-state-retirement.csv` — 18 rows

Columns: `point`, `description`, `prompt_tokens`, `slice_tokens`,
`processed_tokens`, `committed_tokens`, `state_resident`, `reclaim_performed`,
`state_cache_bytes`, `state_kvcache_bytes`, `state_arrayscache_bytes`,
`mlx_active_bytes`, `mlx_buffer_cache_bytes`, `physical_footprint_bytes`,
`guard_visible_bytes`.

How much live memory a parked canonical-recovery state holds, and what comes
back when it is retired. Six points at each of three prompt sizes, one process
per size:

| point | `state_resident` | `reclaim_performed` | |
|---|---|---|---|
| A | 0 | 0 | model loaded, allocator settled, no state yet |
| B | 1 | 0 | target reached over 512-token slices |
| B' | 1 | 1 | one `mx.clear_cache()`, state still held |
| B'' | 1 | 1 | two further clears, state still held |
| C | 0 | 0 | state dropped, no reclaim |
| D | 0 | 1 | state dropped, then reclaim to convergence |

**B'' is a control and the dataset is not readable without it.** B' is an
unconverged reading: `mx.clear_cache()` does not settle in one call, so the
B'-to-D difference reads as a multi-gigabyte effect of holding the state. B''
shows it is not — with the state still resident, physical footprint has already
reached D's value to within 2 MiB at every size. The first conclusion drawn
from this harness was the opposite of the published one, and the control is the
reason.

Three columns are three different instruments and they do not agree, which is
the finding rather than a defect: `state_cache_bytes` is the harness walking
the cache objects, `mlx_active_bytes` is `mx.get_active_memory()`, and
`physical_footprint_bytes` is the process resident footprint.
`guard_visible_bytes` is what the scheduler's own memory guard would compute,
`max(active, phys − hot_cache_cpu)`, with the hot-cache term zero here.

`state_cache_bytes` splits by cache kind because `.state` alone under-reports:
on a hybrid model the linear-attention layers hold a fixed-size recurrent state
that `.state` does not surface, and the first run of this harness missed
18.63 MiB of it. The walker recurses over `.state` *and* `vars(layer)` and
de-duplicates by identity.

Model `mlx-community/Qwen3.5-0.8B-MLX-4bit` — same family and layout as the
served 27B (`qwen3_5`, `full_attention_interval` 4) at a size that can be
loaded beside a running service. The served model was not loaded for this. One
run per size; `physical_footprint_bytes` is **not** reproducible run to run and
the next file shows the spread. Evidence level 2, isolated qualification.

### `recovery-foreground-qos/recovery-state-headroom-probe.csv` — 4 rows

Columns: `arm`, `repetition`, `prompt_tokens`, `probe_alloc_bytes`,
`settled_mlx_active_bytes`, `settled_mlx_buffer_cache_bytes`,
`settled_physical_footprint_bytes`, `probe_mlx_active_bytes`,
`probe_physical_footprint_bytes`, `probe_physical_growth_bytes`.

The negative result. Two arms — `hold` keeps the recovery state resident,
`retire` drops it — each settled and then given a 1 GiB foreground allocation,
twice. Separate processes per arm because the Metal heap only grows, so
whichever arm ran first would set the footprint the second is measured against.

`probe_physical_growth_bytes` is lower in `retire` than in `hold`, which is the
direction the mechanism predicts, and by 104 MiB and 48 MiB against a retained
state of 210.6 MiB. `settled_physical_footprint_bytes` for the two `hold`
repetitions is 4,700.2 MiB and 1,764.1 MiB — for identical work. The baseline
varies by more than the effect, so **no foreground headroom effect is
established**, and this file is kept because a negative result that is not
published gets re-derived as a positive one.

Both files are regenerated into readable form by

    uv run python -m analysis.recovery_state_retirement

which refuses to print a per-token law unless the three sizes are exactly
collinear, and prints the production-geometry scaling separately and labelled
**derived**. They support
[background work under foreground QoS](../research-threads/background-work-under-foreground-qos.md)
§5 and
[EXP-003 `HARDENING.md`](../experiments/exp-003-progressive-shadow-prefill/HARDENING.md)
finding 2. Run 2026-09-22 against the
[omlx#3793](https://github.com/jundot/omlx/pull/3793) branch during review
hardening; they measure the runtime mechanism, not an EXP-003 workload, which
is why they are here and not in `exp-003/`.

### `specprefill-draft-cache-reuse/runtime-scoring.csv` — 37 rows

Columns: `arm`, `n_prompt`, `cached_tokens`, `suffix_tokens`,
`published_boundary`, `extractions`, `scoring_s`, `baseline_equivalent_s`,
`evidence_note`. It has [its own README](specprefill-draft-cache-reuse/README.md),
which carries the column-by-column provenance and the fit.

One row per SpecPrefill draft scoring across two arms run back to back on
2026-09-22: 10 rows for `baseline`, the logical-offset fix
([omlx#3840](https://github.com/jundot/omlx/pull/3840)) alone, and 27 for
`treatment`, which adds the boundary snapshots
([omlx#3842](https://github.com/jundot/omlx/pull/3842)). Draft cache hits go
from 0 of 10 to 23 of 27.

**The arms are not a matched pair and the totals are not the comparison.** Each
session's own output steered what was asked next, so the two arms saw different
prompts, which is why the scoring counts differ. `baseline_equivalent_s` is a
least-squares fit of the baseline arm against `n_prompt`, evaluated per
treatment row — **derived**, and extrapolated for the 13 rows past the fitted
range, which `evidence_note` marks. Everything else in the file is the
runtime's own report of one scoring. Parsed from the served build's log by an
extractor kept with the run's preservation copy; token counts and timings only,
no prompt text and no model output. Supports
[hybrid draft prefix reuse in SpecPrefill](../research-threads/specprefill-draft-cache-reuse.md).

## pcsr-agent-validation

Eight CSVs and their own [README](pcsr-agent-validation/README.md), which
carries the provenance in full because two kinds of evidence sit in that
directory and must not be read as one. In short: a matched two-arm pair over
the tracked zero-idle `append-heavy-80k` shape, and one observational Claude
Code session against the same instance. The round is attached to EXP-003 and
is not a new experiment.

### `zero-idle-turns.csv` — 10 rows

Five turns for each of two arms, `spec` and `pcsr`, over one five-turn
appending session reaching 77,920 tokens with no idle between turns. Token
counts are exact. Evidence level 3 for the session, level 5 for the mechanism.

### `zero-idle-publications.csv` — 3 rows

One row per canonical publication, paired with the first later fetch that
matched its boundary. Both ends are the runtime's own trace.

### `zero-idle-summary.csv` — 2 rows

One row per arm. The recovery counters are cumulative, so each total is the
last reading rather than a sum.

### `claude-code-requests.csv` — 24 rows

One row per foreground request of one Claude Code session. Observational: the
trajectory is its own and nothing here is an effect size.

### `claude-code-publications.csv` — 2 rows

The session's two publications against the very next fetch each. One matched
its boundary exactly; the other was overtaken by the ordinary write-back path
and is marked not attributable.

### `claude-code-runtime-summary.csv` — 1 row

Gap distribution, recovery totals and the count of requests whose recovery
candidate was declined. Twenty-three of the twenty-four were declined
`not_sparse`.

### `semantic-control-{turns,summary}.csv` — 18 and 3 rows

Three arms — dense, SpecPrefill, SpecPrefill with recovered canonical state —
over one six-turn session whose last turn repeats the fifth turn's prompt
unchanged. All three probe hashes differ, and the recovery arm is the only one
that answered the repeated prompt differently from the prompt it repeats.
