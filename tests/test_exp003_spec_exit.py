"""Tests for the EXP-003 / PCSR Spec Exit derivations.

Nothing here starts a server or reads a run of the real experiment. Every case
is a hand-built record chosen to pin one definition down, including the ones
where the honest answer is "no value".
"""

from __future__ import annotations

import csv
import json
import pathlib

import pytest

from analysis import exp003


def _turn(turn: int, prompt: int, cached: int, shadow: dict | None = None,
          kind: str = "session", **recorded) -> dict:
    """One session turn of the runner JSON.

    `recorded` adds the flattened admission keys the runner writes: `route`,
    `route_tail_tokens`, `route_cached_tokens`, `route_threshold_tokens`.
    """
    return {
        "kind": kind,
        "turn": turn,
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "uncached_suffix": prompt - cached,
        "ttft_s": 1.0 + turn,
        "prefill_s": None,
        "decode_s": 0.25,
        "decode_tps": None,
        "output_tokens": 24,
        "wall_s": 2.0 + turn,
        "output_sha": f"sha{turn}",
        "shadow": shadow or {},
        **recorded,
    }


def _admitted(route: str, tail: int, cached: int, threshold: int = 8192) -> dict:
    """The admission keys for a turn the server instrumented."""
    return {"route": route, "route_tail_tokens": tail,
            "route_cached_tokens": cached, "route_threshold_tokens": threshold}


# ------------------------------------------------------------- definitions


def test_canonical_debt_is_the_gap_between_prompt_and_prefix():
    assert exp003.canonical_debt(48000, 44000) == 4000
    assert exp003.canonical_debt(16000, 16000) == 0


def test_canonical_debt_is_none_when_either_side_is_missing():
    assert exp003.canonical_debt(None, 44000) is None
    assert exp003.canonical_debt(48000, None) is None


def test_uncached_tail_is_what_the_request_had_to_compute():
    assert exp003.uncached_tail(32000, 16000) == 16000
    assert exp003.uncached_tail(32000, 32000) == 0
    assert exp003.uncached_tail(32000, None) is None


def test_catch_up_ratio_divides_canonical_gain_by_context_gain():
    assert exp003.catch_up_ratio(28000, 16000) == 1.75
    assert exp003.catch_up_ratio(0, 16000) == 0.0


def test_catch_up_ratio_over_a_zero_length_interval_is_undefined():
    assert exp003.catch_up_ratio(4000, 0) is None
    assert exp003.catch_up_ratio(0, 0) is None
    assert exp003.catch_up_ratio(None, 16000) is None


# ------------------------------------------------------------------ routes


def test_route_follows_the_tail_against_the_break_even():
    assert exp003.classify_route(0, 8192) == exp003.ROUTE_CACHE_HIT
    assert exp003.classify_route(8192, 8192) == exp003.ROUTE_DENSE
    assert exp003.classify_route(8193, 8192) == exp003.ROUTE_SPECPREFILL


def test_a_zero_tail_is_a_cache_hit_without_a_break_even():
    assert exp003.classify_route(0, None) == exp003.ROUTE_CACHE_HIT


def test_route_is_none_without_a_break_even_or_a_tail():
    assert exp003.classify_route(4000, None) is None
    assert exp003.classify_route(None, 8192) is None


# -------------------------------------------------------------- spec exit


def test_spec_exit_is_the_first_turn_after_the_last_sparse_one():
    routes = [exp003.ROUTE_SPECPREFILL, exp003.ROUTE_SPECPREFILL,
              exp003.ROUTE_DENSE, exp003.ROUTE_CACHE_HIT]
    assert exp003.spec_exit_turn(routes) == 2


def test_spec_exit_uses_the_records_own_turn_numbers():
    routes = [exp003.ROUTE_SPECPREFILL, exp003.ROUTE_DENSE]
    assert exp003.spec_exit_turn(routes, [7, 8]) == 8


def test_a_session_that_never_leaves_specprefill_has_no_exit():
    routes = [exp003.ROUTE_DENSE, exp003.ROUTE_SPECPREFILL]
    assert exp003.spec_exit_turn(routes) is None


def test_a_session_that_never_enters_specprefill_exits_at_its_first_turn():
    assert exp003.spec_exit_turn([exp003.ROUTE_DENSE, exp003.ROUTE_CACHE_HIT]) == 0


def test_an_undecided_route_empties_the_exit_rather_than_reporting_zero():
    routes = [exp003.ROUTE_DENSE, None, exp003.ROUTE_DENSE]
    assert exp003.spec_exit_turn(routes) is None


def test_no_turns_means_no_exit():
    assert exp003.spec_exit_turn([]) is None


# ------------------------------------------------------- prefix resolution


def test_the_recovery_figure_prefers_the_turns_own_field():
    row = {"prompt_tokens": 100, "canonical_prefix_tokens": 40,
           "shadow": {"committed_tokens": 10}}
    assert exp003.recovery_prefix_tokens(row) == 40


def test_the_recovery_figure_falls_back_to_the_shadow_counters_in_order():
    published = {"prompt_tokens": 100,
                 "shadow": {"longest_canonical_prefix_tokens": 60,
                            "committed_tokens": 10}}
    committed = {"prompt_tokens": 100, "shadow": {"committed_tokens": 10}}
    assert exp003.recovery_prefix_tokens(published) == 60
    assert exp003.recovery_prefix_tokens(committed) == 10


def test_a_reported_debt_fixes_the_recovery_figure_by_the_same_identity():
    row = {"prompt_tokens": 100, "shadow": {"canonical_debt_tokens": 25}}
    assert exp003.recovery_prefix_tokens(row) == 75


