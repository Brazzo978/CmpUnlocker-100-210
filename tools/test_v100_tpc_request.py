"""Check boundaries and mask invariants of the high-level offline model."""
import random
import unittest
from analyze_v100_tpc_request import model


class MaskModelTests(unittest.TestCase):
    def test_zero_controls_cannot_supply_positive_request(self):
        for count in range(1, 256):
            self.assertEqual(model([0]*6, count, 1)["writes"], {})

    def test_zero_request_does_not_write_existing_controls(self):
        self.assertEqual(model([0x7f]*6, 0, 1)["writes"], {})

    def test_largest_population_first_tie_and_lowest_bit(self):
        result = model([0b10101, 0b111, 0, 0, 0, 0], 2, 1)
        self.assertEqual(result["selected_slots"], [0, 1])
        self.assertEqual(result["writes"]["0x21838"], 0b10100)
        self.assertEqual(result["writes"]["0x2183c"], 0b110)

    def test_low_byte_guard_and_active_slot_writeback(self):
        self.assertEqual(model([3,0,0,0,0,0], 1, 0x100)["writes"], {})
        result = model([3,3,0,0,0,0], 1, 1, active_gpcs=2)
        self.assertEqual(result["selected_slots"], [0])
        self.assertEqual(result["writes"], {"0x2183c": 3})

    def test_clears_exact_request_without_creating_bits(self):
        rng = random.Random(100210)
        for _ in range(500):
            controls = [rng.randrange(128) for _ in range(6)]
            total = sum(x.bit_count() for x in controls)
            if total == 0:
                continue
            count = rng.randrange(1, total+1)
            result = model(controls, count, 1)
            after = [result["writes"][hex(0x21838+4*i)] for i in range(6)]
            self.assertEqual(total-sum(x.bit_count() for x in after), count)
            self.assertTrue(all((new & ~old) == 0 for new, old in zip(after, controls)))
            self.assertEqual(model(controls, total+1, 1)["writes"], {})


if __name__ == "__main__":
    unittest.main()
