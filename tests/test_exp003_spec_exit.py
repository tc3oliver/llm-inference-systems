"""Tests for the EXP-003 / PCSR Spec Exit derivations.

Nothing here starts a server or reads a run of the real experiment. Every case
is a hand-built record chosen to pin one definition down, including the ones
where the honest answer is "no value".
"""

from __future__ import annotations

import csv
import json

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
    assert summary["mean_catch_up_ratio"] == (1.0 + 1.75) / 2
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