def test_the_restored_figure_is_the_admissions_cached_tokens():
    assert exp003.restored_prefix_tokens({"route_cached_tokens": 24576}) == 24576
    assert exp003.restored_prefix_tokens(
        {"route": {"cached_tokens": 24576}}) == 24576
    assert exp003.restored_prefix_tokens({}) is None


def test_canonical_prefix_takes_the_larger_of_the_two_lower_bounds():
    frozen = {"prompt_tokens": 40000, "shadow": {"committed_tokens": 20480},
              "route_cached_tokens": 28672}
    assert exp003.canonical_prefix_tokens(frozen) == (28672, "restore")

    ahead = {"prompt_tokens": 40000, "shadow": {"committed_tokens": 32768},
             "route_cached_tokens": 28672}
    assert exp003.canonical_prefix_tokens(ahead) == (32768, "recovery")


def test_the_two_bounds_agreeing_is_its_own_source():
    row = {"prompt_tokens": 40000, "shadow": {"committed_tokens": 20480},
           "route_cached_tokens": 20480}
    assert exp003.canonical_prefix_tokens(row) == (20480, "equal")


def test_one_bound_alone_names_itself():
    recovery = {"prompt_tokens": 100, "shadow": {"committed_tokens": 10}}
    restore = {"prompt_tokens": 100, "shadow": {}, "route_cached_tokens": 10}
    assert exp003.canonical_prefix_tokens(recovery) == (10, "recovery")
    assert exp003.canonical_prefix_tokens(restore) == (10, "restore")


def test_a_restored_zero_is_a_bound_and_not_an_absence():
    row = {"prompt_tokens": 100, "shadow": {}, "route_cached_tokens": 0}
    assert exp003.canonical_prefix_tokens(row) == (0, "restore")


def test_canonical_prefix_is_none_when_nothing_reports_it():
    assert exp003.canonical_prefix_tokens(
        {"prompt_tokens": 100, "shadow": {}}) == (None, None)


# ------------------------------------------------------------ turn series


def _appending_arm() -> dict:
    return {
        "cumulative_foreground_s": 30.0,
        "actual_service_share": 0.048,
        "turns": [
            _turn(0, 16000, 0, {"committed_tokens": 0}),
            _turn(1, 32000, 16000, {"committed_tokens": 16000}),
            _turn(2, 48000, 44000, {"committed_tokens": 44000}),
            _turn(3, 48000, 40000, {"service_s": 12.0, "publishes": 5},
                  kind="dense-probe"),
        ],
    }


def test_derive_turns_reports_the_interval_that_starts_at_each_turn():
    rows = exp003.session_turns(_appending_arm())
    derived = exp003.derive_turns(rows, dense_break_even=8192)

    assert [d["canonical_prefix_tokens"] for d in derived] == [0, 16000, 44000]
    assert [d["canonical_debt_tokens"] for d in derived] == [16000, 16000, 4000]
    assert [d["uncached_tail_tokens"] for d in derived] == [16000, 16000, 4000]
    # The last turn has no interval after it, and says so rather than zero.
    assert [d["debt_delta_tokens"] for d in derived] == [0, -12000, None]
    assert [d["catch_up_ratio"] for d in derived] == [1.0, 1.75, None]
    assert [d["route"] for d in derived] == [
        exp003.ROUTE_SPECPREFILL, exp003.ROUTE_SPECPREFILL, exp003.ROUTE_DENSE,
    ]


def test_debt_delta_and_catch_up_ratio_are_one_identity():
    rows = exp003.session_turns(_appending_arm())
    derived = exp003.derive_turns(rows, dense_break_even=8192)
    prompts = [row["prompt_tokens"] for row in rows]
    for position, entry in enumerate(derived[:-1]):
        required = prompts[position + 1] - prompts[position]
        assert entry["debt_delta_tokens"] == required * (1 - entry["catch_up_ratio"])


def test_a_turn_series_without_canonical_state_derives_nothing_from_it():
    payload = {"turns": [_turn(0, 16000, 0), _turn(1, 32000, 0)]}
    derived = exp003.derive_turns(exp003.session_turns(payload), 8192)
    assert [d["canonical_prefix_tokens"] for d in derived] == [None, None]
    assert [d["canonical_debt_tokens"] for d in derived] == [None, None]
    assert [d["debt_delta_tokens"] for d in derived] == [None, None]
    assert [d["catch_up_ratio"] for d in derived] == [None, None]
    # The tail is still measured, so the route still is.
    assert [d["route"] for d in derived] == [exp003.ROUTE_SPECPREFILL] * 2


# ------------------------------------------------------------ break-even


def test_the_run_record_supplies_the_break_even_before_the_argument():
    data = {"meta": {"dense_break_even_tokens": 8192}}
    assert exp003.dense_break_even_tokens(data, {}, override=4096) == 8192


def test_an_arm_supplies_its_own_break_even_before_meta():
    data = {"meta": {"dense_break_even_tokens": 8192}}
    arm = {"dense_break_even_tokens": 2048}
    assert exp003.dense_break_even_tokens(data, arm, override=4096) == 2048


def test_the_argument_supplies_it_for_a_record_that_does_not():
    assert exp003.dense_break_even_tokens({}, {}, override=8192) == 8192


def test_no_break_even_anywhere_is_none_rather_than_a_default():
    assert exp003.dense_break_even_tokens({}, {}, override=None) is None


