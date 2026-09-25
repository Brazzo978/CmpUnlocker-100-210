#!/usr/bin/env python3
"""Decode and compare the known GV100 DEVINIT PCIe XLAT tails.

It applies the validated NVGI/PCI-image base mapping, verifies the two pinned
ROMs and prints the condition predicates, full opcode semantics and X00 table
that the older normalized CSV represented incompletely.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path


ROMS = {
    "CMP": {
        "sha256": "e77a5ca44212927a06004fd9910d5456302750e11a773c84817e8fef5ee8e46b",
        "xlat": 0xD9C8,
        "follow": 0xD9E0,
    },
    "V100": {
        "sha256": "c45cf4cca3531de82b7b1a587b81f9f31d58e5589fafad0bb431bb95d89d61d8",
        "xlat": 0xC583,
        "follow": 0xC59B,
    },
}

OPTION_ROM_BASE = 0xA00
BIT_I_DATA = 0xC83


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def load(label: str, path: Path) -> bytes:
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    expected = ROMS[label]["sha256"]
    if actual != expected:
        raise SystemExit(
            f"{label}: SHA-256 mismatch\nexpected {expected}\nactual   {actual}"
        )
    return data


def decode(label: str, data: bytes) -> dict[str, object]:
    off = int(ROMS[label]["xlat"])
    follow = int(ROMS[label]["follow"])
    raw = data[off : off + 17]
    if len(raw) != 17 or raw[0] != 0x96:
        raise SystemExit(f"{label}: expected XLAT opcode 0x96 at {off:#x}")
    if data[off - 3] != 0x75 or data[off - 1] != 0x38:
        raise SystemExit(f"{label}: expected CONDITION,index,NOT before XLAT")
    if data[follow] != 0x6E:
        raise SystemExit(f"{label}: expected NV_REG opcode 0x6e at {follow:#x}")

    shift_raw = raw[5]
    left = bool(shift_raw & 0x80)
    shift = 0x100 - shift_raw if left else shift_raw
    condition_base = OPTION_ROM_BASE + struct.unpack_from(
        "<H", data, BIT_I_DATA + 6
    )[0]
    xlat_pointer_table = OPTION_ROM_BASE + struct.unpack_from(
        "<H", data, BIT_I_DATA + 16
    )[0]
    xlat_data = OPTION_ROM_BASE + struct.unpack_from(
        "<H", data, xlat_pointer_table + 2 * raw[7]
    )[0]
    input_entries = raw[6] + 1
    translation = data[xlat_data : xlat_data + input_entries]
    if len(translation) != input_entries:
        raise SystemExit(f"{label}: truncated X{raw[7]:02x} table")

    condition = data[off - 2]
    follow_condition = data[follow - 2]
    condition_predicate = struct.unpack_from(
        "<III", data, condition_base + 12 * condition
    )
    follow_predicate = struct.unpack_from(
        "<III", data, condition_base + 12 * follow_condition
    )
    return {
        "label": label,
        "offset": off,
        "condition": condition,
        "condition_base": condition_base,
        "condition_predicate": condition_predicate,
        "raw": raw,
        "source": u32(raw, 1),
        "direction": "<<" if left else ">>",
        "source_shift": shift,
        "source_mask": raw[6],
        "xlat_index": raw[7],
        "xlat_pointer_table": xlat_pointer_table,
        "xlat_data": xlat_data,
        "translation": translation,
        "destination": u32(raw, 8),
        "and_mask": u32(raw, 12),
        "destination_shift": raw[16],
        "follow_offset": follow,
        "follow_condition": follow_condition,
        "follow_predicate": follow_predicate,
        "follow_address": u32(data, follow + 1),
        "follow_and": u32(data, follow + 5),
        "follow_or": u32(data, follow + 9),
    }


def describe(item: dict[str, object]) -> None:
    condition_reg, condition_mask, condition_value = item["condition_predicate"]
    follow_reg, follow_mask, follow_value = item["follow_predicate"]
    print(
        f"{item['label']}: NOT(COND#{item['condition']}) @ {item['offset']:#x}\n"
        f"  COND#{item['condition']}: R[{condition_reg:#08x}] & "
        f"{condition_mask:#010x} == {condition_value:#010x}\n"
        f"  bytes: {bytes(item['raw']).hex(' ')}\n"
        f"  R[{item['destination']:#08x}] &= {item['and_mask']:#010x}\n"
        f"  R[{item['destination']:#08x}] |= "
        f"X{item['xlat_index']:02x}((R[{item['source']:#08x}] "
        f"{item['direction']} {item['source_shift']:#x}) & "
        f"{item['source_mask']:#x}) << {item['destination_shift']:#x}\n"
        f"  X{item['xlat_index']:02x} @ {item['xlat_data']:#x}: "
        f"{bytes(item['translation']).hex(' ')}\n"
        f"  follow: NOT(COND#{item['follow_condition']}) @ "
        f"{item['follow_offset']:#x}: R[{item['follow_address']:#08x}] &= "
        f"{item['follow_and']:#010x} |= {item['follow_or']:#010x}\n"
        f"  COND#{item['follow_condition']}: R[{follow_reg:#08x}] & "
        f"{follow_mask:#010x} == {follow_value:#010x}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cmp_rom", type=Path)
    parser.add_argument("v100_rom", type=Path)
    args = parser.parse_args()

    cmp_item = decode("CMP", load("CMP", args.cmp_rom))
    v100_item = decode("V100", load("V100", args.v100_rom))
    describe(cmp_item)
    describe(v100_item)

    same_xlat = cmp_item["raw"] == v100_item["raw"]
    different_guards = (
        cmp_item["condition"], cmp_item["follow_condition"]
    ) != (v100_item["condition"], v100_item["follow_condition"])
    same_predicates = (
        cmp_item["condition_predicate"] == v100_item["condition_predicate"]
        and cmp_item["follow_predicate"] == v100_item["follow_predicate"]
    )
    same_translation = cmp_item["translation"] == v100_item["translation"]
    print(f"same XLAT bytes: {str(same_xlat).lower()}")
    print(f"different ROM-local condition indices: {str(different_guards).lower()}")
    print(f"same effective condition predicates: {str(same_predicates).lower()}")
    print(f"same X00 translation entries: {str(same_translation).lower()}")
    return 0 if all(
        (same_xlat, different_guards, same_predicates, same_translation)
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
