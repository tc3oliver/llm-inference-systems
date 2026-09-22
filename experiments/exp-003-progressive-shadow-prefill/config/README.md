# EXP-003 configs — and why the setting names here are not the current ones

Every file in this directory sets `shadow_prefill_enabled`,
`shadow_prefill_publish_mode` and `shadow_prefill_budget_pct`, and one of them
names an arm "Shadow-End". Those are the names the research build used. They
are **not** the names of the mechanism.

The mechanism is Progressive Canonical State Recovery. Upstream in
[omlx#3793](https://github.com/jundot/omlx/pull/3793) the settings are
`canonical_state_recovery_enabled`,
`canonical_state_recovery_global_budget_pct` and
`canonical_state_recovery_slice_tokens`, the budget class is
`CanonicalRecoveryBudget`, and no symbol in the proposed diff contains
"shadow".

These files are not edited to match, for the same reason the directory keeps
the slug `exp-003-progressive-shadow-prefill`: **a config records what was
run.** Rewriting the keys would make each file describe a server it was never
pointed at, and the arms in `data/exp-003/` would then reference settings that
never existed on the build that produced them. Reader-facing prose is free to
change; what a run was given is not.

Two further cautions that belong with the files rather than with the tables.

`01-four-arms.yaml` carries its own provenance note, and it is worth reading
before the others: it did not produce any table in `data/exp-003/`. The rounds
that did were driven by one-off scripts that took turn size, turn count, idle
gap and budget as arguments. `data/exp-003/README.md` records what each table
actually is.

The publish modes map straight across: `terminal` is the Recovery-End arm,
publishing only when a whole target completes, and `progressive` publishes at
every safe boundary. That comparison is the one the design turns on, and it is
the only place the two modes differ.
