#!/usr/bin/env python3
"""Focused regression tests for decode_gv100_stages.py."""

from __future__ import annotations

import importlib.util
import io
import json
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("decode_gv100_stages.py")
SPEC = importlib.util.spec_from_file_location("decode_gv100_stages", MODULE_PATH)
assert SPEC and SPEC.loader
decoder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(decoder)


STAGES = list(decoder.CORE_SEQUENCE)
LOGICAL_STAGES = decoder.LOGICAL_STAGES
SYMBOLS = sorted(decoder.MISSED_SYMBOLS)


def _physical_values(seed: int = 0) -> dict[int, int]:
    values = {
        decoder.PMC_REG: 0x1FECDFF1,
        decoder.POST_REG: 2,
        decoder.GPC_STATUS_REG: 1,
    }
    for slot in range(6):
        values[decoder.RAW_REGS[slot]] = seed + slot
        values[decoder.CTRL_REGS[slot]] = 0x10 + seed + slot
        values[decoder.STATUS_REGS[slot]] = 0x20 + seed + slot
    return values


def _logical_values(tpc_sum: int = 34, gpcs: int = 5) -> dict[int, int]:
    counts = [7, 7, 7, 7, 6]
    if tpc_sum != 34:
        counts[-1] += tpc_sum - 34
    values = {
        decoder.LOGICAL_GPC_COUNT_REG: gpcs,
        decoder.COMPUTE_PIPE_CONTROL_REG: 0x00000001,
    }
    for gpc in range(gpcs):
        values[decoder.GPC_TPC_COUNT_BASE + decoder.GPC_STRIDE * gpc] = counts[gpc]
        for ppc, mask in enumerate((0x09, 0x12, 0x24)):
            values[decoder.PPC_TPC_MASK_BASE + decoder.GPC_STRIDE * gpc + 4 * ppc] = mask
    return values


def make_run(
    *,
    bdf: str = "0000:01:00.0",
    dropped: int = 0,
    missed: dict[str, int] | None = None,
    invalid_by_stage: dict[str, int] | None = None,
    rc_by_stage: dict[str, int] | None = None,
    values_by_stage: dict[str, dict[int, int]] | None = None,
    post_value: int = 0,
    post_by_stage: dict[str, int] | None = None,
    pmu_present: int | None = None,
    stages: list[str] | None = None,
    include_end: bool = True,
    declared_records: int | None = None,
    armed_type: int | None = 34,
) -> str:
    stages = list(STAGES if stages is None else stages)
    invalid_by_stage = invalid_by_stage or {}
    rc_by_stage = rc_by_stage or {}
    values_by_stage = values_by_stage or {}
    post_by_stage = post_by_stage or {}
    lines: list[str] = []
    if armed_type is not None:
        lines.append(f"[ 1.0] cmp100_stage_ro ARMED bdf={bdf} live_gr_type={armed_type}")
    count = len(stages) if declared_records is None else declared_records
    pmu_field = "" if pmu_present is None else f" pmu_present={pmu_present}"
    lines.append(
        f"[ 2.0] cmp100_stage_ro BEGIN bdf={bdf} records={count} dropped={dropped}{pmu_field}"
    )
    for seq, name in enumerate(stages):
        rc = 4096 if name == "gr_reset_mask_return" else 0
        rc = rc_by_stage.get(name, rc)
        invalid = invalid_by_stage.get(name, 0)
        post = post_value if name in {"post_entry", "post_return"} else -1
        post = post_by_stage.get(name, post)
        lines.append(
            "cmp100_stage_ro STAGE "
            f"seq={seq} name={name} start_ns={1000 + seq * 10} "
            f"end_ns={1005 + seq * 10} pid=123 rc={rc} post={post} invalid={invalid}"
        )
        values = _physical_values()
        if name in LOGICAL_STAGES:
            values.update(_logical_values())
        values.update(values_by_stage.get(name, {}))
        for reg, value in values.items():
            lines.append(
                f"cmp100_stage_ro VALUE seq={seq} reg=0x{reg:06x} value=0x{value:08x}"
            )
    missed = missed or {}
    for symbol in SYMBOLS:
        lines.append(
            f"cmp100_stage_ro MISSED symbol={symbol} nmissed={missed.get(symbol, 0)}"
        )
    if include_end:
        lines.append(f"cmp100_stage_ro END bdf={bdf}")
    return "\n".join(lines) + "\n"


