# Data

Seven files, one experiment, and everything the figures and the prose are
built from. Each entry gives the row count, the columns, and where the numbers
came from.

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

Every prefix-cache restore in one continuous session with sparse prefill
enabled and no background densification in the build. The `phase` column is
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

### `exp-001/README.md`

Provenance, integrity notes and the proximate cause for the three `trace-b-*`
files, including why both 20 and 19 are correct restore counts and why the
difference does not affect any row. Read it before using the trace; it is not
duplicated here.

## Provenance in general

The cold-prefill and think-time numbers come from benchmark runs. The
session-turns and trace-b numbers were extracted from session transcripts of
the original runs and cross-checked against the server log lines quoted in
the same transcripts. All of it is from one machine — Apple silicon, M4 Max,
64GB unified memory, a 27B-class MoE model at 4-bit — over a small number of
sessions, and the counts above are the whole population, not a sample.