def test_without_a_break_even_the_route_and_the_exit_are_empty():
    summary = exp003.arm_summary(_appending_arm(), dense_break_even=None)
    assert summary["spec_exit_turn"] is None
    assert summary["dense_break_even_tokens"] is None


# ----------------------------------------------------------- arm summary


def test_arm_summary_excludes_the_probe_and_carries_the_spec_exit():
    summary = exp003.arm_summary(_appending_arm(), dense_break_even=8192)
    assert summary["turns"] == 3
    assert summary["spec_exit_turn"] == 2
    assert summary["final_canonical_prefix_tokens"] == 44000
    assert summary["final_canonical_debt_tokens"] == 4000
    # Pooled over the session: 44,000 tokens of prefix against 32,000 of
    # prompt, not the mean of the two per-turn ratios.
    assert summary["session_catch_up_ratio"] == 44000 / 32000
    assert summary["cumulative_foreground_s"] == 30.0
    assert summary["measured_recovery_share"] == 0.048
    assert summary["dense_break_even_tokens"] == 8192


def test_recovery_share_falls_back_to_the_last_cumulative_counter():
    payload = {
        "turns": [
            _turn(0, 16000, 0, {"service_share": 0.09}),
            _turn(1, 32000, 16000, {"service_share": 0.05}),
            _turn(2, 32000, 32000, {"service_share": 0.04}, kind="dense-probe"),
        ],
    }
    assert exp003.measured_recovery_share(payload) == 0.04


def test_recovery_share_is_none_when_no_shadow_reported_one():
    payload = {"turns": [_turn(0, 16000, 0)]}
    assert exp003.measured_recovery_share(payload) is None


# ----------------------------------------------------------------- tables


def _run_main(tmp_path, monkeypatch, data, extra_argv=()):
    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps(data))
    monkeypatch.setattr(exp003, "OUT_DIR", tmp_path / "out")
    code = exp003.main(["exp003", str(runs), *extra_argv])
    assert code == 0
    turns = list(csv.DictReader((tmp_path / "out" / "session-turns.csv").open()))
    summary = list(csv.DictReader((tmp_path / "out" / "arm-summary.csv").open()))
    return turns, summary


def test_main_writes_the_derived_columns_beside_the_existing_ones(tmp_path,
                                                                  monkeypatch):
    data = {"meta": {"dense_break_even_tokens": 8192}, "pass": _appending_arm()}
    turns, summary = _run_main(tmp_path, monkeypatch, data)

    assert [row["turn"] for row in turns] == ["0", "1", "2", "3"]
    # Existing columns still carry what they carried.
    assert turns[1]["prompt_tokens"] == "32000"
    assert turns[1]["uncached_suffix"] == "16000"
    assert turns[1]["shadow_committed_tokens"] == "16000"
    # New ones carry the derivation, including a measured zero.
    assert turns[0]["debt_delta_tokens"] == "0"
    assert turns[0]["route"] == exp003.ROUTE_SPECPREFILL
    assert turns[2]["route"] == exp003.ROUTE_DENSE
    assert turns[2]["catch_up_ratio"] == ""

    assert summary[0]["spec_exit_turn"] == "2"
    assert summary[0]["turns"] == "3"
    assert summary[0]["final_canonical_debt_tokens"] == "4000"
    assert summary[0]["dense_break_even_tokens"] == "8192"
    assert summary[0]["cumulative_foreground_s"] == "30.0"


def test_the_probe_row_takes_no_derived_columns(tmp_path, monkeypatch):
    data = {"meta": {"dense_break_even_tokens": 8192}, "pass": _appending_arm()}
    turns, _ = _run_main(tmp_path, monkeypatch, data)
    probe = turns[3]
    assert probe["kind"] == "dense-probe"
    for field in exp003.DERIVED_TURN_FIELDS:
        assert probe[field] == ""


def test_the_flag_supplies_a_break_even_the_record_lacks(tmp_path, monkeypatch):
    data = {"pass": _appending_arm()}
    _, summary = _run_main(tmp_path, monkeypatch, data,
                           extra_argv=["--dense-break-even", "8192"])
    assert summary[0]["spec_exit_turn"] == "2"
    assert summary[0]["dense_break_even_tokens"] == "8192"


def test_without_a_break_even_the_tables_leave_the_route_empty(tmp_path,
                                                               monkeypatch):
    data = {"pass": _appending_arm()}
    turns, summary = _run_main(tmp_path, monkeypatch, data)
    assert [row["route"] for row in turns] == ["", "", "", ""]
    assert summary[0]["spec_exit_turn"] == ""
    # The rest of the derivation does not depend on it and is still written.
    assert turns[2]["canonical_debt_tokens"] == "4000"


def test_the_round_prefix_argument_still_names_the_tables(tmp_path, monkeypatch):
    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps({"pass": _appending_arm()}))
    monkeypatch.setattr(exp003, "OUT_DIR", tmp_path / "out")
    assert exp003.main(["exp003", str(runs), "compact"]) == 0
    assert (tmp_path / "out" / "compact-turns.csv").exists()
    assert (tmp_path / "out" / "compact-summary.csv").exists()


def test_no_arguments_is_a_usage_error(tmp_path, monkeypatch):
    monkeypatch.setattr(exp003, "OUT_DIR", tmp_path / "out")
    assert exp003.main(["exp003"]) == 2


# ------------------------------------------------- the recorded admission


def test_a_recorded_route_is_normalised_to_this_modules_names():
    assert exp003.recorded_route({"route": "cache_hit"}) == exp003.ROUTE_CACHE_HIT
    assert exp003.recorded_route({"route": "dense"}) == exp003.ROUTE_DENSE
    assert exp003.recorded_route({"route": "specprefill"}) == exp003.ROUTE_SPECPREFILL


