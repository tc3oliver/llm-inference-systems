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
behind it, from microbenchmark up to a change accepted outside this machine.
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

One experiment: EXP-001, reusable state economics in interactive inference.
That is the whole program to date. Everything below is a question, not a
plan.

## Open questions

The cliff in EXP-001 has a specific proximate cause — a partial prefix match
rejected to protect state correctness — and I do not know how general the
failure is. Whether a prefill optimization can be made to leave a valid
reusable checkpoint behind, rather than a placeholder the cache must reject,
is the question I most want answered.

Repayment is the other one. Across the twenty restores I observed, no
following dense request ever repaid the debt, because the saving was smaller
than the recomputation recovery needed. That is a reading of one trace in one
regime, not an experiment I ran.
The conditions under which it flips — different suffix growth rates, different
checkpoint granularity, cheaper dense recovery — are unexplored.

Beyond those: how scheduling policy should treat reusable state as a resource
rather than a side effect; whether the same accounting applies to speculative
decoding, where the unit of waste is a rejected draft rather than a rejected
checkpoint; what long-context correctness costs when prompt-protection
boundaries are computed rather than declared; and how any of this transfers
off one vendor's unified-memory hardware, which I cannot answer from here.

None of these has a date, a directory, or an identifier. They are what I would
look at next, listed so the framing of EXP-001 makes sense, and nothing in
this repository should be read as a commitment to run them.
