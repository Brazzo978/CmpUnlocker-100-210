#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("decode_gv100_top.py")
SPEC = importlib.util.spec_from_file_location("decode_gv100_top", MODULE_PATH)
assert SPEC and SPEC.loader
decoder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(decoder)


def top_line(index: int, value: int, bdf: str = "0000:01:00.0") -> str:
    reg = decoder.TOP_BASE + index * 4
    return f"cmp100_boot_ro bdf={bdf} reg=0x{reg:06x} value=0x{value:08x}"


def capture(words, extras=(), duplicate_lines=(), include_end=True):
    lines = ["cmp100_boot_ro BEGIN seq=7"]
    lines.extend(extras)
    lines.extend(top_line(index, value) for index, value in enumerate(words))
    lines.extend(duplicate_lines)
    if include_end:
        lines.append("cmp100_boot_ro END seq=7")
    return "\n".join(lines)


def gr_chain(reset=12):
    # DATA(addr=0x400000, continue), ENUM(reset, continue), TYPE(GR, end).
    data = 0x80000000 | 0x00400000 | 0x1
    enum = 0x80000000 | (reset << 9) | 0x4 | 0x2
    type_gr = 0x3
    return [data, enum, type_gr]


class DecodeTopTests(unittest.TestCase):
    def test_known_gr_data_enum_type_chain(self):
        words = gr_chain() + [0] * 61
        result = decoder.decode_top_words(words)
        self.assertEqual(len(result["devices"]), 1)
        gr = result["devices"][0]
        self.assertTrue(gr["complete"])
        self.assertEqual(gr["type"], "GR")
        self.assertEqual(gr["instance"], 0)
        self.assertEqual(gr["address"], "0x00400000")
        self.assertEqual(gr["reset_selector"], 12)

    def test_invalid_word_does_not_end_continued_record(self):
        chain = gr_chain(9)
        # A NOT_VALID word with continuation clear must continue per source.
        words = [chain[0], 0x00000000, chain[1], chain[2]] + [0] * 60
        result = decoder.decode_top_words(words)
        self.assertEqual(result["devices"][0]["word_indices"], [0, 2, 3])
        self.assertEqual(result["devices"][0]["reset_selector"], 9)
        self.assertIn(1, result["invalid_word_indices"])

    def test_incomplete_continuation_is_not_a_device(self):
        words = [0] * 64
        words[-1] = 0x80000000 | (17 << 9) | 0x4 | 0x2
        decoded = decoder.decode_top_words(words)
        self.assertEqual(decoded["devices"], [])
        self.assertIsNotNone(decoded["incomplete_record"])
        self.assertEqual(decoded["incomplete_record"]["reset_selector"], 17)


