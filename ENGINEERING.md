# What was built to get there

The finding in
[FINDINGS.md](experiments/exp-001-reusable-state-economics/FINDINGS.md) was
not available to someone who only benchmarked. It came out of a sequence of
runtime designs, each of which invalidated an assumption the previous one
rested on, and this file is that sequence.

The stages are ordered by what each one changed in the system, not by date.
Every measured number is in `data/exp-001/` and each stage names its file;
the remaining numbers are configuration constants such as tile size, keep
fraction and threshold, and are named as such. Where a stage was never
measured, I say so.

![System evolution](figures/fig8-system-evolution.svg)

## Stage 0 — Dense baseline

One 27B-class dense model at 4-bit on one Apple silicon machine, prefill on the
GPU, a block-structured prefix cache with 1024-token blocks. Cold prefill at
16K ran at about 302 tok/s, which at that prompt size is a 57.84 s wait for
the first token. Warm continuation was excellent: a growing session restored
most of its context from the cache on every turn and paid only for the new
suffix.

That baseline has both properties an interactive workload needs, and only one
of them was being measured. The whole study is what happens when you optimize
the measured one.

`data/exp-001/cold-prefill.csv`, dense columns.

## Stage 1 — Heterogeneous prefill

The first lever was moving part of the prefill computation off the GPU onto
the neural engine. The model's MLP and gated-delta-net layers were partitioned
between the two, with a fraction of each assigned to the neural engine at a
fixed 1024-token tile, running two engine instances side by side. The tile
size was chosen to match the prefix cache's block size, which looked like a
convenience at the time and turned out to matter later, when the background
work in stage 5 needed a unit that was also a valid cache boundary.

I did not keep a file for the GPU-only against dual-engine throughput
comparison from this stage, so no ratio for it appears anywhere in this
repository. The cut is recorded in
[LIMITATIONS.md](experiments/exp-001-reusable-state-economics/LIMITATIONS.md).

## Stage 2 — Sparse prefill composed with it

The second lever was SpecPrefill, an attention-based sparse prefill mechanism:
a 0.8B scorer reads the prompt, keeps the top 20% of tokens, and only those go
through full attention. It engages above 8192 prompt tokens. Stacked on stage
1, cold 16K went from
57.84 s to 19.24 s and 32K from 122.7 s to 33.5 s, and in an isolated
qualification, a single smoke run with one resident engine, the two
mechanisms composed at 95-97% of the ideal product of their separate
speedups. Two accelerators that nearly multiply was the best result in the
study by the standard I was applying at the time.

The same build, on an eight-turn growing session, ran 129.1 s against 108.1 s
dense-only. The cold-start win was real and the session came out slower
anyway. That was the first sign that the standard was wrong.

`data/exp-001/cold-prefill.csv`; `data/exp-001/session-aggregates.csv`,
rows `isolated_16k_*` and `stacking_fraction_of_ideal_pct`;
`data/exp-001/hybrid-runtime.csv`, row `session_8turn_sparse_no_recovery_s`.

## Stage 3 — Correctness before performance

Instrumenting the sparse path for stage 2 meant logging the boundary it uses
to protect the system prompt and tool definitions from being dropped. The
logged boundary did not match the real one. It was derived by subtracting a
re-render of the non-system messages from the full render, which assumes a
chat template emits the same thing regardless of which roles are present, and
this template does not. With tools in play the derived boundary fell as
little as 37 tokens short (`data/exp-001/session-aggregates.csv`, row
`boundary_shortfall_min_tokens`), which is the smallest shortfall observed in
this study's own run and configuration. That placed the close of the tool
instructions and the start of the operator's system prompt inside the region
sparse prefill is allowed to discard: tokens the runtime contract required to
stay fully computed became eligible for sparse processing. The later upstream
reproduction in PR #3756 measured different counts on current upstream code,
on a different path; those are its numbers, not a restatement of this one.

