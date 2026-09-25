#!/usr/bin/env python3
"""Compare recorded GV100 physical STATUS with logical GR topology, offline.

Input is the archived before/after register JSON, not a device or a firmware.
No hardware access. Matching counts do not demonstrate execution or recovery.
"""
import argparse
import json
from pathlib import Path


def analyze(regs):
    def read(address):
        value = int(regs[f"0x{address:06x}"], 16)
        if value == 0xffffffff:
            raise ValueError(f"Invalid all-ones register at {address:#x}")
        return value

    gpc_disable = read(0x21c1c) & 0x3f
    physical = []
    for g in range(6):
        disabled = read(0x21c38 + 4*g) & 0x7f
        enabled = 0 if gpc_disable & (1 << g) else ~disabled & 0x7f
        physical.append({"physical_gpc": g, "gpc_disabled": bool(gpc_disable & (1 << g)),
                         "status_disable_mask": hex(disabled),
                         "enabled_mask": hex(enabled), "tpcs": enabled.bit_count()})

    logical = []
    gpc_count = read(0x409604) & 0x1f
    if not 1 <= gpc_count <= 6:
        raise ValueError("Invalid recorded logical GPC count")
    for g in range(gpc_count):
        # GV100 hw_gr: NUM_AVAILABLE_TPCS is [4:0]; ZCULL count is separate.
        count = read(0x502608 + 0x8000*g) & 0x1f
        masks = [read(0x500c30 + 0x8000*g + 4*p) for p in range(3)]
        if not 1 <= count <= 7 or any(m & ~0x7f for m in masks):
            raise ValueError("Invalid recorded TPC count/PPC mask")
        union = masks[0] | masks[1] | masks[2]
        disjoint = all(not (masks[a] & masks[b]) for a in range(3) for b in range(a+1, 3))
        logical.append({"logical_gpc": g, "tpcs": count,
                        "ppc_masks": [hex(m) for m in masks], "ppc_union": hex(union),
                        "disjoint": disjoint, "count_matches_union": count == union.bit_count(),
                        "union_is_low_contiguous": union == (1 << count) - 1})

    physical_total = sum(g["tpcs"] for g in physical)
    logical_total = sum(g["tpcs"] for g in logical)
    return {"physical": physical, "logical": logical,
            "physical_tpcs": physical_total, "logical_tpcs": logical_total,
            "counts_agree": physical_total == logical_total,
            "inferred_sms": 2*logical_total,
            "compute_control": hex(read(0x409664)),
            "scope": "Historical snapshot; indexing correspondence is not inferred"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    captures = json.loads(args.capture.read_text(encoding="utf-8"))
    result = {phase: {bdf: analyze(regs) for bdf, regs in captures[phase].items()}
              for phase in ("before", "after")}
    for cards in result.values():
        for card in cards.values():
            if not card["counts_agree"] or not all(g["count_matches_union"] and g["disjoint"] for g in card["logical"]):
                raise ValueError("Topology stages disagree; inspect original capture")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