class CaptureTests(unittest.TestCase):
    def test_complete_snapshot_current_post_and_gr_mask(self):
        words = gr_chain(12) + [0] * 61
        extras = [
            "cmp100_boot_ro bdf=0000:01:00.0 reg=0x02240c value=0x00000002",
            "cmp100_boot_ro bdf=0000:01:00.0 reg=0x000200 value=0x1fecdff1",
        ]
        result = decoder.parse_capture(capture(words, extras))
        snap = result["snapshots"][0]
        self.assertTrue(snap["validation"]["valid"])
        self.assertEqual(snap["top"]["gr"]["reset_selector"], 12)
        self.assertEqual(snap["top"]["gr"]["pmc_mask"], "0x00001000")
        self.assertTrue(snap["top"]["gr"]["pmc_bit_set_current"])
        self.assertEqual(snap["post_0x2240c"]["bit1"], 1)
        self.assertFalse(snap["post_0x2240c"]["nouveau_would_run_devinit_now"])
        self.assertIn("current register state only", snap["post_0x2240c"]["interpretation_scope"])

    def test_missing_top_word_suppresses_reset(self):
        words = gr_chain(12) + [0] * 60  # 63 words total.
        snap = decoder.parse_capture(capture(words))["snapshots"][0]
        self.assertFalse(snap["top"]["input_valid_for_decode"])
        self.assertEqual(snap["top"]["missing_indices"], [63])
        self.assertIsNone(snap["top"]["gr"]["reset_selector"])
        self.assertIsNone(snap["top"]["gr"]["pmc_mask"])
        self.assertIsNone(snap["top"]["gr"]["pmc_bit_set_current"])

    def test_duplicate_top_register_suppresses_reset(self):
        words = gr_chain(12) + [0] * 61
        duplicate = top_line(1, words[1])
        snap = decoder.parse_capture(capture(words, duplicate_lines=[duplicate]))["snapshots"][0]
        self.assertFalse(snap["top"]["input_valid_for_decode"])
        self.assertEqual(len(snap["top"]["duplicate_registers"]), 1)
        self.assertIsNone(snap["top"]["gr"]["reset_selector"])

    def test_duplicate_all_ones_occurrence_is_reported_by_index(self):
        words = gr_chain(12) + [0] * 61
        duplicate = top_line(20, 0xFFFFFFFF)
        snap = decoder.parse_capture(capture(words, duplicate_lines=[duplicate]))["snapshots"][0]
        self.assertEqual(snap["top"]["all_ones_indices"], [20])
        self.assertIsNone(snap["top"]["gr"]["reset_selector"])

    def test_all_ones_suppresses_reset(self):
        snap = decoder.parse_capture(capture([0xFFFFFFFF] * 64))["snapshots"][0]
        self.assertEqual(snap["top"]["all_ones_indices"], list(range(64)))
        self.assertFalse(snap["top"]["input_valid_for_decode"])
        self.assertIsNone(snap["top"]["gr"]["reset_selector"])

    def test_one_all_ones_top_word_suppresses_reset(self):
        words = gr_chain(12) + [0] * 61
        words[40] = 0xFFFFFFFF
        snap = decoder.parse_capture(capture(words))["snapshots"][0]
        self.assertEqual(snap["top"]["all_ones_indices"], [40])
        self.assertFalse(snap["top"]["input_valid_for_decode"])
        self.assertIsNone(snap["top"]["gr"]["reset_selector"])

    def test_all_ones_aux_registers_have_no_derived_state(self):
        words = gr_chain(12) + [0] * 61
        extras = [
            "cmp100_boot_ro bdf=0000:01:00.0 reg=0x02240c value=0xffffffff",
            "cmp100_boot_ro bdf=0000:01:00.0 reg=0x000200 value=0xffffffff",
        ]
        snap = decoder.parse_capture(capture(words, extras))["snapshots"][0]
        self.assertFalse(snap["validation"]["valid"])
        self.assertNotIn("bit1", snap["post_0x2240c"])
        self.assertIsNone(snap["top"]["gr"]["pmc_bit_set_current"])
        self.assertEqual(snap["post_0x2240c"]["invalid_reason"], "all_ones_sentinel")
        self.assertEqual(snap["pmc_enable_0x200"]["invalid_reason"], "all_ones_sentinel")

    def test_incomplete_gr_record_does_not_supply_reset(self):
        words = [0] * 64
        words[-2] = 0x80000000 | (7 << 9) | 0x4 | 0x2
        words[-1] = 0  # NOT_VALID does not terminate the pending record.
        snap = decoder.parse_capture(capture(words))["snapshots"][0]
        self.assertFalse(snap["validation"]["valid"])
        self.assertIsNotNone(snap["top"]["incomplete_record"])
        self.assertIsNone(snap["top"]["gr"]["reset_selector"])

    def test_trailing_incomplete_record_suppresses_prior_gr_reset(self):
        words = gr_chain(12) + [0] * 60 + [0x80000003]
        snap = decoder.parse_capture(capture(words))["snapshots"][0]
        self.assertEqual(snap["top"]["devices"][0]["type"], "GR")
        self.assertIsNotNone(snap["top"]["incomplete_record"])
        self.assertIsNone(snap["top"]["gr"]["reset_selector"])
        self.assertEqual(snap["top"]["gr"]["derivation"], "suppressed_by_snapshot_validation")

    def test_boundary_bdf_mismatch_suppresses_reset(self):
        words = gr_chain(12) + [0] * 61
        text = capture(words).replace(
            "cmp100_boot_ro BEGIN seq=7",
            "cmp100_boot_ro BEGIN seq=7 bdf=0000:02:00.0",
        )
        snap = decoder.parse_capture(text)["snapshots"][0]
        self.assertFalse(snap["boundary_bdf"]["matches_observed"])
        self.assertFalse(snap["top"]["input_valid_for_decode"])
        self.assertIsNone(snap["top"]["gr"]["reset_selector"])

    def test_multiple_snapshots_and_current_post_zero(self):
        first = capture(gr_chain(3) + [0] * 61,
                        ["cmp100_boot_ro bdf=0000:01:00.0 reg=0x02240c value=0x00000000"])
        second = capture(gr_chain(4) + [0] * 61,
                         ["cmp100_boot_ro bdf=0000:02:00.0 reg=0x02240c value=0x00000002"])
        result = decoder.parse_capture(first + "\nnoise\n" + second)
        self.assertEqual(len(result["snapshots"]), 2)
        self.assertTrue(result["snapshots"][0]["post_0x2240c"]["nouveau_would_run_devinit_now"])
        self.assertFalse(result["snapshots"][1]["post_0x2240c"]["nouveau_would_run_devinit_now"])

    def test_unclosed_boundary_is_preserved_and_invalid(self):
        words = gr_chain(5) + [0] * 61
        result = decoder.parse_capture(capture(words, include_end=False))
        snap = result["snapshots"][0]
        self.assertIsNone(snap["boundary"]["end_line"])
        self.assertFalse(snap["top"]["input_valid_for_decode"])
        self.assertIsNone(snap["top"]["gr"]["reset_selector"])


if __name__ == "__main__":
    unittest.main()