def test_a_recorded_route_is_read_from_the_nested_block_too():
    row = {"route": {"route": "dense", "threshold_tokens": 4096,
                     "cached_tokens": 100, "tail_tokens": 20}}
    assert exp003.recorded_route(row) == exp003.ROUTE_DENSE
    assert exp003.recorded_threshold_tokens(row) == 4096
    assert exp003._recorded(row, "tail_tokens") == 20


def test_a_route_this_module_does_not_know_is_written_through():
    assert exp003.recorded_route({"route": "hybrid"}) == "HYBRID"


def test_an_unknown_recorded_route_leaves_the_exit_undecided():
    assert exp003.spec_exit_turn([exp003.ROUTE_DENSE, "HYBRID"]) is None


def test_no_recorded_route_is_none_rather_than_a_guess():
    assert exp003.recorded_route({}) is None
    assert exp003.recorded_route({"route": None}) is None
    assert exp003.recorded_threshold_tokens({}) is None


# ------------------------------------------------- recorded wins, visibly


def _instrumented_arm() -> dict:
    """The same session, with the server's admission record on every turn.

    Turn 2 is the disagreement: the admission restored 44,000 tokens and ran
    dense, while the usage object reports the 0 a SpecPrefill turn reports.
    """
    return {
        "cumulative_foreground_s": 30.0,
        "actual_service_share": 0.048,
        "turns": [
            _turn(0, 16000, 0, {"committed_tokens": 0},
                  **_admitted("specprefill", 16000, 0)),
            _turn(1, 32000, 0, {"committed_tokens": 16000},
                  **_admitted("specprefill", 16000, 16000)),
            _turn(2, 48000, 0, {"committed_tokens": 44000},
                  **_admitted("dense", 4000, 44000)),
            _turn(3, 48000, 40000, {"service_s": 12.0, "publishes": 5},
                  kind="dense-probe"),
        ],
    }


def test_the_recorded_route_wins_and_the_source_says_so():
    derived = exp003.derive_turns(exp003.session_turns(_instrumented_arm()))
    assert [d["route"] for d in derived] == [
        exp003.ROUTE_SPECPREFILL, exp003.ROUTE_SPECPREFILL, exp003.ROUTE_DENSE,
    ]
    assert [d["route_source"] for d in derived] == ["recorded"] * 3


def test_the_derivation_is_used_only_where_nothing_was_recorded():
    derived = exp003.derive_turns(exp003.session_turns(_appending_arm()), 8192)
    assert [d["route_source"] for d in derived] == ["derived"] * 3


def test_the_route_source_is_empty_when_neither_decided():
    derived = exp003.derive_turns([_turn(0, 16000, 4000)], dense_break_even=None)
    assert derived[0]["route"] is None
    assert derived[0]["route_source"] is None
    assert derived[0]["route_disagrees"] is None


def test_a_recorded_route_the_derivation_contradicts_is_flagged_not_hidden():
    # The admission ran dense on a 4,000-token tail; a derivation reading the
    # usage cached_tokens of 0 would have called the same turn SpecPrefill.
    row = _turn(2, 48000, 0, **_admitted("dense", 4000, 44000))
    row["route_cached_tokens"] = None       # force the usage count to be used
    derived = exp003.derive_turns([row])
    assert derived[0]["route"] == exp003.ROUTE_DENSE
    assert derived[0]["route_source"] == "recorded"
    assert derived[0]["route_disagrees"] is True
    assert derived[0]["uncached_tail_tokens"] == 48000


def test_agreement_is_recorded_as_a_comparison_that_was_made():
    derived = exp003.derive_turns(exp003.session_turns(_instrumented_arm()))
    assert [d["route_disagrees"] for d in derived] == [False, False, False]


def test_disagreements_are_counted_in_the_arm_summary():
    row = _turn(0, 48000, 0, **_admitted("dense", 4000, 44000))
    row["route_cached_tokens"] = None
    assert exp003.arm_summary({"turns": [row]})["route_disagreements"] == 1


def test_no_comparison_leaves_the_disagreement_count_empty():
    summary = exp003.arm_summary(_appending_arm(), dense_break_even=8192)
    assert summary["route_disagreements"] is None


# ------------------------------------------------ the tail's cache count


def test_the_admissions_cache_count_beats_the_usage_objects():
    row = _turn(0, 48000, 0, **_admitted("dense", 4000, 44000))
    assert exp003.cached_tokens_for_tail(row) == (44000, "recorded")
    derived = exp003.derive_turns([row])
    assert derived[0]["uncached_tail_tokens"] == 4000
    assert derived[0]["cached_tokens_source"] == "recorded"
    # Both counts stay on the row.
    assert derived[0]["route_cached_tokens"] == 44000
    assert row["cached_tokens"] == 0


def test_the_usage_count_is_used_when_nothing_was_recorded():
    row = _turn(0, 32000, 16000)
    assert exp003.cached_tokens_for_tail(row) == (16000, "usage")
    derived = exp003.derive_turns([row], 8192)
    assert derived[0]["uncached_tail_tokens"] == 16000
    assert derived[0]["cached_tokens_source"] == "usage"


def test_no_cache_count_anywhere_leaves_the_tail_empty():
    row = {"prompt_tokens": 32000, "cached_tokens": None, "shadow": {}}
    assert exp003.cached_tokens_for_tail(row) == (None, None)
    derived = exp003.derive_turns([row], 8192)
    assert derived[0]["uncached_tail_tokens"] is None
    assert derived[0]["cached_tokens_source"] is None


