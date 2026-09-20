# Data

Sixteen CSV files, one JSONL file and this README. Twelve of the CSVs belong
to EXP-001 — the nine the study was built on, plus three tables from an earlier
campaign that replicates its finding. The rest support the research threads.
Everything the figures and the prose are built from is here. Each entry gives
the row count, the columns, and where the numbers came from.

Nothing here is smoothed, interpolated or back-generated. Where the source
reported a value it was copied at the precision it was reported; where the
source did not report one, the cell is empty and the column stays. No row was
reconstructed from prose, and no series was fitted or filled.

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

## Provenance in general

The cold-prefill and think-time numbers come from benchmark runs. The
hybrid-runtime and waiting-turn numbers are transcribed from the commit
message that recorded them at the time. The
session-turns and trace-b numbers were extracted from session transcripts of
the original runs and cross-checked against the server log lines quoted in
the same transcripts. All of it is from one machine — Apple silicon, M4 Max,
64GB unified memory, a 27B-class MoE model at 4-bit — over a small number of
sessions, and the counts above are the whole population, not a sample.

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

## Research threads

### `speculative-decoding/mtp-sequences.csv` — 431 rows

Columns: `source_dir`, `model`, `task`, `finish`, `output_tokens`, `cycles`,
`tokens_per_cycle`, `accepted`, `drafted`, `accept_pct`, `depth_detail`,
`backbone_ms`, `mtp_head_ms`, `sample_ms`, `cache_ms`.

One row per finished sequence that used the runtime's native draft head,
parsed from the server logs of coding-agent task runs in September 2026,
across two models of the same size class and five task types. Duplicate lines
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
