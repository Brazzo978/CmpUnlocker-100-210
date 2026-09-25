#!/usr/bin/env python3
"""Decode the hash-pinned GV100 DEVINIT GPC-status copy block.

Offline only.  Prints metadata and equations; it does not alter either ROM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path


ROMS = {
    "CMP": {
        "sha256": "e77a5ca44212927a06004fd9910d5456302750e11a773c84817e8fef5ee8e46b",
        "condition_offset": 0x8352,
        "condition_index": 84,
        "copy_offset": 0x8399,
    },
    "V100": {
        "sha256": "c45cf4cca3531de82b7b1a587b81f9f31d58e5589fafad0bb431bb95d89d61d8",
        "condition_offset": 0x82FD,
        "condition_index": 82,
        "copy_offset": 0x8344,
    },
}


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def all_u32_offsets(data: bytes, value: int) -> list[str]:
    needle = struct.pack("<I", value)
    return [hex(i) for i in range(len(data) - 3) if data[i:i + 4] == needle]


def decode(label: str, path: Path) -> dict:
    expected = ROMS[label]
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    require(digest == expected["sha256"], f"{label}: SHA-256 mismatch")

    condition_offset = expected["condition_offset"]
    condition_index = expected["condition_index"]
    copy_offset = expected["copy_offset"]
    require(data[condition_offset:condition_offset + 3] == bytes((0x75, condition_index, 0x38)),
            f"{label}: expected CONDITION index {condition_index} followed by NOT")

    # Validated PCI-image mapping also used by the existing bounded DEVINIT
    # analyzer: BIT condition-table pointer at ROM 0xc89 is relative to 0xa00.
    condition_base = 0xA00 + struct.unpack_from("<H", data, 0xC89)[0]
    condition = struct.unpack_from("<III", data, condition_base + 12 * condition_index)
    require(condition == (0x213EC, 1, 0), f"{label}: unexpected condition-table row")

    require(data[copy_offset] == 0x5F, f"{label}: expected INIT_NV_COPY")
    source, shift, source_and, source_xor, destination, destination_and = struct.unpack_from(
        "<IbIIII", data, copy_offset + 1)
    require((source, shift, source_and, source_xor, destination, destination_and) ==
            (0x21C1C, 0, 0x3F, 0x3F, 0x206F0, 0xFFFFFFC0),
            f"{label}: unexpected INIT_NV_COPY fields")
    require(data[copy_offset + 22] == 0x72, f"{label}: expected RESUME after copy")

    target_words = ([0x21760 + 4 * i for i in range(6)] +
                    [0x21C38 + 4 * i for i in range(6)])
    return {
        "rom": label,
        "sha256": digest,
        "condition": {
            "file_offset": hex(condition_offset),
            "decimal_index": condition_index,
            "table_base": hex(condition_base),
            "register": hex(condition[0]),
            "mask": hex(condition[1]),
            "compare": hex(condition[2]),
            "following_opcode": "INIT_NOT",
            "bounded_effect": "with incoming allow, guarded block runs when (R[0x213ec] & 1) != 0",
        },
        "copy": {
            "file_offset": hex(copy_offset),
            "opcode": "INIT_NV_COPY (0x5f)",
            "source": hex(source),
            "shift": shift,
            "source_and": hex(source_and),
            "source_xor": hex(source_xor),
            "destination": hex(destination),
            "destination_and": hex(destination_and),
            "equation": "R[0x206f0] = (old & 0xffffffc0) | ((R[0x21c1c] & 0x3f) ^ 0x3f)",
            "next_opcode": "INIT_RESUME (0x72)",
        },
        "literal_occurrences_for_raw_and_tpc_status_words": {
            hex(word): all_u32_offsets(data, word) for word in target_words
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmp_rom", type=Path)
    ap.add_argument("v100_rom", type=Path)
    ns = ap.parse_args()
    try:
        result = {
            "scope": "offline static decode; no execution or destination-register semantics claimed",
            "results": [decode("CMP", ns.cmp_rom), decode("V100", ns.v100_rom)],
        }
    except (OSError, ValueError, struct.error) as error:
        ap.exit(1, f"Error: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
