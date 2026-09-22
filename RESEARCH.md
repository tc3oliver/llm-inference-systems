# Research program

Most published inference numbers describe a request arriving at an idle
server with an empty cache. Almost no production request looks like that. An
agent turn arrives carrying the whole conversation so far, against a server
that has seen most of it already, and its latency is dominated by how much of
that history still exists in reusable form. The gap between those two pictures
is what I work on.

The subject is LLM inference under real workloads: prefill behaviour, reusable
KV and prefix state, scheduling, long-context correctness, and heterogeneous
compute. The scale is one person and one machine, which bounds what can be
claimed and is stated wherever it bites.

## Method

**The evidence ladder.** Every finding is graded by the kind of evidence
behind it, from microbenchmark up to a change submitted outside this machine.
A number from an isolated benchmark and a number from a real agent session are
not interchangeable, and the grading exists so that nobody has to guess which
one they are reading. [`EVIDENCE.md`](EVIDENCE.md) defines the levels.

**Workload-shaped evaluation.** A result is not believed until it survives a
workload with the geometry of the thing it is supposed to help. In EXP-001 the
synthetic interactive workload and the real agent session disagreed, and the
real one was right. Synthetic tests still earn their place — they isolate
variables that a real session mixes together — but they do not close a
question.

**Negative results get published.** Four hypotheses in EXP-001 were refuted,
including two I expected to hold. They are written up by hypothesis in
`docs/negative-results.md`, not buried as a bug list, because the refutations
carry most of the information. The zero-idle row of the think-time table,
where the hybrid arm is slower than dense-only, is in the repository for the
same reason: reporting the 15-second row without it would be selective.

**No number without a source.** Every figure in this repository reads from a
CSV in `data/`. A claim I could not trace to a file was cut rather than
estimated, and the cuts are recorded in the experiment's `LIMITATIONS.md`.

## What exists

Three completed studies, nine pages under `research-threads/` in six
different states, and a set of questions that would need work nobody has done
yet. The categories are kept separate on purpose, because the difference
between them is the difference between a finding and an anecdote.

### Completed studies

**[EXP-001 — Reusable state dynamics](experiments/exp-001-reusable-state-economics/)**.
The cost of an inference optimization includes the reusable state it creates,
or fails to create, for later requests. Sparse prefill cut cold 16K
time-to-first-token from 57.84 s to 19.24 s and made a continuation-heavy
agent session slower, because a sparsified suffix does not advance the
reusable dense prefix state and the checkpoint stops recovering. Four
hypotheses were refuted, including two I expected to hold. A correctness bug
found while instrumenting it went upstream. Section 6 of its findings records
an earlier campaign, on an older build and different sessions, that shows
the same signature and was run before I knew it was a finding.

**[EXP-002 — A cost model for speculative decoding](experiments/exp-002-speculative-decoding-economics/)**.
Whether speculative decoding reduces latency is decided by the price of one
verify cycle measured in dense decode steps, not by the acceptance rate. On a
35B mixture-of-experts a four-position verify forward costs 2.43 dense steps and
a fixed draft depth of 3 is 10% slower than dense on code and 43% slower on
prose; on a dense 27B the same forward costs 1.37 and the mechanism is 1.81x
faster on a matched coding prompt. The runtime's existing adaptive controller
avoids every losing region and beats the fixed depth in the winning one, so no
change was proposed to it. 36 matched runs. Grew out of the speculative-decoding
thread, which is kept as written. It leaves one thing open and does not pretend
otherwise: with the mechanism on, greedy output stopped being reproducible and
stopped matching the dense decoder, and nothing here measures whether that
difference reaches the answer. That question belongs to the correctness thread
below, which now carries it as its fourth case.

