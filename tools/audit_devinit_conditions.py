#!/usr/bin/env python3
"""Audit CMP devinit condition references against the pinned private SPI image.

Offline only: reads two local files; never accesses PCI, MMIO, flash or network.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import struct
from pathlib import Path

SOURCE_SHA = "86dddefe1b9ab87f5244f2f6f591c93413cae0d4702b3f4d4552969252e9a75b"
TABLE_BASE = 0x52FB  # physical SPI offset, not extracted VBIOS offset
PASTED_OFFSETS = (
    0x001580, 0x001584, 0x00158C, 0x0015B4, 0x00E800, 0x00E820,
    0x00E8D0, 0x0205E4, 0x021110, 0x021228, 0x021288, 0x021290,
    0x0213EC, 0x021C18, 0x021D38, 0x02240C, 0x088000, 0x118C50,
    0x12004C, 0x120064, 0x90BC90, 0x91BC90, 0x92BC90, 0x93BC90,
    0x90BC10, 0x91BC10, 0x92BC10, 0x93BC10,
)


def indices(rows: list[dict[str, str]], opcode: str) -> list[int]:
    found = []
    for row in rows:
        if row["rom"] != "CMP" or row["opcode"] != opcode:
            continue
        match = re.search(r"cond_idx.*?(\d+)", row["first_operand"])
        if not match:
            raise ValueError(f"missing index at {row['file_offset']}")
        found.append(int(match.group(1)))
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-image", type=Path, required=True)
    parser.add_argument("--ir", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    raw = args.source_image.read_bytes()
    if len(raw) != 1 << 20 or hashlib.sha256(raw).hexdigest() != SOURCE_SHA:
        raise ValueError("source is not the pinned original 1 MiB image")
    with args.ir.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    ordinary = indices(rows, "CONDITION")
    polls = indices(rows, "POLL_NV_COND")
    if max(ordinary + polls) >= 85:
        raise ValueError("index exceeds reviewed table range")

    def record(index: int) -> dict[str, int]:
        address, mask, value = struct.unpack_from("<III", raw, TABLE_BASE + 12 * index)
        return {"index": index, "address": address, "mask": mask, "value": value}

    ordinary_records = [record(i) for i in sorted(set(ordinary))]
    poll_records = [record(i) for i in sorted(set(polls))]
    ordinary_registers = {r["address"] for r in ordinary_records}
    poll_registers = {r["address"] for r in poll_records}
    pasted = set(PASTED_OFFSETS)
    missing = sorted(ordinary_registers - pasted)
    capture = sorted(pasted | ordinary_registers | poll_registers)
    if any(address >= 0x1000000 or address % 4 for address in capture):
        raise ValueError("proposed capture contains non-BAR0 or unaligned offset")
    result = {
        "source_sha256": SOURCE_SHA,
        "table_base_physical": TABLE_BASE,
        "ordinary_occurrences_in_ir": len(ordinary),
        "ordinary_unique_indices": len(set(ordinary)),
        "ordinary_unique_registers": len(ordinary_registers),
        "poll_occurrences_in_ir": len(polls),
        "poll_unique_indices": len(set(polls)),
        "pasted_count": len(pasted),
        "missing_ordinary_registers_from_pasted": [f"0x{x:06X}" for x in missing],
        "corrected_capture_count": len(capture),
        "corrected_capture_offsets": [f"0x{x:06X}" for x in capture],
        "ordinary_records": ordinary_records,
        "poll_records": poll_records,
        "note": "Captured post-failure values cannot establish runtime execution order.",
    }
    output = json.dumps(result, indent=2) + "\n"
    if args.out:
        if args.out.exists():
            raise FileExistsError(args.out)
        args.out.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