# --------------------------------------------- the threshold in force


def test_the_recorded_threshold_beats_the_record_and_the_argument():
    data = {"meta": {"dense_break_even_tokens": 99999}}
    arm = {"dense_break_even_tokens": 77777,
           "turns": [_turn(0, 16000, 0, **_admitted("dense", 4000, 12000, 8192))]}
    assert exp003.dense_break_even_tokens(data, arm, override=55555) == 8192


def test_turns_admitted_under_thresholds_that_differ_name_no_break_even():
    arm = {"turns": [
        _turn(0, 16000, 0, **_admitted("dense", 4000, 12000, 8192)),
        _turn(1, 32000, 0, **_admitted("dense", 4000, 28000, 4096)),
    ]}
    assert exp003.dense_break_even_tokens({}, arm, override=55555) is None


def test_each_turn_is_still_classified_against_its_own_threshold():
    rows = [
        _turn(0, 16000, 10000, route_threshold_tokens=8192),
        _turn(1, 32000, 26000, route_threshold_tokens=4096),
    ]
    derived = exp003.derive_turns(rows, dense_break_even=None)
    # Both tails are 6,000: under 8192 it is dense, over 4096 it is sparse.
    assert [d["uncached_tail_tokens"] for d in derived] == [6000, 6000]
    assert [d["route"] for d in derived] == [
        exp003.ROUTE_DENSE, exp003.ROUTE_SPECPREFILL,
    ]
    assert [d["route_source"] for d in derived] == ["derived", "derived"]


def test_the_spec_exit_follows_the_recorded_routes(tmp_path, monkeypatch):
    data = {"pass": _instrumented_arm()}
    turns, summary = _run_main(tmp_path, monkeypatch, data)
    assert [row["route"] for row in turns[:3]] == [
        exp003.ROUTE_SPECPREFILL, exp003.ROUTE_SPECPREFILL, exp003.ROUTE_DENSE,
    ]
    assert [row["route_source"] for row in turns[:3]] == ["recorded"] * 3
    assert [row["cached_tokens_source"] for row in turns[:3]] == ["recorded"] * 3
    # No break-even was configured anywhere; the admission supplied it.
    assert summary[0]["spec_exit_turn"] == "2"
    assert summary[0]["dense_break_even_tokens"] == "8192"
    assert summary[0]["route_disagreements"] == "0"
    # The usage column still carries what the usage object said.
    assert [row["cached_tokens"] for row in turns[:3]] == ["0", "0", "0"]
    assert [row["route_cached_tokens"] for row in turns[:3]] == ["0", "16000", "44000"]


# ------------------------------- the prefix the foreground itself advanced


def _frozen_counter_arm() -> dict:
    """A session where the recovery job stops and the restore keeps growing.

    This is the shape of the run that found the bug: the job's committed
    counter sits at 20,480 while the serving path restores more and more,
    because once the foreground goes dense it stores its own boundary.
    """
    return {
        "cumulative_foreground_s": 40.0,
        "turns": [
            _turn(0, 24576, 0, {"committed_tokens": 20480},
                  **_admitted("specprefill", 24576, 0)),
            _turn(1, 28672, 0, {"committed_tokens": 20480},
                  **_admitted("dense", 4096, 24576)),
            _turn(2, 32768, 0, {"committed_tokens": 20480},
                  **_admitted("dense", 4096, 28672)),
            _turn(3, 36864, 0, {"committed_tokens": 20480},
                  **_admitted("dense", 4096, 32768)),
        ],
    }


def test_the_frozen_counter_no_longer_decides_the_prefix():
    derived = exp003.derive_turns(exp003.session_turns(_frozen_counter_arm()))
    assert [d["canonical_prefix_tokens"] for d in derived] == [
        20480, 24576, 28672, 32768,
    ]
    assert [d["canonical_prefix_source"] for d in derived] == [
        "recovery", "restore", "restore", "restore",
    ]


def test_the_corrected_prefix_carries_into_the_debt_and_the_deltas():
    derived = exp003.derive_turns(exp003.session_turns(_frozen_counter_arm()))
    # prompt - prefix, with the prefix now following the restore.
    assert [d["canonical_debt_tokens"] for d in derived] == [
        4096, 4096, 4096, 4096,
    ]
    # A debt that holds steady while the prompt grows is a ratio of exactly 1;
    # the frozen counter would have reported 0 for the same three intervals.
    assert [d["debt_delta_tokens"] for d in derived] == [0, 0, 0, None]
    assert [d["catch_up_ratio"] for d in derived] == [1.0, 1.0, 1.0, None]


def test_the_frozen_counter_stays_visible_in_its_own_column(tmp_path,
                                                            monkeypatch):
    turns, summary = _run_main(tmp_path, monkeypatch,
                               {"pass": _frozen_counter_arm()})
    assert [row["shadow_committed_tokens"] for row in turns] == ["20480"] * 4
    assert [row["canonical_prefix_tokens"] for row in turns] == [
        "20480", "24576", "28672", "32768",
    ]
    assert [row["canonical_prefix_source"] for row in turns] == [
        "recovery", "restore", "restore", "restore",
    ]
    assert summary[0]["final_canonical_prefix_tokens"] == "32768"
    assert summary[0]["final_canonical_debt_tokens"] == "4096"
    assert summary[0]["final_canonical_prefix_source"] == "restore"
    assert summary[0]["spec_exit_turn"] == "1"


# ------------------------------------------------- the pooled catch-up


