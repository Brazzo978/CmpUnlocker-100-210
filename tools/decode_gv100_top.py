#!/usr/bin/env python3
"""Decode read-only GV100 TOP register snapshots using Nouveau's GK104 rules.

The input is kernel text delimited by lines containing
``cmp100_boot_ro ... BEGIN`` and ``cmp100_boot_ro ... END``.  Register lines
inside a snapshot must contain ``bdf=... reg=0x... value=0x...``.

This decoder follows gk104_top_parse() at Linux commit
5225b8eec4c9bb21aecff6295fab6346a3c3738e.  Validation failures are emitted
in JSON and suppress derived GR reset selectors rather than guessing values.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


SOURCE_COMMIT = "5225b8eec4c9bb21aecff6295fab6346a3c3738e"
TOP_BASE = 0x022700
TOP_WORDS = 64
TOP_END = TOP_BASE + TOP_WORDS * 4
POST_REG = 0x02240C
PMC_ENABLE_REG = 0x000200

PREFIX_RE = re.compile(r"\bcmp100_boot_ro\b", re.IGNORECASE)
BEGIN_RE = re.compile(r"\bBEGIN\b", re.IGNORECASE)
END_RE = re.compile(r"\bEND\b", re.IGNORECASE)
FIELD_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
HEX_RE = re.compile(r"^0[xX]([0-9a-fA-F]+)$")


# Exact ENGINE_TYPE translation in gk104_top_parse().
ENGINE_TYPES: dict[int, tuple[str, int | None]] = {
    0x00: ("GR", 0),
    0x01: ("CE", 0),
    0x02: ("CE", 1),
    0x03: ("CE", 2),
    0x08: ("MSPDEC", 0),
    0x09: ("MSPPP", 0),
    0x0A: ("MSVLD", 0),
    0x0B: ("MSENC", 0),
    0x0C: ("VIC", 0),
    0x0D: ("SEC2", 0),
    0x0E: ("NVENC", None),
    0x0F: ("NVENC", 1),
    0x10: ("NVDEC", None),
    0x12: ("IOCTRL", None),
    0x13: ("CE", None),
    0x14: ("GSP", 0),
    0x15: ("NVJPG", None),
}


def _hex32(value: int) -> str:
    return f"0x{value:08x}"


def _parse_hex32(text: str) -> int:
    match = HEX_RE.fullmatch(text)
    if not match:
        raise ValueError(f"not a hexadecimal integer: {text}")
    value = int(match.group(1), 16)
    if value > 0xFFFFFFFF:
        raise ValueError(f"value exceeds 32 bits: {text}")
    return value


def _fields(line: str) -> dict[str, str]:
    return {key.lower(): value for key, value in FIELD_RE.findall(line)}


def _new_record(index: int) -> dict[str, Any]:
    # Mirrors nvkm_top_device_new() defaults plus parser-local type/inst.
    return {
        "start_index": index,
        "word_indices": [],
        "raw_words": [],
        "engine_type_code": None,
        "source_instance": 0,
        "address": 0,
        "fault": -1,
        "engine": -1,
        "runlist": -1,
        "interrupt": -1,
        "reset": -1,
    }


def _finish_record(record: dict[str, Any], index: int, complete: bool) -> dict[str, Any]:
    code = record["engine_type_code"]
    mapping = ENGINE_TYPES.get(code) if code is not None else None
    source_inst = record["source_instance"]
    if mapping is None:
        name = "UNKNOWN"
        instance = -1
        fixed_instance_warning = False
    else:
        name, fixed = mapping
        instance = source_inst if fixed is None else fixed
        fixed_instance_warning = fixed is not None and source_inst != 0

    return {
        "complete": complete,
        "start_index": record["start_index"],
        "end_index": index,
        "word_indices": record["word_indices"],
        "raw_words": record["raw_words"],
        "engine_type_code": None if code is None else f"0x{code:08x}",
        "type": name,
        "instance": instance,
        "source_instance": source_inst,
        "fixed_instance_warning": fixed_instance_warning,
        "address": _hex32(record["address"]),
        "fault": record["fault"],
        "engine": record["engine"],
        "runlist": record["runlist"],
        "interrupt": record["interrupt"],
        "reset_selector": None if record["reset"] < 0 else record["reset"],
    }


def decode_top_words(words: Iterable[int]) -> dict[str, Any]:
    """Decode exactly 64 TOP words with gk104_top_parse() continuation rules."""

    values = list(words)
    if len(values) != TOP_WORDS:
        raise ValueError(f"expected {TOP_WORDS} TOP words, got {len(values)}")
    if any(value < 0 or value > 0xFFFFFFFF for value in values):
        raise ValueError("TOP words must be unsigned 32-bit integers")

    devices: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    invalid_indices: list[int] = []

    for index, data in enumerate(values):
        kind = data & 0x3
        if kind == 0:  # NOT_VALID continues even when bit 31 is clear.
            invalid_indices.append(index)
            continue

        if current is None:
            current = _new_record(index)
        current["word_indices"].append(index)
        current["raw_words"].append(_hex32(data))

        if kind == 1:  # DATA
            current["source_instance"] = (data & 0x3C000000) >> 26
            current["address"] = data & 0x00FFF000
            if data & 0x4:
                current["fault"] = (data & 0x000003F8) >> 3
        elif kind == 2:  # ENUM
            if data & 0x20:
                current["engine"] = (data & 0x3C000000) >> 26
            if data & 0x10:
                current["runlist"] = (data & 0x01E00000) >> 21
            if data & 0x8:
                current["interrupt"] = (data & 0x000F8000) >> 15
            if data & 0x4:
                current["reset"] = (data & 0x00003E00) >> 9
        else:  # ENGINE_TYPE
            current["engine_type_code"] = (data & 0x7FFFFFFC) >> 2

        if data & 0x80000000:
            continue

        devices.append(_finish_record(current, index, True))
        current = None

    incomplete = None
    if current is not None:
        incomplete = _finish_record(current, TOP_WORDS - 1, False)

    return {
        "devices": devices,
        "incomplete_record": incomplete,
        "invalid_word_indices": invalid_indices,
    }


def _single_register(occurrences: list[dict[str, Any]], reg: int) -> dict[str, Any]:
    found = [item for item in occurrences if item["reg_int"] == reg]
    if len(found) != 1:
        return {
            "value": None,
            "occurrence_count": len(found),
            "lines": [item["line"] for item in found],
        }
    value = found[0]["value_int"]
    return {
        "value": _hex32(found[0]["value_int"]),
        "value_int": value,
        "occurrence_count": 1,
        "line": found[0]["line"],
        "valid": value != 0xFFFFFFFF,
        "invalid_reason": "all_ones_sentinel" if value == 0xFFFFFFFF else None,
    }


def _finalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    occurrences = snapshot.pop("_occurrences")
    by_index: dict[int, list[dict[str, Any]]] = {}
    for item in occurrences:
        reg = item["reg_int"]
        if TOP_BASE <= reg < TOP_END and (reg - TOP_BASE) % 4 == 0:
            by_index.setdefault((reg - TOP_BASE) // 4, []).append(item)

    missing = [index for index in range(TOP_WORDS) if index not in by_index]
    duplicates = []
    for index, items in sorted(by_index.items()):
        if len(items) > 1:
            duplicates.append(
                {
                    "index": index,
                    "register": _hex32(TOP_BASE + 4 * index),
                    "lines": [item["line"] for item in items],
                    "values": [_hex32(item["value_int"]) for item in items],
                }
            )

    unique_complete = not missing and not duplicates
    words = [by_index[index][0]["value_int"] for index in range(TOP_WORDS)] if unique_complete else []
    all_ones_indices = [
        index for index, items in sorted(by_index.items())
        if any(item["value_int"] == 0xFFFFFFFF for item in items)
    ]
    boundary_complete = snapshot["boundary"]["end_line"] is not None
    bdfs = sorted({item["bdf"] for item in occurrences})
    observed_bdf = bdfs[0] if len(bdfs) == 1 else None
    begin_bdf = snapshot.get("begin_fields", {}).get("bdf")
    end_bdf = snapshot.get("end_fields", {}).get("bdf")
    boundary_bdf_match = all(
        boundary_bdf == observed_bdf
        for boundary_bdf in (begin_bdf, end_bdf)
        if boundary_bdf is not None
    )
    input_valid = (
        boundary_complete
        and observed_bdf is not None
        and boundary_bdf_match
        and unique_complete
        and not all_ones_indices
    )

    decoded = decode_top_words(words) if input_valid else {
        "devices": [],
        "incomplete_record": None,
        "invalid_word_indices": [],
    }

    stream_complete = decoded["incomplete_record"] is None
    derived_valid = input_valid and stream_complete
    gr_devices = [item for item in decoded["devices"] if item["type"] == "GR" and item["instance"] == 0]
    gr_reset = None
    gr_mask = None
    derivation = "no_complete_gr_device"
    if derived_valid and len(gr_devices) == 1:
        selector = gr_devices[0]["reset_selector"]
        if selector is None:
            derivation = "complete_gr_device_has_no_reset_selector"
        else:
            gr_reset = selector
            gr_mask = _hex32(1 << selector)
            derivation = "single_complete_gr_device"
    elif derived_valid and len(gr_devices) > 1:
        derivation = "multiple_complete_gr_devices"
    if not derived_valid:
        derivation = "suppressed_by_snapshot_validation"

    post = _single_register(occurrences, POST_REG)
    if post["value"] is not None and post["valid"]:
        bit1 = (post["value_int"] >> 1) & 1
        post.update(
            {
                "bit1": bit1,
                "nouveau_would_run_devinit_now": bit1 == 0,
                "interpretation_scope": "current register state only; does not reconstruct earlier boot history",
            }
        )
        del post["value_int"]

    pmc = _single_register(occurrences, PMC_ENABLE_REG)
    pmc_value = pmc.pop("value_int", None)
    if not pmc.get("valid", False):
        pmc_value = None
    pmc_bit_set_current = None
    if gr_reset is not None and pmc_value is not None:
        pmc_bit_set_current = bool(pmc_value & (1 << gr_reset))

    messages = []
    if not boundary_complete:
        messages.append("snapshot has no END boundary")
    if len(bdfs) != 1:
        messages.append(f"expected one BDF, found {len(bdfs)}")
    if not boundary_bdf_match:
        messages.append("BEGIN/END BDF does not match observed register BDF")
    if missing:
        messages.append(f"missing {len(missing)} TOP words")
    if duplicates:
        messages.append(f"duplicate TOP registers: {len(duplicates)}")
    if all_ones_indices:
        messages.append(f"TOP contains 0xffffffff at {len(all_ones_indices)} indices")
    if post.get("invalid_reason"):
        messages.append("POST register is 0xffffffff")
    if pmc.get("invalid_reason"):
        messages.append("PMC enable register is 0xffffffff")
    if decoded["incomplete_record"] is not None:
        messages.append("TOP stream ends with an incomplete continued record")

    snapshot.update(
        {
            "bdf": observed_bdf,
            "bdfs_seen": bdfs,
            "boundary_bdf": {
                "begin": begin_bdf,
                "end": end_bdf,
                "matches_observed": boundary_bdf_match,
            },
            "register_occurrences": [
                {
                    "line": item["line"],
                    "bdf": item["bdf"],
                    "register": _hex32(item["reg_int"]),
                    "value": _hex32(item["value_int"]),
                }
                for item in occurrences
            ],
            "post_0x2240c": post,
            "pmc_enable_0x200": pmc,
            "top": {
                "base": _hex32(TOP_BASE),
                "expected_words": TOP_WORDS,
                "unique_words": len(by_index),
                "missing_indices": missing,
                "duplicate_registers": duplicates,
                "all_ones_indices": all_ones_indices,
                "input_valid_for_decode": input_valid,
                **decoded,
                "gr": {
                    "complete_devices": gr_devices,
                    "reset_selector": gr_reset,
                    "pmc_mask": gr_mask,
                    "pmc_bit_set_current": pmc_bit_set_current,
                    "derivation": derivation,
                },
            },
            "validation": {
                "valid": derived_valid and not post.get("invalid_reason") and not pmc.get("invalid_reason"),
                "messages": messages,
            },
        }
    )
    return snapshot


def parse_capture(text: str) -> dict[str, Any]:
    snapshots: list[dict[str, Any]] = []
    global_messages: list[str] = []
    current: dict[str, Any] | None = None

    for line_no, raw in enumerate(text.splitlines(), 1):
        if not PREFIX_RE.search(raw):
            continue
        is_begin = bool(BEGIN_RE.search(raw))
        is_end = bool(END_RE.search(raw))

        if is_begin and not is_end:
            if current is not None:
                global_messages.append(f"line {line_no}: BEGIN before previous END")
                snapshots.append(_finalize_snapshot(current))
            current = {
                "snapshot_index": len(snapshots),
                "boundary": {"begin_line": line_no, "end_line": None},
                "begin_fields": _fields(raw),
                "_occurrences": [],
            }
            continue

        if is_end and not is_begin:
            if current is None:
                global_messages.append(f"line {line_no}: END without BEGIN")
            else:
                current["boundary"]["end_line"] = line_no
                current["end_fields"] = _fields(raw)
                snapshots.append(_finalize_snapshot(current))
                current = None
            continue

        fields = _fields(raw)
        if {"bdf", "reg", "value"}.issubset(fields):
            if current is None:
                global_messages.append(f"line {line_no}: register outside BEGIN/END")
                continue
            try:
                reg = _parse_hex32(fields["reg"])
                value = _parse_hex32(fields["value"])
            except ValueError as error:
                global_messages.append(f"line {line_no}: {error}")
                continue
            current["_occurrences"].append(
                {"line": line_no, "bdf": fields["bdf"], "reg_int": reg, "value_int": value}
            )

    if current is not None:
        global_messages.append("end of input before END")
        snapshots.append(_finalize_snapshot(current))

    if not snapshots:
        global_messages.append("no snapshots found")

    return {
        "schema_version": 1,
        "decoder": "gk104_top_parse compatible",
        "nouveau_source_commit": SOURCE_COMMIT,
        "snapshots": snapshots,
        "validation": {"valid": not global_messages and all(s["validation"]["valid"] for s in snapshots),
                       "messages": global_messages},
    }


def _read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="kernel capture text, or - for stdin")
    parser.add_argument("-o", "--output", help="write JSON here instead of stdout")
    args = parser.parse_args(argv)

    result = parse_capture(_read_input(args.input))
    rendered = json.dumps(result, indent=2, sort_keys=False) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
