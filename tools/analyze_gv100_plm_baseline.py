#!/usr/bin/env python3
"""Validate and summarize a two-card cmp100_plm_ro capture."""

import argparse
import hashlib
import json
from pathlib import Path


BDFS = ("0000:01:00.0", "0000:02:00.0")
FIELDS = ("match", "mask", "data1", "data2", "action", "plm")


def fields(line: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in line.split() if "=" in part)


def analyze(data: bytes) -> dict:
    lines = data.decode("utf-8").splitlines()
    traps: dict[str, dict[int, dict[int, dict[str, str]]]] = {}
    extra: dict[str, dict[int, dict[str, str]]] = {}
    begin: set[str] = set()
    end: dict[str, dict[str, str]] = {}
    for line in lines:
        if "cmp100_plm_ro " not in line:
            continue
        payload = line.split("cmp100_plm_ro ", 1)[1]
        kind, _, tail = payload.partition(" ")
        item = fields(tail)
        bdf = item.get("bdf")
        if bdf not in BDFS:
            raise ValueError(f"unexpected BDF in {line}")
        if kind == "BEGIN":
            if bdf in begin:
                raise ValueError(f"duplicate BEGIN {bdf}")
            begin.add(bdf)
            if item.get("pci") != "10de:1d84" or item.get("driver") != "nvidia":
                raise ValueError(f"identity/binding failed {bdf}")
        elif kind == "TRAP":
            passage, slot = int(item["pass"]), int(item["slot"])
            if passage not in (0, 1) or not 0 <= slot < 22:
                raise ValueError(f"invalid pass/slot {bdf}: {passage}/{slot}")
            values = {field: item[field] for field in FIELDS}
            slots = traps.setdefault(bdf, {}).setdefault(passage, {})
            if slot in slots:
                raise ValueError(f"duplicate trap {bdf}/{passage}/{slot}")
            slots[slot] = values
        elif kind == "EXTRA":
            passage = int(item["pass"])
            if passage not in (0, 1) or passage in extra.setdefault(bdf, {}):
                raise ValueError(f"duplicate/invalid EXTRA {bdf}/{passage}")
            extra[bdf][passage] = item
            if item["boot_before"] != "0x140000a1" or item["boot_after"] != "0x140000a1":
                raise ValueError(f"BOOT0 failed {bdf}/{passage}")
        elif kind == "END":
            if bdf in end:
                raise ValueError(f"duplicate END {bdf}")
            end[bdf] = item
    if begin != set(BDFS) or set(end) != set(BDFS):
        raise ValueError("both exact-BDF BEGIN/END markers are required")
    for bdf in BDFS:
        if set(traps[bdf]) != {0, 1} or set(extra[bdf]) != {0, 1}:
            raise ValueError(f"two passes required {bdf}")
        for passage in (0, 1):
            if set(traps[bdf][passage]) != set(range(22)):
                raise ValueError(f"all 22 slots required {bdf}/{passage}")
        if end[bdf].get("passes") != "2" or end[bdf].get("traps") != "22" or end[bdf].get("hardware_writes") != "0":
            raise ValueError(f"incomplete or write-capable capture {bdf}")
    stability = {
        bdf: traps[bdf][0] == traps[bdf][1] and
        all(extra[bdf][0][k] == extra[bdf][1][k] for k in
            ("fecs_plm_409650", "tensor_409664", "scratch30_15f8", "scratch31_15fc"))
        for bdf in BDFS
    }
    differences = {
        str(slot): {bdf: traps[bdf][0][slot] for bdf in BDFS}
        for slot in range(22)
        if traps[BDFS[0]][0][slot] != traps[BDFS[1]][0][slot]
    }
    return {
        "schema": 1,
        "capture_sha256": hashlib.sha256(data).hexdigest(),
        "bdfs": list(BDFS),
        "passes_per_card": 2,
        "slots_per_pass": 22,
        "stable_both_passes": stability,
        "cross_card_trap_differences": differences,
        "trap20": {bdf: traps[bdf][0][20] for bdf in BDFS},
        "fecs_plm_409650": {bdf: extra[bdf][0]["fecs_plm_409650"] for bdf in BDFS},
        "tensor_409664": {bdf: extra[bdf][0]["tensor_409664"] for bdf in BDFS},
        "scratch": {bdf: {
            "30": extra[bdf][0]["scratch30_15f8"],
            "31": extra[bdf][0]["scratch31_15fc"],
        } for bdf in BDFS},
        "all_traps_pass0": {bdf: traps[bdf][0] for bdf in BDFS},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    result = analyze(args.capture.read_bytes())
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "all_traps_pass0"}, indent=2))


if __name__ == "__main__":
    main()