def test_the_session_ratio_pools_rather_than_averages():
    # Intervals of 16,000, 200 and 16,000 tokens: the mean of the per-turn
    # ratios is 0.579, which lets the 200-token turn carry a third of it.
    rows = [
        _turn(0, 16000, 0, {"committed_tokens": 0}),
        _turn(1, 32000, 0, {"committed_tokens": 8000}),
        _turn(2, 32200, 0, {"committed_tokens": 8200}),
        _turn(3, 48200, 0, {"committed_tokens": 12000}),
    ]
    derived = exp003.derive_turns(rows)
    ratios = [e["catch_up_ratio"] for e in derived if e["catch_up_ratio"] is not None]
    assert abs(sum(ratios) / len(ratios) - 0.5791666) < 1e-6

    summary = exp003.arm_summary({"turns": rows})
    assert summary["session_catch_up_ratio"] == 12000 / 32200


def test_an_interval_with_no_context_growth_still_counts():
    # The prefix advanced 12,000 tokens while the prompt did not move. The
    # per-turn ratio is undefined there and the mean dropped it entirely.
    rows = [
        _turn(0, 32000, 0, {"committed_tokens": 8000}),
        _turn(1, 32000, 0, {"committed_tokens": 20000}),
        _turn(2, 40000, 0, {"committed_tokens": 28000}),
    ]
    derived = exp003.derive_turns(rows)
    assert derived[0]["catch_up_ratio"] is None
    assert exp003.arm_summary({"turns": rows})["session_catch_up_ratio"] == 2.5


def test_the_session_ratio_survives_a_turn_that_reported_no_prefix():
    rows = [
        _turn(0, 16000, 0, {"committed_tokens": 0}),
        _turn(1, 32000, 0),
        _turn(2, 48000, 0, {"committed_tokens": 32000}),
    ]
    derived = exp003.derive_turns(rows)
    assert derived[1]["canonical_prefix_tokens"] is None
    assert exp003.arm_summary({"turns": rows})["session_catch_up_ratio"] == 1.0


def test_the_session_ratio_is_null_when_the_session_added_no_context():
    rows = [_turn(0, 32000, 0, {"committed_tokens": 8000}),
            _turn(1, 32000, 0, {"committed_tokens": 20000})]
    assert exp003.arm_summary({"turns": rows})["session_catch_up_ratio"] is None


def test_the_session_ratio_obeys_the_debt_identity():
    rows = [
        _turn(0, 16000, 0, {"committed_tokens": 0}),
        _turn(1, 32000, 0, {"committed_tokens": 8000}),
        _turn(2, 48000, 0, {"committed_tokens": 40000}),
    ]
    summary = exp003.arm_summary({"turns": rows})
    derived = exp003.derive_turns(rows)
    growth = rows[-1]["prompt_tokens"] - rows[0]["prompt_tokens"]
    debt_change = (derived[-1]["canonical_debt_tokens"]
                   - derived[0]["canonical_debt_tokens"])
    assert debt_change == growth * (1 - summary["session_catch_up_ratio"])


# ----------------------------------- the ratio while recovery was served


def _served(service_s):
    return {"committed_tokens": 0, "service_s": service_s}


def test_the_served_intervals_are_the_ones_the_counter_advanced_over():
    rows = [
        _turn(0, 16000, 0, {"committed_tokens": 0, "service_s": 0.0}),
        _turn(1, 32000, 0, {"committed_tokens": 16000, "service_s": 4.0}),
        _turn(2, 48000, 0, {"committed_tokens": 20000, "service_s": 4.0}),
        _turn(3, 64000, 0, {"committed_tokens": 40000, "service_s": 9.0}),
    ]
    assert exp003.recovery_served_intervals(rows) == [0, 2]
    summary = exp003.arm_summary({"turns": rows})
    # Served: 0 -> 16,000 and 20,000 -> 40,000 of prefix, over 32,000 of prompt.
    assert summary["recovery_served_catch_up_ratio"] == 36000 / 32000
    # Unrestricted: the whole session, including the interval it sat idle.
    assert summary["session_catch_up_ratio"] == 40000 / 48000


def test_an_arm_with_no_service_counter_has_no_served_ratio():
    summary = exp003.arm_summary(_appending_arm(), dense_break_even=8192)
    assert exp003.recovery_served_intervals(exp003.session_turns(_appending_arm())) == []
    assert summary["recovery_served_catch_up_ratio"] is None
    assert summary["session_catch_up_ratio"] is not None


def test_a_counter_that_never_advances_is_not_a_served_interval():
    rows = [_turn(0, 16000, 0, _served(2.0)), _turn(1, 32000, 0, _served(2.0))]
    assert exp003.recovery_served_intervals(rows) == []
    assert exp003.arm_summary({"turns": rows})["recovery_served_catch_up_ratio"] is None


def test_served_intervals_that_added_no_context_leave_the_ratio_undefined():
    rows = [_turn(0, 32000, 0, {"committed_tokens": 0, "service_s": 0.0}),
            _turn(1, 32000, 0, {"committed_tokens": 9000, "service_s": 5.0})]
    assert exp003.recovery_served_intervals(rows) == [0]
    # A rate per token added is undefined when no tokens were added; the
    # recovery itself is still visible in the prefix column.
    assert exp003.arm_summary({"turns": rows})["recovery_served_catch_up_ratio"] is None


# -------------------------------------------------- what the exit cost