**[EXP-003 — Progressive Canonical State Recovery](experiments/exp-003-progressive-shadow-prefill/)**
(*research result complete; upstream validation pending*. The directory keeps
its original slug — paths here are never renamed to match a change in wording.)
The debt a sparse prefill creates can be repaid in the background almost for
free, and repaying it is not the problem. A scheduler-owned dense re-read of the
range a sparse turn skipped was runnable on 32 scheduler steps, took 231.75 s —
53% of the session's wall time — read 24,575 of its 24,576-token target, and cost
the foreground 48.00 s against the sparse control's 47.93 s. The session still
ended with a canonical prefix of zero, because every block it published came back
at the next restore as a placeholder and was rejected. Starvation, throughput and
contention were each refuted by the runtime's own counters; what was left was
publication. A fourth arm published five times where another published
once, and reached a committed prefix of 20,480 tokens against 4,096 — and the
probe restored zero from both, in the same 48.00 s. That disagreement between
the runtime's own counter and the probe was how the study could say that
progressive publication worked as designed and was still not what was missing.

What was missing has since been found. The store's boundary-snapshot payload
holds the model's non-sliceable layers alone, 48 of its 64, so a published
block was stamped `num_layers: 48` and a restore comparing that with 64 read it
as cross-model contamination and discarded the chain it had just matched.
Publishing the live cache instead fixed it: 12,288 tokens match across 3
blocks, 64 layers reconstruct, the request attaches with 12,288 cached and
4,121 left to prefill, and time to first token falls from 67.34 s cold to
18.72 s on a completion byte-identical to the dense reference. That is one
matched comparison, and it settles restorability rather than rate. Two of the
numbers above belong to a build measured before it: the job read 24,575 of its
24,576-token target because it could not reach that target, and the 20,480
against 4,096 was measured with that defect and one other in the build. Both
were fixed on 2026-09-21, the direction of the gap survives and its size is not
established.

What had failed was `claimed canonical publication != independently restorable
canonical state`, not the architecture, and it is fixed. Five rounds after the
fix then refuted the experiment's own objective. It was built around Spec Exit —
the turn on which a session stops taking the sparse route — and the exit turn is
the most expensive turn of its session in every cell that has one, while the
fastest configuration measured never exits at all. What the mechanism is worth is
read from the tail rather than from the exit: at a 15 s idle gap it holds time to
first token flat across seven turns against a sparse control rising to 42.16 s,
for a session 26.9% shorter and no route change. The dense turns in these rounds
were paused by the runtime's prefill memory throttle where the sparse ones were
not, so how much of the gap between the two routes is the route is **not
established**. One bug fell out on the way — the prefill OOM-requeue path claims
to clear the SpecPrefill RoPE patch and does not.

