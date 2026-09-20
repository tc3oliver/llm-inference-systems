# Research thread — cross-runtime observations

**Status: no controlled comparison exists, and none was run.** This page
exists to say precisely that, and to record what would be needed, because the
absence is easy to paper over and the papering-over is the thing that would
make the rest of this repository less trustworthy.

## The question

Which of the behaviours in EXP-001 and the other threads are properties of
inference systems in general, and which are properties of one runtime's
implementation on one vendor's hardware?

It matters for reading everything else here. The cache cliff is described as
a consequence of a prefix cache refusing a partial match — which is correct
behaviour that any cache implementing the same safety rule would exhibit —
but the only evidence is one runtime. If the cliff is really an artifact of
this implementation's block granularity, the finding is a bug report. If it
is not, it is a design constraint. Nothing here distinguishes the two.

## What exists

Everything measured in this repository comes from one machine, one vendor's
unified-memory hardware, one serving runtime and one model family. That is
the whole cross-runtime evidence base: a single point.

There is a second inference runtime installed on the same machine, sharing
the same underlying compute library but with its own serving layer, its own
prompt cache and its own speculative decoding mechanism. Comparing the two
would isolate the serving layer from the compute, which is the layer every
claim in this repository is actually about. It has not been done, and doing
it was declined rather than deferred by accident.

## What does not exist, and why

**No AMD, ROCm or datacentre-GPU measurement appears in this repository, and
none will be added from existing material.** There is no such environment
reachable from this machine. Material I have seen on those platforms is
employer-internal: it cannot be published, cannot be re-derived here, and
would not be a controlled comparison with anything here even if it could,
because the model, context lengths, workload, runtime and instrumentation all
differ. Publishing an abstracted version would produce a claim that reads
like a comparison and is not one.

So the honest statement is the short one: I have worked on inference systems
on other hardware, and none of that work is evidence in this repository.

## What a real comparison would need

Naming this precisely is more useful than an approximation of it:

1. The same workload shapes, driven by the same client, against both
   runtimes, with the prompt geometry fixed rather than the prompt text.
2. Instrumentation that reports the same quantities on both sides. The
   load-bearing column in EXP-001 is the per-request uncached suffix, and a
   runtime that does not expose it cannot participate.
3. Comparison of relative behaviour, never raw throughput: does the cache hit
   rate decline over a continuation-heavy session on both, at what context
   length does each stop advancing its reusable state, and does the ratio
   between isolated speedup and session outcome have the same sign.
4. A stated model-parity argument. Two runtimes running two different models
   measure the models, not the runtimes.

Until that exists, the correct reading of this repository is that its
findings are established for one runtime on one machine, with a mechanism
described well enough that someone on a different stack could check whether
it reproduces. That is what
[`docs/reproducibility.md`](../docs/reproducibility.md) is for.