def _exit_cost_arm() -> dict:
    """The shape of the observed run: the exit turn is the dearest one.

    A dense prefill of a 7,169-token tail costs more than a sparse prefill of
    the 27,649-token prompt before it.
    """
    turns = [
        _turn(0, 20480, 0, **_admitted("specprefill", 20480, 0)),
        _turn(1, 27649, 0, **_admitted("specprefill", 20480, 0)),
        _turn(2, 34818, 0, **_admitted("dense", 7169, 27649)),
        _turn(3, 41987, 0, **_admitted("dense", 7169, 34818)),
    ]
    turns[1]["ttft_s"], turns[1]["wall_s"] = 23.85, 25.0
    turns[2]["ttft_s"], turns[2]["wall_s"] = 42.77, 44.0
    turns[0]["wall_s"], turns[3]["wall_s"] = 18.0, 20.0
    return {"turns": turns}


def test_the_exit_columns_split_the_foreground_at_the_exit():
    summary = exp003.arm_summary(_exit_cost_arm())
    assert summary["spec_exit_turn"] == 2
    assert summary["pre_exit_foreground_s"] == 18.0 + 25.0
    assert summary["post_exit_foreground_s"] == 44.0 + 20.0
    assert summary["ttft_at_exit_s"] == 42.77
    assert summary["ttft_before_exit_s"] == 23.85


def test_the_exit_turn_may_be_the_most_expensive_and_the_table_shows_it():
    summary = exp003.arm_summary(_exit_cost_arm())
    # Nothing here calls that a win or a loss; the numbers are simply both
    # present, and the exit turn is dearer than the sparse turn before it.
    assert summary["ttft_at_exit_s"] > 23.85
    assert "exit_paid_back" not in summary
    assert "exit_verdict" not in summary


def test_an_exit_at_the_first_turn_has_no_foreground_before_it():
    rows = [_turn(0, 16000, 16000, **_admitted("cache_hit", 0, 16000)),
            _turn(1, 20000, 16000, **_admitted("dense", 4000, 16000))]
    for row, wall in zip(rows, (5.0, 6.0)):
        row["wall_s"] = wall
    summary = exp003.arm_summary({"turns": rows})
    assert summary["spec_exit_turn"] == 0
    # A measurement, not an absence: nothing ran before turn 0.
    assert summary["pre_exit_foreground_s"] == 0
    assert summary["post_exit_foreground_s"] == 11.0
    # There is no turn before turn 0, so no step to report.
    assert summary["ttft_before_exit_s"] is None


def test_no_exit_leaves_all_three_exit_columns_empty():
    rows = [_turn(0, 20480, 0, **_admitted("dense", 4000, 16480)),
            _turn(1, 40960, 0, **_admitted("specprefill", 20480, 20480))]
    summary = exp003.arm_summary({"turns": rows})
    assert summary["spec_exit_turn"] is None
    assert summary["pre_exit_foreground_s"] is None
    assert summary["post_exit_foreground_s"] is None
    assert summary["ttft_at_exit_s"] is None
    assert summary["ttft_before_exit_s"] is None


def test_a_missing_wall_time_empties_that_side_rather_than_undercounting():
    arm = _exit_cost_arm()
    arm["turns"][0]["wall_s"] = None
    summary = exp003.arm_summary(arm)
    assert summary["pre_exit_foreground_s"] is None
    assert summary["post_exit_foreground_s"] == 44.0 + 20.0


def test_the_exit_columns_reach_the_summary_table(tmp_path, monkeypatch):
    _, summary = _run_main(tmp_path, monkeypatch, {"pass": _exit_cost_arm()})
    assert summary[0]["spec_exit_turn"] == "2"
    assert summary[0]["pre_exit_foreground_s"] == "43.0"
    assert summary[0]["post_exit_foreground_s"] == "64.0"
    assert summary[0]["ttft_at_exit_s"] == "42.77"
    assert "mean_catch_up_ratio" not in summary[0]


# ------------------------------------------------- a sweep of several files


def test_the_two_field_sets_cannot_drift_apart():
    assert set(exp003.MULTI_TURN_FIELDS) == (
        set(exp003.TURN_FIELDS) | set(exp003.CELL_FIELDS))
    assert set(exp003.MULTI_SUMMARY_FIELDS) == (
        set(exp003.SUMMARY_FIELDS) | set(exp003.CELL_FIELDS))


def test_the_budget_sits_next_to_the_share_it_is_compared_against():
    fields = exp003.MULTI_SUMMARY_FIELDS
    assert fields[fields.index("budget_pct") + 1] == "measured_recovery_share"


def test_the_cell_identity_comes_from_the_files_meta():
    data = {"meta": {"budget_pct": 5.0, "idle_s": 75.0, "threshold": 8192,
                     "head": 24000, "append": 3000}}
    assert exp003.cell_identity(data, pathlib.Path("/x/exit-b5.json")) == {
        "cell": "exit-b5", "budget_pct": 5.0, "idle_s": 75.0,
        "threshold": 8192, "head": 24000, "append": 3000,
    }


def test_a_record_that_names_itself_keeps_its_own_name():
    data = {"meta": {"cell": "pass/budget-5", "budget_pct": 5.0}}
    identity = exp003.cell_identity(data, pathlib.Path("whatever.json"))
    assert identity["cell"] == "pass/budget-5"


def test_a_field_the_meta_does_not_carry_is_null_not_invented():
    identity = exp003.cell_identity({"meta": {"budget_pct": 5.0}},
                                    pathlib.Path("c.json"))
    assert identity["idle_s"] is None
    assert identity["threshold"] is None