def selected(text: str) -> dict:
    result = decoder.parse_capture(text)
    index = result["latest_complete_run_index"]
    assert index is not None
    return result["runs"][index]


def codes(run: dict) -> list[str]:
    return [item["code"] for item in run["validation"]["errors"]]


class DecodeStagesTests(unittest.TestCase):
    def test_valid_run_and_reset_mask_4096(self) -> None:
        run = selected(make_run())
        self.assertTrue(run["validation"]["valid"])
        self.assertFalse(run["mask_comparison"]["any_observed_change"])
        self.assertEqual(run["validation"]["expected_live_gr_type"], 34)
        baseline = run["stages"][0]
        self.assertEqual(baseline["logical_topology"]["tpc_count_sum"], 34)

    def test_forced_post_with_present_pmu_is_valid(self) -> None:
        run = selected(make_run(post_value=1, pmu_present=1))
        self.assertTrue(run["validation"]["valid"])
        self.assertEqual(run["pmu_present"], 1)
        self.assertEqual(
            [stage["post"] for stage in run["stages"] if stage["name"].startswith("post_")],
            [1, 1],
        )

    def test_forced_post_requires_present_pmu(self) -> None:
        missing = selected(make_run(post_value=1, pmu_present=None))
        null = selected(make_run(post_value=1, pmu_present=0))
        self.assertIn("forced_post_requires_pmu", codes(missing))
        self.assertIn("forced_post_requires_pmu", codes(null))

    def test_post_entry_and_return_must_match(self) -> None:
        run = selected(
            make_run(
                pmu_present=1,
                post_by_stage={"post_entry": 1, "post_return": 0},
            )
        )
        self.assertIn("post_value_mismatch", codes(run))

    def test_post_values_must_be_boolean(self) -> None:
        run = selected(make_run(post_value=-1))
        errors = [item for item in run["validation"]["errors"] if item["code"] == "post_value_not_boolean"]
        self.assertEqual(len(errors), 2)

    def test_false_post_legacy_capture_without_pmu_field_stays_valid(self) -> None:
        run = selected(make_run(post_value=0, pmu_present=None))
        self.assertTrue(run["validation"]["valid"])
        self.assertIsNone(run["pmu_present"])

    def test_failed_forced_post_prefix_is_preserved_but_invalid(self) -> None:
        run = selected(
            make_run(
                stages=["nvidia_baseline", "post_entry", "post_return"],
                post_value=1,
                pmu_present=1,
                rc_by_stage={"post_return": -110},
            )
        )
        self.assertEqual([stage["name"] for stage in run["stages"]], [
            "nvidia_baseline",
            "post_entry",
            "post_return",
        ])
        self.assertFalse(run["validation"]["valid"])
        self.assertIn("negative_return", codes(run))
        self.assertIn("missing_stage", codes(run))

    def test_mask_change_is_reported_by_family_and_slot(self) -> None:
        changed = {decoder.CTRL_REGS[3]: 0xDEADBEEF}
        run = selected(make_run(values_by_stage={"ctxctl_return": changed}))
        self.assertTrue(run["validation"]["valid"])
        self.assertTrue(run["mask_comparison"]["any_observed_change"])
        last = run["mask_comparison"]["transitions"][-1]
        self.assertEqual(last["changed_slots"]["ctrl"], [3])
        self.assertEqual(last["changed_slots"]["raw"], [])
        self.assertIn("unobserved", run["mask_comparison"]["scope"])

    def test_latest_complete_skips_trailing_incomplete_run(self) -> None:
        first = make_run(bdf="0000:01:00.0")
        trailing = make_run(bdf="0000:02:00.0", include_end=False)
        result = decoder.parse_capture(first + trailing)
        self.assertEqual(result["run_count"], 2)
        self.assertEqual(result["latest_complete_run_index"], 0)
        self.assertIn("missing_end", codes(result["runs"][1]))

    def test_latest_complete_can_be_semantically_invalid(self) -> None:
        result = decoder.parse_capture(make_run() + make_run(dropped=1))
        self.assertEqual(result["latest_complete_run_index"], 1)
        self.assertIn("records_dropped", codes(result["runs"][1]))

    def test_dropped_and_probe_missed_are_invalid(self) -> None:
        run = selected(make_run(dropped=2, missed={"nvkm_mc_reset": 1}))
        self.assertIn("records_dropped", codes(run))
        self.assertIn("probe_missed", codes(run))

    def test_missing_missed_symbol_is_invalid(self) -> None:
        text = make_run().replace(
            "cmp100_stage_ro MISSED symbol=nvkm_mc_reset nmissed=0\n", ""
        )
        self.assertIn("missing_missed_counter", codes(selected(text)))

    def test_collector_invalid_flags_are_rejected(self) -> None:
        run = selected(make_run(invalid_by_stage={"post_return": 4}))
        error = next(item for item in run["validation"]["errors"] if item["code"] == "collector_invalid")
        self.assertEqual(error["invalid"], 4)
        self.assertEqual(error["flags"], ["bad_gpc_count_or_missing_pmc_gr_bit"])

    def test_any_all_ones_register_value_is_rejected(self) -> None:
        run = selected(
            make_run(values_by_stage={"post_entry": {decoder.RAW_REGS[4]: 0xFFFFFFFF}})
        )
        self.assertIn("all_ones_value", codes(run))

    def test_aux_all_ones_values_are_rejected_too(self) -> None:
        run = selected(
            make_run(values_by_stage={"nvidia_baseline": {decoder.PMC_REG: 0xFFFFFFFF}})
        )
        self.assertIn("all_ones_value", codes(run))

    def test_missing_physical_register_is_invalid(self) -> None:
        text = make_run()
        line = f"cmp100_stage_ro VALUE seq=1 reg=0x{decoder.STATUS_REGS[5]:06x} value=0x00000025\n"
        run = selected(text.replace(line, ""))
        self.assertIn("missing_physical_registers", codes(run))

    def test_missing_one_of_three_ppc_masks_is_invalid(self) -> None:
        text = make_run()
        seq = STAGES.index("ctxctl_entry")
        reg = decoder.PPC_TPC_MASK_BASE + decoder.GPC_STRIDE * 4 + 8
        line = f"cmp100_stage_ro VALUE seq={seq} reg=0x{reg:06x} value=0x00000024\n"
        run = selected(text.replace(line, ""))
        error = next(item for item in run["validation"]["errors"] if item["code"] == "missing_ppc_masks")
        self.assertIn(f"0x{reg:06x}", error["registers"])

    def test_missing_compute_pipe_control_uses_precise_name(self) -> None:
        text = make_run()
        seq = STAGES.index("oneinit_return")
        line = (
            f"cmp100_stage_ro VALUE seq={seq} reg=0x{decoder.COMPUTE_PIPE_CONTROL_REG:06x} "
            "value=0x00000001\n"
        )
        run = selected(text.replace(line, ""))
        self.assertIn("missing_compute_pipe_control", codes(run))

    def test_logical_gpc_count_must_be_one_through_six(self) -> None:
        run = selected(
            make_run(values_by_stage={"nvidia_baseline": {decoder.LOGICAL_GPC_COUNT_REG: 0}})
        )
        self.assertIn("invalid_logical_gpc_count", codes(run))

    def test_tpc_sum_is_reported_without_conflating_it_with_gr_enum(self) -> None:
        replacements: dict[str, dict[int, int]] = {}
        for stage in LOGICAL_STAGES:
            values = _logical_values(tpc_sum=35)
            replacements[stage] = values
        run = selected(make_run(values_by_stage=replacements))
        self.assertTrue(run["validation"]["valid"])
        sums = [
            stage["logical_topology"]["tpc_count_sum"]
            for stage in run["stages"]
            if stage["name"] in LOGICAL_STAGES
        ]
        self.assertEqual(sums, [35, 35, 35, 35])
        self.assertEqual(run["validation"]["expected_live_gr_type"], 34)

    def test_negative_and_nonzero_function_returns_are_invalid(self) -> None:
        run = selected(make_run(rc_by_stage={"oneinit_return": -5, "post_return": 1}))
        self.assertIn("negative_return", codes(run))
        self.assertGreaterEqual(codes(run).count("unexpected_return"), 2)

    def test_missing_core_stage_is_invalid(self) -> None:
        stages = [name for name in STAGES if name != "ctxctl_entry"]
        run = selected(make_run(stages=stages))
        self.assertIn("missing_stage", codes(run))

    def test_out_of_order_core_stage_is_invalid(self) -> None:
        stages = list(STAGES)
        left = stages.index("oneinit_entry")
        right = stages.index("oneinit_return")
        stages[left], stages[right] = stages[right], stages[left]
        run = selected(make_run(stages=stages))
        self.assertIn("stage_order", codes(run))

    def test_duplicate_reset_calls_before_oneinit_are_ambiguous_and_preserved(self) -> None:
        stages = list(STAGES)
        insert_at = stages.index("gr_reset_return") + 1
        stages[insert_at:insert_at] = ["gr_reset_mask_return", "gr_reset_return"]
        run = selected(make_run(stages=stages))
        self.assertEqual(len(run["stages"]), len(stages))
        self.assertIn("ambiguous_initial_reset", codes(run))
        self.assertIn("unclassified_reset_events", codes(run))

    def test_complete_reset_pair_after_ctxctl_is_classified_and_valid(self) -> None:
        stages = list(STAGES) + ["gr_reset_mask_return", "gr_reset_return"]
        run = selected(make_run(stages=stages))
        self.assertTrue(run["validation"]["valid"])
        self.assertEqual(
            run["reset_classification"]["post_ctxctl_pairs"],
            [{"mask_seq": len(STAGES), "reset_seq": len(STAGES) + 1}],
        )

    def test_declared_count_and_bdf_mismatch_are_invalid(self) -> None:
        text = make_run(declared_records=99).replace(
            "cmp100_stage_ro END bdf=0000:01:00.0",
            "cmp100_stage_ro END bdf=0000:02:00.0",
        )
        run = selected(text)
        self.assertIn("record_count_mismatch", codes(run))
        self.assertIn("bdf_mismatch", codes(run))

    def test_unexpected_armed_gr_type_is_invalid(self) -> None:
        run = selected(make_run(armed_type=35))
        self.assertIn("unexpected_live_gr_type", codes(run))

    def test_each_run_uses_nearest_preceding_armed_type(self) -> None:
        result = decoder.parse_capture(make_run(armed_type=35) + make_run(armed_type=34))
        self.assertIn("unexpected_live_gr_type", codes(result["runs"][0]))
        self.assertTrue(result["runs"][1]["validation"]["valid"])

    def test_duplicate_register_is_invalid_and_raw_values_remain(self) -> None:
        text = make_run()
        seq = STAGES.index("post_entry")
        duplicate = (
            f"cmp100_stage_ro VALUE seq={seq} reg=0x{decoder.RAW_REGS[0]:06x} "
            "value=0x12345678\n"
        )
        anchor = f"cmp100_stage_ro MISSED symbol={SYMBOLS[0]}"
        text = text.replace(anchor, duplicate + anchor)
        run = selected(text)
        self.assertIn("duplicate_register", codes(run))
        stage = next(item for item in run["stages"] if item["seq"] == seq)
        self.assertEqual(
            sum(item["reg"] == f"0x{decoder.RAW_REGS[0]:06x}" for item in stage["values"]),
            2,
        )

    def test_empty_input_has_no_selected_run_and_exits_two(self) -> None:
        old_stdin, old_stdout = decoder.sys.stdin, decoder.sys.stdout
        decoder.sys.stdin, decoder.sys.stdout = io.StringIO(""), io.StringIO()
        try:
            rc = decoder.main([])
            output = json.loads(decoder.sys.stdout.getvalue())
        finally:
            decoder.sys.stdin, decoder.sys.stdout = old_stdin, old_stdout
        self.assertEqual(rc, 2)
        self.assertEqual(output["run_count"], 0)
        self.assertIsNone(output["selected_run_index"])
        self.assertIsNone(output["selected_run"])

    def test_only_incomplete_run_has_no_selection_and_exits_two_with_all(self) -> None:
        capture = make_run(include_end=False)
        old_stdin, old_stdout = decoder.sys.stdin, decoder.sys.stdout
        decoder.sys.stdin, decoder.sys.stdout = io.StringIO(capture), io.StringIO()
        try:
            rc = decoder.main(["--all"])
            output = json.loads(decoder.sys.stdout.getvalue())
        finally:
            decoder.sys.stdin, decoder.sys.stdout = old_stdin, old_stdout
        self.assertEqual(rc, 2)
        self.assertIsNone(output["latest_complete_run_index"])
        self.assertEqual(len(output["runs"]), 1)
        self.assertFalse(output["runs"][0]["delimiter_complete"])


if __name__ == "__main__":
    unittest.main()
