"""Checks against false certainty in the offline GR call inventory."""
import unittest
from inventory_gr_mmio_calls import inventory, parse


class InventoryTests(unittest.TestCase):
    def result(self, operations):
        seq = [(i, op, args.split()) for i, (op, args) in enumerate(operations)]
        return inventory(seq, {99: 'write'})[-1]['arguments']['r10']

    def test_computed_address(self):
        self.assertEqual(self.result([
            ('mov', '$r10 0x21000'), ('add', 'b32 $r10 $r10 0x760'),
            ('lcall', '0x63')]), '0x21760')

    def test_uncertainty_kills_previous_value(self):
        disruptions = [
            ('lcall', '0x55'), ('ld', 'b32 $r10 D[$r9]'),
            ('mov', 'b16 $r10 0'), ('mpopret', '$r1'),
            ('mpopaddret', '$r1 0x8'), ('iret', ''), ('bra', '0x2'),
        ]
        for disruption in disruptions:
            with self.subTest(disruption=disruption):
                self.assertIsNone(self.result([
                    ('mov', '$r10 0x21760'), disruption, ('lcall', '0x63')]))

    def test_join_does_not_inherit_linear_predecessor(self):
        self.assertIsNone(self.result([
            ('bra', '0x2'), ('mov', '$r10 0x21760'), ('lcall', '0x63')]))

    def test_self_copy_and_wrapping(self):
        self.assertEqual(self.result([
            ('mov', '$r10 0xffffffff'), ('mov', 'b32 $r10 $r10'),
            ('add', 'b32 $r10 $r10 0x2'), ('lcall', '0x63')]), '0x1')

    def test_combined_envydis_labels(self):
        self.assertEqual(parse('0000147d: 8a 70 41 40        CB mov $r10 0x404170'),
                         [(0x147d, 'mov', ['$r10', '0x404170'])])

    def test_unparsed_instruction_fails(self):
        with self.assertRaises(ValueError):
            parse('00000010: ?? unknown')


if __name__ == '__main__':
    unittest.main()
