#!/usr/bin/env python3
"""CPU-only regression tests for tools/gemm_stress.py.

Hermetic by design: torch is stubbed out so these run with plain system
python3 (no GPU, no PyTorch install). Run with:

    python3 tests/test_gemm_stress.py
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
import unittest


def load_gemm_stress():
    """Import tools/gemm_stress.py with a stub torch module."""
    stub = types.ModuleType("torch")
    stub.float16 = "torch.float16"
    stub.bfloat16 = "torch.bfloat16"
    stub.float32 = "torch.float32"

    def _no_grad(*args, **kwargs):
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]

        def wrap(fn):
            return fn

        return wrap

    stub.no_grad = _no_grad
    sys.modules["torch"] = stub
    try:
        root = pathlib.Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "gemm_stress", root / "tools" / "gemm_stress.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["gemm_stress"] = module  # dataclasses needs this
        spec.loader.exec_module(module)
        return module
    finally:
        del sys.modules["torch"]


gs = load_gemm_stress()


class AmdExclusionTest(unittest.TestCase):
    AMD_NAMES = [
        "AMD Instinct MI210",
        "AMD Radeon RX 7900 XTX",
        "Radeon Pro V340",
        "gfx90a",
        "Vega 10",
    ]
    NVIDIA_NAMES = [
        "Tesla V100-PCIE-16GB",
        "NVIDIA CMP 100-210",
        "NVIDIA H100 80GB HBM3",
        "NVIDIA GeForce RTX 4090",
    ]

    def test_amd_names_detected(self):
        for name in self.AMD_NAMES:
            self.assertTrue(gs.is_amd_device(name), name)

    def test_nvidia_names_kept(self):
        for name in self.NVIDIA_NAMES:
            self.assertFalse(gs.is_amd_device(name), name)

    def test_default_selection_excludes_amd(self):
        names = {0: "NVIDIA CMP 100-210", 1: "AMD Instinct MI210",
                 2: "Tesla V100-PCIE-16GB"}
        keep, skipped = gs.select_devices(None, names)
        self.assertEqual(keep, [0, 2])
        self.assertEqual(skipped, [1])

    def test_explicitly_requested_amd_still_excluded(self):
        names = {0: "NVIDIA CMP 100-210", 1: "AMD Instinct MI210"}
        keep, skipped = gs.select_devices([1], names)
        self.assertEqual(keep, [])
        self.assertEqual(skipped, [1])

    def test_unknown_indices_pass_through(self):
        # Out-of-range indices are left for torch to reject naturally.
        names = {0: "NVIDIA CMP 100-210"}
        keep, skipped = gs.select_devices([0, 7], names)
        self.assertEqual(keep, [0, 7])
        self.assertEqual(skipped, [])


class ShapesAndMemoryTest(unittest.TestCase):
    def test_parse_shapes(self):
        dims = gs.parse_shapes([1024], ["4096x2048x8192", "512,512,1024"])
        self.assertEqual(dims, [(1024, 1024, 1024), (4096, 2048, 8192),
                                (512, 512, 1024)])

    def test_parse_shapes_rejects_bad(self):
        for bad in ("1024xabc", "1024", "1x2x3x4", "0x1024x1024"):
            with self.assertRaises(ValueError, msg=bad):
                gs.parse_shapes([], [bad])

    def test_block_validation_fits_where_full_does_not(self):
        # 32k FP16 square: full FP32 reference blows a 12 GB budget while
        # corner-block validation stays well inside it.
        full = gs.estimate_bytes(32768, 32768, 32768, "fp16", True)
        block = gs.estimate_bytes(32768, 32768, 32768, "fp16", False)
        self.assertGreater(full, 12e9)
        self.assertLess(block, 12e9)


class TensorCoreCountTest(unittest.TestCase):
    def test_known_arches(self):
        self.assertEqual(gs.tensor_core_count(7, 0, 80), 640)   # GV100
        self.assertEqual(gs.tensor_core_count(7, 5, 72), 576)   # Turing
        self.assertEqual(gs.tensor_core_count(8, 0, 108), 432)  # Ampere
        self.assertEqual(gs.tensor_core_count(9, 0, 144), 576)  # Hopper

    def test_unknown_arch_or_sms_yields_none(self):
        self.assertIsNone(gs.tensor_core_count(6, 1, 20))
        self.assertIsNone(gs.tensor_core_count(10, 0, 100))
        self.assertIsNone(gs.tensor_core_count(7, 0, 0))
        self.assertIsNone(gs.tensor_core_count(8, 0, -4))


class BatchDevicesTest(unittest.TestCase):
    def test_even_split(self):
        self.assertEqual(gs.batch_devices([1, 2, 3, 4], 2),
                         [[1, 2], [3, 4]])

    def test_ragged_tail(self):
        self.assertEqual(gs.batch_devices([0, 1, 2, 3, 4, 5, 6, 7], 3),
                         [[0, 1, 2], [3, 4, 5], [6, 7]])

    def test_cap_above_count_is_one_batch(self):
        self.assertEqual(gs.batch_devices([1, 2], 8), [[1, 2]])

    def test_one_is_sequential(self):
        self.assertEqual(gs.batch_devices([1, 2, 3], 1),
                         [[1], [2], [3]])

    def test_zero_or_negative_rejected(self):
        for bad in (0, -2):
            with self.assertRaises(ValueError, msg=bad):
                gs.batch_devices([1, 2], bad)


class RunAllInterruptTest(unittest.TestCase):
    def setUp(self):
        gs._STOP.clear()
        self.calls = []

    def tearDown(self):
        gs._STOP.clear()

    def make_args(self, **overrides):
        params = {"max_parallel": 0, "parallel": False}
        params.update(overrides)
        return types.SimpleNamespace(**params)

    def stub_runner(self, idx, configs, args):
        self.calls.append(idx)
        return (f"gpu{idx}", [f"res{idx}"])

    def test_sequential_runs_all_in_order(self):
        per_device, interrupted = gs.run_all(
            [1, 2, 3], [], self.make_args(), runner=self.stub_runner)
        self.assertFalse(interrupted)
        self.assertEqual(self.calls, [1, 2, 3])
        self.assertEqual(sorted(per_device), [1, 2, 3])

    def test_parallel_flag_runs_all(self):
        per_device, interrupted = gs.run_all(
            [1, 2], [], self.make_args(parallel=True),
            runner=self.stub_runner)
        self.assertFalse(interrupted)
        self.assertEqual(sorted(self.calls), [1, 2])
        self.assertEqual(sorted(per_device), [1, 2])

    def test_interrupt_returns_partial_and_stops(self):
        def flaky(idx, configs, args):
            self.calls.append(idx)
            if idx == 2:
                raise KeyboardInterrupt
            return (f"gpu{idx}", [f"res{idx}"])

        per_device, interrupted = gs.run_all(
            [1, 2, 3], [], self.make_args(), runner=flaky)
        self.assertTrue(interrupted)
        self.assertTrue(gs.stop_requested())
        self.assertEqual(self.calls, [1, 2])
        self.assertEqual(sorted(per_device), [1])

    def test_preset_stop_flag_runs_nothing(self):
        gs.request_stop()
        per_device, interrupted = gs.run_all(
            [1, 2], [], self.make_args(), runner=self.stub_runner)
        self.assertTrue(interrupted)
        self.assertEqual(self.calls, [])
        self.assertEqual(per_device, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
