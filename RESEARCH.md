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

Two completed studies, three research threads, and a set of questions that would
need work nobody has done yet. The categories are kept separate on purpose,
because the difference between them is the difference between a finding and an
anecdote.

### Completed studies

**[EXP-001 — Reusable state economics](experiments/exp-001-reusable-state-economics/)**.
The cost of an inference optimization includes the reusable state it creates,
or fails to create, for later requests. Sparse prefill cut cold 16K
time-to-first-token from 57.84 s to 19.24 s and made a continuation-heavy
agent session slower, because a sparsified suffix does not advance the
reusable dense prefix state and the checkpoint stops recovering. Four
hypotheses were refuted, including two I expected to hold. A correctness bug
found while instrumenting it went upstream. Section 6 of its findings records
an earlier campaign, on an older build and different sessions, that shows
the same signature and was run before I knew it was a finding.

**[EXP-002 — Speculative decoding economics](experiments/exp-002-speculative-decoding-economics/)**.
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

### Research threads

Each has real measurement behind it and none answers its own question. They
are not experiments and are not labelled as such.

1. **[Correctness as a constraint](research-threads/inference-correctness.md)**
   — four optimizations, four different answers. Restoring a cached prefix
   was output-identical across seven paired cases while cutting one of them
   from 56.3 s to 2.3 s. Three attention-routing builds produced three
   different logit vectors and one identical output at 68K context. The
   protected-prefix boundary was the one that silently changed the model's
   input, and became an upstream fix. Speculative decoding, measured in
   EXP-002, both changed the output and stopped it being reproducible at all —
   and whether that matters is the measurement none of the four has.

2. **[Heterogeneous compute](research-threads/heterogeneous-compute.md)** —
   a neural-engine prefill path that compiled, reported itself enabled, and
   never executed, because the serving layer's block size was smaller than
   the compiled tile. When it did run it was nearly five times faster in
   isolation and lost the session anyway.

3. **[Cross-runtime observations](research-threads/cross-runtime-observations.md)**
   — there is no controlled cross-runtime or cross-hardware comparison here,
   and the page exists to say so precisely rather than to imply one.

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
the cache must reject, is the question I most want answered.

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
