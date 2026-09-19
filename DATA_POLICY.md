# Data policy

This work was done on a personal machine running a local inference server
against real coding-agent traffic. That traffic carries material I have no
right to republish and material that cannot be un-republished once it is in a
public git history. The rules below are what I apply before anything lands in
a commit.

## What is published

Sanitized traces. The request-level CSVs under `data/exp-001/` were extracted
from a session transcript and reduced to the columns the analysis needs —
sequence, clock, token counts, phase label. No prompt text, no completion
text, no tool arguments.

Derived tables. Cold prefill timings, think-time sweeps and per-turn cache hit
rates, as CSV, at the precision the source reported.

Chart source data. Every figure reads from a file in `data/`. If a number
appears in a figure and not in `data/`, that is a bug in the figure.

Generated corpora. Any benchmark input published here is synthesized to the
right size and shape rather than captured from real traffic. A prompt of a
given token count and a given tool-block structure exercises the code path;
the original wording adds nothing and cannot be licensed.

Log lines only when a single line carries the mechanism and contains nothing
else. One line in this repository qualifies — the cache-rejection line first
quoted in `data/exp-001/README.md`, repeated where the argument needs it in
the experiment findings and in the article.

## What is never published

Vendor system prompts and tool definitions, verbatim or paraphrased closely
enough to reconstruct. Unlike a credential, this cannot be rotated. Removing
it means rewriting every commit that carries it, and a file that was edited
still keeps its old blob in history under an innocent path.

Model output captured from a third-party product.

Raw production logs. They contain prompt fragments, timing correlations and
occasionally request bodies. Every number here came out of a log; no log came
out with it.

Absolute machine paths, hostnames, LAN addresses, internal release or build
names, and anything that identifies where this runs. The platform is described
as a class of machine, which is all a reader needs.

Credentials of any kind, including ones that are expired or were never live.

## On generated corpora

A benchmark corpus is a shape: a token count, a distribution of block sizes,
a ratio of system prompt to tools to conversation. Generate it. Embedding a
captured prompt as a constant in the generator script is the same leak twice,
and it regenerates the file you just deleted.

## If something leaks anyway

Anything that reached a public commit is compromised from the moment it is
pushed, regardless of what happens next. The response is to rewrite history
and say so here, not to delete the file in a follow-up commit and move on.