The fix measures the boundary instead of inferring it: render the static
messages twice through the caller's own template with two different throwaway
turns, and keep the token prefix both renders share with the real prompt.
Tokens the two probes agree on cannot depend on conversation content. That
became [oMLX PR #3756](https://github.com/jundot/omlx/pull/3756), which is
open at the time of writing. The session work that follows ran on builds
carrying it, because a latency comparison against a configuration that
violates the prompt contract is not a comparison. I did not measure a
downstream semantic failure caused by the boundary bug, and do not claim
one.

## Stage 4 — Sparse first, dense later

If a sparse request is fast but its sparsified suffix does not advance the
normal reusable dense prefix state, and a dense request is slow but leaves a
checkpoint, the obvious design is both: serve the request
sparse, then rebuild the dense prefix it skipped while the scheduler has
nothing else to do.

![Sparse first, dense later](figures/fig9-hybrid-architecture.svg)

The job tracks prompt tokens only. Generated output is re-rendered by the
chat template before the next turn, and including it in the job's identity
made a growing conversation look like an unrelated one and restarted the job
from the beginning. That was the first of three bugs that had nothing to do
with prefill and everything to do with whether background work can coexist
with a request path.

## Stage 5 — Incremental materialization

The background job does not wait until the whole prefix is dense before
publishing anything. It works in 1024-token slices, and every completed block
is written through the same store path a foreground request uses. A turn that
arrives while the job is halfway through finds half a checkpoint, which is
worth exactly half.

That shows up as a declining cost for the turns that arrive while the job is
still running: 10.16 s, 8.19 s, 6.07 s, 3.91 s, 1.77 s across five consecutive
turns, each one finding more of the prefix already stored. It also decides
what the admission estimate should charge. Charging the first turn's price put
the sparse-or-dense decision exactly on its own break-even, where a 1% change
in the measured densification rate moved an eight-turn session between 81.8 s
and 111.1 s. Charging the mean of the declining sequence moved it off that
knife edge.

The design intended to fail closed: store only tokens the job has densely
processed, always on a block boundary, on a hybrid attention-plus-recurrent
model only from a boundary-aligned state snapshot, and drop the job on memory
pressure, on cache corruption recovery, on reset and on shutdown. A later
review of the implementation found that it did not fully achieve this; the
gaps are listed under [Prototype safety review](#prototype-safety-review)
below.

`data/exp-001/waiting-turn-cost.csv`; `data/exp-001/hybrid-runtime.csv`, rows
`breakeven_*`.

## Stage 6 — Making background work stay in the background

Deferred work is only deferred if the scheduler can yield to a request, and
at first it could not. Two separate defects.

**Arrival visibility.** A request is invisible to the scheduler until its
admission runs on the single-worker executor, and that cannot happen while a
step is in flight. So the scheduler looked idle, started a background slice,
and the request that had already arrived waited behind it. Five requests
queued for a total of 6.7 s this way. The fix is an inbound counter raised
before the executor hand-off, so the scheduler knows a request exists before
it can be admitted.

**Executor starvation.** A slice holds the interpreter lock for its whole
duration, which starves the loop that accepts requests. Back-to-back slices
left no window in which a request could announce itself at all. The fix is
requiring two consecutive idle steps before a slice starts, at a cost of one
step interval per slice.

Foreground generation throughput went from 13.5 tok/s to 47 tok/s once slices
stopped overlapping decode, in the one run where I measured it. At the lower
figure the assistant visibly types at a third of its speed while the job
runs; at the higher one the job is not noticeable.

![Background recovery](figures/fig7-background-recovery.svg)

`data/exp-001/hybrid-runtime.csv`, rows `foreground_generation_tok_s` and
`queueing_behind_five_requests_s`.

## Stage 7 — Controlled success

On a synthetic eight-turn session parameterized by idle time between turns,
the build with stages 4 through 6 in it beat dense-only by about 24% at 15 s
of idle and about 22% at 10 s, nearly drew level at 5 s, and lost by 11% at
zero idle. Cold 16K
time-to-first-token on the same corpus stayed at 15.7 s against 56.5 s.

The zero-idle row was already the warning. Recovery throughput has to outrun
context growth, and with no gap between turns it cannot. I read it at the
time as a deliberate trade: a much faster first token for 11% more total time
when the user never pauses. That reading assumed real users pause.

`data/exp-001/think-time.csv`; `data/exp-001/hybrid-runtime.csv`, row
`cold_ttft_16k_s`.

## Stage 8 — The real workload

In the coding-agent workload I observed, tool-driven turns left little or no
idle time. A file read puts the whole file in the prompt. A test run puts its
output in the prompt. The static prefix was large from the first request, the
context grew in bursts, and between tool calls there was almost no gap. The
one run per arm I have of that workload is not an effect-size measurement,
because the agents took different paths, but the per-turn cache hit rate in
the sparse arm fell from 88.2% to
27.1% while the dense arm ended near 98%. A hit rate is a property of what
the cache could restore for the prompt it was given, not of how long the
agent chose to work, so the shape of that column is more defensible than any
wall-clock ratio from the same sessions.

Context growth rate exceeded recovery rate. The controlled experiment showed
the algorithmic idea works when enough idle time exists; this workload
violated that precondition. The implementation safety gaps below are a
separate reason the prototype was not shipped, and neither reason subsumes
the other.

`data/exp-001/session-turns.csv`.

## Stage 9 — Isolating the mechanism

To see the mechanism without the recovery job in the way, I traced a session
on a build with none of stages 4 to 7 present: sparse prefill only. Twenty
consecutive prefix-cache restores. The checkpoint climbs for ten requests,
collapses at request 11 to the last block the cache would accept, and never
moves again while the uncached suffix grows from 17,060 to 33,979 tokens.
The cache layer logs the reason at the transition: a partial prefix match,
rejected to prevent serving stale state. The rejection is correct, and its
cost is the finding.

The order matters. Request 10 restored 37,888 tokens with a 6,902-token
suffix, below the threshold, and ran dense. The restore at request 11 is the
cliff, and it happened before any sparse admission on that request: the
17,060-token miss it left is what crossed the threshold and engaged
SpecPrefill. From then on every suffix was sparsified, a sparsified suffix
does not advance the normal reusable dense prefix state, and the checkpoint
never recovered. The request-11 sparse admission did not cause the
request-11 cliff, because the restore came first. The log names a partial
match whose last matched block held a placeholder; sparse prefill is capable
of leaving one, but the surviving trace does not establish when or how this
particular placeholder was created. The unrepaired state that follows is the
prefix-cache debt.

`data/exp-001/trace-b-*.csv`; Figure 3.

## Stage 10 — Deployment

No configuration in stages 1 through 7 is right for every request the server
receives. The one-shot long prompt wants sparse prefill. The continuation-heavy
session wants a checkpoint that keeps advancing. The healthy incremental
session does not care, because it never crosses the threshold. The server
cannot tell these apart at admission, and I do not have the evidence to build
a classifier that could.

So the decision moved to where the request shape is known. Locally, the
transport that agents use defaults sparse prefill off and accepts an explicit
per-request override; the long-context transport keeps its model-level
setting. Upstream, the change I sent is smaller than that policy:
[oMLX PR #3762](https://github.com/jundot/omlx/pull/3762) gives the Anthropic
messages endpoint the same three per-request sparse-prefill fields the
OpenAI-compatible endpoint already had, so a client that only speaks that API
can express the intent at all. It changes no default on either endpoint, and
it is open at the time of writing. The default-off policy is a deployment
choice for this serving setup and is not something I am recommending upstream.

## Prototype safety review

The build that stages 4 through 7 ran on was an experimental branch, and a
later review of that branch against the request path it copies found four
gaps. They are recorded here so that the stages above read as what they are:
a feasibility result, not a shippable component. None of them exists in the
served build, which carries no background densification at all.

- **Buffer synchronization.** The request path stores a cache from a worker
  thread under `_mx_buffer_access_lock`, so that a cache clear on the
  inference thread cannot reclaim a Metal buffer mid-read. The densification
  job calls the same store entry point from the scheduler step without taking
  that lock.
- **Cache lifecycle.** Intermediate progress stores publish blocks under the
  job's request id. The drop path releases the job's boundary snapshots and
  its in-memory cache, but does not call `clear_request_entry` or
  `release_for_eviction` for blocks already published, so a dropped job can
  leave blocks whose refcount is never returned.
- **Unsupported-cache gate.** The job is queued from inside the branch that
  refuses to store a sparse or unreconstructible cache, which is the only
  point where `_model_has_unreconstructible_cache()` is consulted. The job
  itself never re-checks that gate before it stores, so a model whose
  recurrent state cannot be reconstructed is protected only by the
  boundary-snapshot path it happens to take.
- **Liveness.** Idleness is decided by a hand-maintained inbound counter
  raised before the executor hand-off and lowered when admission runs. It is
  not tied to the scheduler's own `has_requests()` state, so a request that
  is counted inbound but never admitted leaves the job blocked until reset.

The three results of the prototype are therefore separate claims with
separate evidence:

| Layer | Result | Evidence |
|---|---|---|
| Algorithm | Background dense recovery is feasible when idle time exists. | Stage 7, `data/exp-001/think-time.csv` |
| Implementation | The experimental implementation failed a later safety and lifecycle review and was neither production-ready nor upstream-ready. | This section, read against the branch source |
| Workload | Even a corrected implementation would not solve the regime where context growth outruns recovery throughput. | Stage 8, `data/exp-001/session-turns.csv` |

## Why the discarded stages stay in the record

Stages 4 through 7 were set aside after stage 8, and they still earned their
place. Without the recovery job there is no think-time curve, without the
think-time curve there is no zero-idle row, and the zero-idle row is the
controlled statement of the condition the real workload then violated. The
trace in stage 9 was only clean because I knew, from stage 6, exactly which
code had to be absent from the build for the checkpoint series to have one
explanation.