def test_several_files_need_a_prefix_to_say_which_word_is_the_name():
    with pytest.raises(ValueError):
        exp003._inputs(["a.json", "b.json", "c.json"], None)
    paths, prefix = exp003._inputs(["a.json", "b.json"], "sweep")
    assert [path.name for path in paths] == ["a.json", "b.json"]
    assert prefix == "sweep"


def test_the_old_positional_form_is_untouched():
    paths, prefix = exp003._inputs(["runs.json"], None)
    assert [path.name for path in paths] == ["runs.json"] and prefix == ""
    paths, prefix = exp003._inputs(["runs.json", "compact"], None)
    assert prefix == "compact"


def test_the_flags_are_parsed_in_either_spelling():
    positional, override, prefix = exp003._parse_args(
        ["a.json", "--prefix=sweep", "--dense-break-even", "4096"])
    assert positional == ["a.json"] and override == 4096 and prefix == "sweep"


def test_the_foreground_total_is_read_under_either_name():
    assert exp003.cumulative_foreground_s({"session_s": 158.118}) == 158.118
    assert exp003.cumulative_foreground_s(
        {"cumulative_foreground_s": 30.0, "session_s": 99.0}) == 30.0
    assert exp003.cumulative_foreground_s({}) is None


def _sweep_cell(budget: float, session_s: float, exit_at: int) -> dict:
    """One cell of a budget sweep: four turns, exiting the sparse route once."""
    turns = []
    for index in range(4):
        route = "specprefill" if index < exit_at else "dense"
        tail = 20000 if route == "specprefill" else 4000
        turns.append(_turn(
            index, 16000 + 4000 * index, 0,
            {"committed_tokens": 4000 * index, "service_share": budget / 400},
            **_admitted(route, tail, 16000 + 4000 * index - tail)))
    return {
        "meta": {"budget_pct": budget, "idle_s": 75.0, "threshold": 8192,
                 "head": 24000, "append": 3000},
        "pcsr": {"turns": turns, "session_s": session_s},
    }


def _write_cells(tmp_path, cells: dict) -> list[str]:
    paths = []
    for name, data in cells.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(data))
        paths.append(str(path))
    return paths


def test_a_sweep_writes_one_summary_row_per_file_and_arm(tmp_path, monkeypatch):
    paths = _write_cells(tmp_path, {
        "cell-b5": _sweep_cell(5.0, 158.1, 2),
        "cell-b100": _sweep_cell(100.0, 214.9, 1),
    })
    monkeypatch.setattr(exp003, "OUT_DIR", tmp_path / "out")
    assert exp003.main(["exp003", *paths, "--prefix", "sweep"]) == 0

    summary = list(csv.DictReader((tmp_path / "out" / "sweep-summary.csv").open()))
    turns = list(csv.DictReader((tmp_path / "out" / "sweep-turns.csv").open()))
    assert [row["cell"] for row in summary] == ["cell-b5", "cell-b100"]
    assert len(turns) == 8
    assert [row["arm"] for row in summary] == ["pcsr", "pcsr"]


def test_every_row_of_a_sweep_carries_the_cell_it_came_from(tmp_path,
                                                            monkeypatch):
    paths = _write_cells(tmp_path, {"cell-b5": _sweep_cell(5.0, 158.1, 2)})
    paths += _write_cells(tmp_path, {"cell-b20": _sweep_cell(20.0, 158.4, 2)})
    monkeypatch.setattr(exp003, "OUT_DIR", tmp_path / "out")
    exp003.main(["exp003", *paths, "--prefix", "sweep"])

    turns = list(csv.DictReader((tmp_path / "out" / "sweep-turns.csv").open()))
    assert {row["cell"] for row in turns} == {"cell-b5", "cell-b20"}
    for row in turns:
        assert row["idle_s"] == "75.0"
        assert row["threshold"] == "8192"
        assert row["head"] == "24000"
        assert row["append"] == "3000"
    assert {row["budget_pct"] for row in turns} == {"5.0", "20.0"}


def test_a_sweeps_tables_carry_the_cell_columns_and_a_single_file_does_not(
        tmp_path, monkeypatch):
    monkeypatch.setattr(exp003, "OUT_DIR", tmp_path / "out")
    one = _write_cells(tmp_path, {"only": _sweep_cell(5.0, 158.1, 2)})
    exp003.main(["exp003", *one, "solo"])
    solo = csv.reader((tmp_path / "out" / "solo-turns.csv").open())
    assert tuple(next(solo)) == exp003.TURN_FIELDS

    two = one + _write_cells(tmp_path, {"other": _sweep_cell(20.0, 158.4, 2)})
    exp003.main(["exp003", *two, "--prefix", "both"])
    both = csv.reader((tmp_path / "out" / "both-turns.csv").open())
    assert tuple(next(both)) == exp003.MULTI_TURN_FIELDS


def test_the_summary_puts_the_budget_beside_the_share_it_got(tmp_path,
                                                             monkeypatch):
    paths = _write_cells(tmp_path, {
        "cell-b5": _sweep_cell(5.0, 158.1, 2),
        "cell-b20": _sweep_cell(20.0, 158.4, 2),
    })
    monkeypatch.setattr(exp003, "OUT_DIR", tmp_path / "out")
    exp003.main(["exp003", *paths, "--prefix", "sweep"])
    summary = list(csv.DictReader((tmp_path / "out" / "sweep-summary.csv").open()))
    assert [(row["budget_pct"], row["measured_recovery_share"])
            for row in summary] == [("5.0", "0.0125"), ("20.0", "0.05")]
    # The foreground total comes through under the runner's `session_s`.
    assert [row["cumulative_foreground_s"] for row in summary] == ["158.1", "158.4"]
