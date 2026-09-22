# specprefill-draft-cache-reuse

One CSV, 37 rows, behind
[Hybrid draft prefix reuse in SpecPrefill](../../research-threads/specprefill-draft-cache-reuse.md).
It records what SpecPrefill's draft scoring did on every scoring of two arms
run back to back on 2026-09-22, and it is a two-arm comparison that is **not**
a matched pair. Read the divergence note below before reading a ratio out of
it.

## `runtime-scoring.csv` — 37 rows

Columns: `arm`, `n_prompt`, `cached_tokens`, `suffix_tokens`,
`published_boundary`, `extractions`, `scoring_s`, `baseline_equivalent_s`,
`evidence_note`.

One row per SpecPrefill draft scoring. 10 rows for `baseline`, 27 for
`treatment`.

| column | what it is | how it got here |
|---|---|---|
| `n_prompt` | tokens in the prompt being scored | **Measured** — the runtime's own line |
| `cached_tokens` | tokens the restored draft cache supplied | **Measured**; 0 means a miss, not a null |
| `suffix_tokens` | `n_prompt - cached_tokens`, the tokens actually prefilled | **Derived**, one subtraction |
| `published_boundary` | absolute token count of the recurrent checkpoint captured on that scoring | **Measured**; empty where no boundary was reachable |
| `extractions` | state extractions performed on that scoring | **Measured**; 1 or 0, never more, which is the design claim |
| `scoring_s` | wall time of the scoring pass, one decimal as the runtime reports it | **Measured** |
| `baseline_equivalent_s` | what the fitted baseline arm would have spent at this `n_prompt` | **Derived** — see the fit below. Empty on baseline rows, which are the fit's own source |
| `evidence_note` | `cold` / `warm`, and whether the fit was extrapolated to reach that row | bookkeeping |

`cached_tokens` is 0 on a miss because zero tokens were supplied, which is a
measurement. No cell in this file is a placeholder for something unmeasured;
where a quantity does not exist for a row — `published_boundary` where no
boundary was reachable, `baseline_equivalent_s` on the arm being fitted — the
cell is empty and the column stays.

## The two arms

Both ran on one machine against one served hybrid pair: a 27B-class dense
target with a 0.8B hybrid GDN draft model, draft `block_size` 1024.

- **`baseline`** is the treatment's own parent commit —
  [omlx#3840](https://github.com/jundot/omlx/pull/3840) alone, the logical-offset
  correctness fix. It can read a restored draft cache correctly; there is just
  never one to read.
- **`treatment`** adds [omlx#3842](https://github.com/jundot/omlx/pull/3842),
  which publishes a recurrent checkpoint at a reachable draft block boundary.
  Nothing else differs between the arms.

Totals, straight from the file: baseline 10 scorings, **0** draft cache hits,
33.1 s of scoring. Treatment 27 scorings, **23** hits, 38.7 s of scoring, 21
boundary captures over 18 distinct boundaries rising from 12,288 to 47,104.
Separately from this file, the runtime's `partial prefix match detected`
rejection appears 9 times in the baseline window and 0 times in the treatment
window.

## Why the totals are not the comparison

**The two sessions diverge.** Each arm drove a real task and the model's own
output steered what was asked next, so the arms did not see the same prompts —
that is why one has 10 scorings and the other 27. Comparing 33.1 s with 38.7 s
compares two different workloads and means nothing.

The comparison is per-prompt. `baseline_equivalent_s` is a least-squares fit of
the baseline arm's own `scoring_s` against `n_prompt`,

    -1.05 + n * 1.920e-4  seconds,   fitted on n = 13,291..34,010

evaluated at each treatment row's `n_prompt`. Summed over the treatment arm it
gives 38.7 s actual against 140.4 s of fitted equivalent. Two things bound that
number. **13 of the 27 treatment rows sit past 34,010**, where the fit is
extrapolated, and `evidence_note` marks each one. And summing the
one-decimal column in the CSV gives 140.3 s rather than 140.4 s; the 140.4 s
figure, which is the one the pull request quotes, accumulates at full precision
before rounding.

The four cold treatment rows are the arm's own control on cost: the capture
machinery runs on them and they land at 0.9x, 1.0x, 1.0x and 0.9x of the fitted
baseline, so it is not being paid for when it does not pay off.

## Provenance

Parsed from the served build's own log lines — `SpecPrefill: scored N tokens in
Ts`, its draft cache hit and boundary fields, and the sparse prefill line — by a
small extractor that selects a time window per arm. The extractor, the
comparison script and both raw arm tables are kept with the run's preservation
copy outside this repository; nothing was transcribed by hand and nothing was
reconstructed from the write-up.

Both arms were served by a local production instance with the pull-request
branches applied, which was returned to its own branch afterwards. The prompts
were a real coding task and are **not** published: the file carries token counts
and timings only, no prompt text, no model output, no paths and no host
identifiers.

## What this file does not establish

- Not a matched pair, for the reason above. Session wall time is not an effect
  size here and is not recorded.
- Not an output-equivalence result. Both arms finished the same task with the
  same answer, which is two stochastic sessions agreeing, not a fixed-seed
  comparison.
- One draft topology, one block size, one machine. The block-size replay that
  argues 2048 would behave the same is arithmetic over these rows rather than a
  second run, it is **Derived**, and it lives in the pull request, not here.
- Both pull requests are open at the time of writing. An open pull request is a
  proposal.
