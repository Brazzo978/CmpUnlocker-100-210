import importlib.util
import pathlib
import struct
import unittest

ROOT = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("probe", ROOT / "analyze_pmu_readability_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ReadabilityProbeTests(unittest.TestCase):
    def setUp(self):
        self.imem = bytearray(0x6700)
        for address in (0, 4, 0x40, 0x1F8, 0x1FC, 0x200, 0x204, 0x208):
            struct.pack_into("<I", self.imem, address, 0x13570000 | address)
        self.old_hash = probe.EXPECTED_IMEM_SHA256
        import hashlib
        probe.EXPECTED_IMEM_SHA256 = hashlib.sha256(self.imem).hexdigest()

    def tearDown(self):
        probe.EXPECTED_IMEM_SHA256 = self.old_hash

    def capture(self, secure_values=None, dmem_value=0x12345678):
        secure_values = secure_values or {}
        lines = [
            "cmp100_pmu_readability BEGIN bdf=x captures=1 samples=24 invalid=0 nmissed=0",
            "cmp100_pmu_readability SELECTORS saved_imemc=0x1 restored_imemc=0x1 saved_dmemc=0x2 restored_dmemc=0x2",
        ]
        offsets = (0, 4, 0x40, 0x1F8, 0x1FC, 0x200, 0x204, 0x208)
        for secure in (0, 1):
            for address in offsets:
                expected = struct.unpack_from("<I", self.imem, address)[0]
                value = secure_values.get(address, expected) if secure else expected
                lines.append(f"cmp100_pmu_readability VALUE space=I secure={secure} address=0x{address:04x} value=0x{value:08x}")
        for address in (0, 4, 0x48, 0x3A8, 0x3B0, 0x558, 0x600, 0x6FC):
            lines.append(f"cmp100_pmu_readability VALUE space=D secure=0 address=0x{address:04x} value=0x{dmem_value:08x}")
        return "\n".join(lines)

    def test_secure_body_match(self):
        result = probe.analyze(self.capture(), bytes(self.imem))
        self.assertTrue(result["valid"])
        self.assertEqual(result["verdict"], "selected_secure_imem_words_match_loaded_pre_os")
        self.assertEqual(result["secure_body_matches"], 3)

    def test_poison_is_not_readability(self):
        result = probe.analyze(
            self.capture(
                {0x200: 0xBADF1100, 0x204: 0xBADF1100, 0x208: 0xBADF1100},
                dmem_value=0xDEAD5EC2,
            ),
            bytes(self.imem),
        )
        self.assertEqual(result["verdict"], "secure_imem_not_shown_readable")
        self.assertTrue(result["secure_body_degenerate"])
        self.assertEqual(result["dmem_uniform_value"], 0xDEAD5EC2)
        self.assertTrue(result["dmem_uniform_poison"])

    def test_selector_mismatch_invalidates(self):
        text = self.capture().replace("restored_imemc=0x1", "restored_imemc=0x9")
        result = probe.analyze(text, bytes(self.imem))
        self.assertFalse(result["valid"])
        self.assertEqual(result["verdict"], "invalid_capture")


if __name__ == "__main__":
    unittest.main()