The mechanism went upstream as
[omlx#3793](https://github.com/jundot/omlx/pull/3793), which depends on
[omlx#3811](https://github.com/jundot/omlx/pull/3811), a SpecPrefill × mRoPE
positional-correctness defect that validating PCSR exposed and PCSR did not
cause. Preparing #3793 for review turned up six further defects in the
mechanism itself, none of them reachable by the workloads in `data/`; they are
written up with their invariants in
[`HARDENING.md`](experiments/exp-003-progressive-shadow-prefill/HARDENING.md).
One of the six came with a measurement of its own —
[`data/recovery-foreground-qos/recovery-state-retirement.csv`](data/recovery-foreground-qos/recovery-state-retirement.csv),
which establishes what a parked recovery job retains and does **not** establish
any effect on foreground headroom.

### Research threads and candidates

Ten pages, in seven states. None of them is an experiment and none is
labelled as one.

**Open threads** — real measurement behind them, and none answers its own
question.

1. **[Correctness as a constraint](research-threads/inference-correctness.md)**
   — five optimizations, five different answers. Restoring a cached prefix
   was output-identical across seven paired cases while cutting one of them
   from 56.3 s to 2.3 s. Three attention-routing builds produced three
   different logit vectors and one identical output at 68K context. The
   protected-prefix boundary was the one that silently changed the model's
   input, and became an upstream fix. Speculative decoding, measured in
   EXP-002, both changed the output and stopped it being reproducible at all —
   and whether that matters is the measurement none of the five has. The fifth
   case is the odd one: a cache restore that succeeded and was then read as
   sitting at position 0, so the prompt was re-prefilled over state it already
   held and the token selection was computed from a key range nobody intended.

2. **[Heterogeneous compute](research-threads/heterogeneous-compute.md)** —
   a neural-engine prefill path that compiled, reported itself enabled, and
   never executed, because the serving layer's block size was smaller than
   the compiled tile. When it did run it was nearly five times faster in
   isolation and lost the session anyway.

3. **[Cross-runtime observations](research-threads/cross-runtime-observations.md)**
   — there is no controlled cross-runtime or cross-hardware comparison here,
   and the page exists to say so precisely rather than to imply one.

**A thread with findings of its own.**
[Background work under foreground QoS](research-threads/background-work-under-foreground-qos.md)
carries five measured findings that are not EXP-003's: a share-of-wall-time
budget controls how *often* background work collides and cannot bound what a
collision costs; the execution slice and the publication grain are independent
control variables; parking removed idle scheduler spin without costing
recovery throughput; and a per-engine budget does not give a process-global
bound. Two mechanism lessons from #3793's hardening were added to it and are
labelled as source-established rather than measured.

**Two threads with their mechanism established and their validation pending.**
[Hybrid draft prefix reuse in SpecPrefill](research-threads/specprefill-draft-cache-reuse.md)
— SpecPrefill's draft prefix cache never produced a hit on a hybrid recurrent
model, and the reason was two unrelated defects that each made the cache
useless on its own. The runtime read the restored cache's logical position
from layer 0, which on a hybrid model is recurrent and carries no position, so
a restored prefix was scored as empty and the whole prompt re-prefilled on top
of it ([#3840](https://github.com/jundot/omlx/pull/3840)). And draft scoring
never published a recurrent checkpoint for a later turn to restore from, so
every stored block held a placeholder and the walk-back correctly found nothing
([#3842](https://github.com/jundot/omlx/pull/3842)). Only both together produce
a reusable draft prefix: 0 cache hits in 10 scorings become 23 in 27, with
the runtime's rejection line going from 9 occurrences to none. It is a thread
and not EXP-004 because the two arms' sessions took different trajectories, so
what exists is a per-prompt comparison against a fitted baseline and not a
matched effect size. A third finding came out of it that no hit-rate number can
see: a restored cache kept alive by a leftover alias past the point that
returns its buffers is memory that was not freed, and it makes the reclaim
figure under-report what is held.

The story it sits in is worth stating as a chain, because no single step in it
is the finding:
EXP-003 closed
→ the mechanism went into a real Claude Code workload for production validation
→ the optimization did not activate the way the controlled rounds predicted
→ instrumenting the target and draft cache roles separately
→ the draft cache turned out never to have been reused at all
→ making the reuse path reachable exposed a correctness defect behind it
→ #3840 and #3842.
Neither defect is in PCSR, and neither is a seventh or eighth hardening finding
of it. They are what taking a mechanism into a workload turned up in a second
cache path, one the experiment never instrumented on its own.

[Prefix-cache instances and their state-preservation contracts](research-threads/prefix-cache-instance-consistency.md)
was a recorded candidate until that investigation gave it half an answer. What
is now established is that the target path and the SpecPrefill draft path hold
different state-preservation contracts while facing the same recurrent-layer
problem, and that the asymmetry had real correctness and reuse consequences.
What the page was opened for — two `BlockAwarePrefixCache` instances live for
one served model, disagreeing about `gdn_ssd_split_enabled` — is unchanged and
still **not established**, and the page keeps the two apart.

**A thread closed at its design gate.**
[Moving draft scoring-window origin invalidates reusable draft state](research-threads/specprefill-scoring-window-origin.md)
— with #3840 and #3842 in production, draft scoring still missed on turns whose
prompt history had not changed. The draft cache is keyed by a window that
starts at `max(target_cached, system_end)`, so any change to that maximum
re-roots the hash chain: a miss in exactly the 44 of 44 transitions where it
moved, reproduced standalone with nothing but the planner, the cache and the
draft model. The miss turned out to be correct under the current scoring
contract. Neither recurrent nor attention state from one window origin is the
state another origin calls for, so keeping the cache across the move needs
full-prompt scoring semantics. Against the dense target those scored further
from dense logits than the moving window at both moved frontiers measured.
NO-GO: no implementation, and the page is an upstream issue candidate rather
than a pull request.

**A promoted thread, kept as written.**
[What decides whether speculative decoding pays](research-threads/speculative-decoding.md)
— the question it is named after was answered by EXP-002. The page is left in
the state it was in before that, because the gap it declared is what the
experiment went and closed.

**One recorded candidate, not investigated.**
[SpecPrefill admission economics](research-threads/specprefill-admission-economics.md)
has one question and one clean dataset, and no experiment open. It is recorded
so the absence is visible rather than implied.

**One internal working document.**
[PCSR — minimal reproductions and the evidence-to-code map](research-threads/pcsr-reproduction-and-evidence-map.md)
maps every public PCSR claim to a dataset, to the production symbol that
produced it, and to a test that fails if the mechanism stops behaving. It is
not a result and introduces none.

### Tooling

[`harness/`](harness/) measures a local inference server into the schema in
[`schemas/`](schemas/), with the isolation rules this work uses. It is the
reproduction path, not a result, and nothing published depends on it.

## Cross-cutting findings

Three things recur across the study and the threads, each supported by more
than one of them.

**An isolated speedup and a session outcome can have opposite signs.** Sparse
prefill in EXP-001, and the neural-engine plus sparse configuration in the
earlier campaign, were both several times faster on a cold request and both
lost the session. [EXP-001 §6, heterogeneous-compute thread]

**Boundaries are where optimizations break.** The cache cliff is a partial
block match rejected at a restore. The protected-prefix bug was a role
boundary in a chat template. The neural-engine path did nothing because the
block boundary did not reach the compiled tile. Three unrelated failures, all
at the seam where work gets divided. [EXP-001, correctness thread,
heterogeneous-compute thread]

**What a mechanism costs is not what it accepts.** Acceptance rate,
cache hit rate and tokens per verify cycle are all properties of a request
that say nothing directly about time. In EXP-001 the cache hit rate was the
more defensible column precisely because it was not a wall-clock number. EXP-002
is the sharpest form of it: acceptance of 56% loses on one model and 79% wins
1.81x on another, and the term that separates them is what a verify cycle costs,
which nobody was reporting. [EXP-001, EXP-002]

## Open questions

The cliff in EXP-001 has a specific proximate cause — a partial prefix match
rejected at a restore to protect state correctness, before SpecPrefill, an
attention-based sparse prefill mechanism, had engaged on that request — and I
do not know how general the failure is. Whether a prefill optimization can be
made to leave a valid reusable checkpoint behind, rather than a placeholder
the cache must reject, is the question I most want answered. Half of it now has
an answer on the draft side — the placeholder is there because nobody published
the recurrent state, and publishing it at a reachable boundary removes it — and
that is the draft prefix cache on a different code path from EXP-001's cliff,
which no measurement here reaches.

Repayment is the other one. Across the twenty restores I observed, no dense
request ever followed a sparse one, because the suffix never dropped back
below the threshold, so repayment was never attempted. The suffix series says
the bill would have been larger than the sparse saving. That is an arithmetic
reading of one trace in one regime, not an experiment I ran.
The conditions under which it flips — different suffix growth rates, different
checkpoint granularity, cheaper dense recovery — are unexplored.

Each research thread names the specific thing missing that would promote it,
and those are the sharpest open questions here because the surrounding
evidence already exists. In short: a correctness sweep across context length
against a fixed reference and a decided threshold; the neural-engine path
measured at a matched tile and block size with nothing else on; and a second
runtime driven by the same client. The speculative-decoding thread's missing
matched arm is the one that got run, and EXP-002 is what came of it — along with
its own successor question, which is the same one EXP-001 ended on: every run in
it is a single request against a cold cache, and a mechanism that charges at
prefill and pays at decode is exactly the kind whose sign can flip in a session.

Beyond those: how scheduling policy should treat reusable state as a resource
rather than a side effect; what long-context correctness costs when
prompt-protection boundaries are computed rather than declared; and how any
of this transfers off one vendor's unified-memory hardware, which I cannot
answer from here.

None of these has a date or an identifier. They are what I would look at
next, listed so the framing of the work above makes sense, and nothing in
this repository should be read as a commitment to run them. Where a gap could
have been closed by running something new, it was deliberately left open
rather than filled in after the fact.
