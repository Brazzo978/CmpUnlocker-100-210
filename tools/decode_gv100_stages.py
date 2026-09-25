#!/usr/bin/env python3
"""Parse and validate cmp100_stage_ro read-only lifecycle captures.

The parser is deliberately conservative.  It preserves every parsed record,
but marks a run invalid when a boundary is missing or ambiguous, a probe
reported loss, or the collector itself marked a sample invalid.  Mask-change
results describe only the emitted stage observations; they cannot exclude a
change and restoration between two observations.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_LIVE_GR_TYPE = 34
PREFIX = "cmp100_stage_ro"
PMC_REG = 0x000200
POST_REG = 0x02240C
GPC_STATUS_REG = 0x021C1C
LOGICAL_GPC_COUNT_REG = 0x409604
COMPUTE_PIPE_CONTROL_REG = 0x409664
GPC_TPC_COUNT_BASE = 0x502608
PPC_TPC_MASK_BASE = 0x500C30
GPC_STRIDE = 0x8000
MAX_GPCS = 6
PPCS_PER_GPC = 3

RAW_REGS = tuple(0x21760 + 4 * slot for slot in range(MAX_GPCS))
CTRL_REGS = tuple(0x21838 + 4 * slot for slot in range(MAX_GPCS))
STATUS_REGS = tuple(0x21C38 + 4 * slot for slot in range(MAX_GPCS))
PHYSICAL_REGS = (PMC_REG, POST_REG, GPC_STATUS_REG) + tuple(
    reg
    for slot in range(MAX_GPCS)
    for reg in (RAW_REGS[slot], CTRL_REGS[slot], STATUS_REGS[slot])
)

CORE_SEQUENCE = (
    "nvidia_baseline",
    "post_entry",
    "post_return",
    "gr_reset_mask_return",
    "gr_reset_return",
    "oneinit_entry",
    "oneinit_return",
    "ctxctl_entry",
    "ctxctl_return",
)
NONRESET_SEQUENCE = tuple(
    name for name in CORE_SEQUENCE if name not in {"gr_reset_mask_return", "gr_reset_return"}
)
LOGICAL_STAGES = {
    "nvidia_baseline",
    "oneinit_return",
    "ctxctl_entry",
    "ctxctl_return",
}
MISSED_SYMBOLS = {
    "nvkm_devinit_post",
    "nvkm_mc_reset",
    "nvkm_mc_reset_mask",
    "gf100_gr_oneinit",
    "gf100_gr_init_ctxctl_ext",
}

FIELD_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
HEX_RE = re.compile(r"^0[xX][0-9a-fA-F]+$")


def _fields(line: str) -> dict[str, str]:
    return {key.lower(): value for key, value in FIELD_RE.findall(line)}


def _integer(value: str, field: str) -> int:
    try:
        return int(value, 16 if HEX_RE.fullmatch(value) else 10)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field} value {value!r}") from exc


def _hex(reg_or_value: int, digits: int = 8) -> str:
    return f"0x{reg_or_value:0{digits}x}"


def _error(errors: list[dict[str, Any]], code: str, **details: Any) -> None:
    errors.append({"code": code, **details})


def _new_run(line_no: int, line: str, fields: dict[str, str]) -> dict[str, Any]:
    run: dict[str, Any] = {
        "begin_line": line_no,
        "end_line": None,
        "begin_bdf": fields.get("bdf"),
        "end_bdf": None,
        "declared_records": None,
        "dropped": None,
        "pmu_present": None,
        "stages": [],
        "missed": [],
        "orphan_values": [],
        "unparsed_lines": [],
        "raw_lines": [line],
        "delimiter_complete": False,
    }
    try:
        run["declared_records"] = _integer(fields["records"], "records")
        run["dropped"] = _integer(fields["dropped"], "dropped")
    except (KeyError, ValueError) as exc:
        run["unparsed_lines"].append(
            {"line": line_no, "kind": "BEGIN", "reason": str(exc), "raw": line}
        )
    if "pmu_present" in fields:
        try:
            run["pmu_present"] = _integer(fields["pmu_present"], "pmu_present")
        except ValueError as exc:
            run["unparsed_lines"].append(
                {"line": line_no, "kind": "BEGIN", "reason": str(exc), "raw": line}
            )
    return run


def _parse_stage(line_no: int, line: str, fields: dict[str, str]) -> dict[str, Any]:
    required = ("seq", "name", "start_ns", "end_ns", "pid", "rc", "post", "invalid")
    missing = [name for name in required if name not in fields]
    if missing:
        raise ValueError("missing STAGE fields: " + ", ".join(missing))
    return {
        "line": line_no,
        "seq": _integer(fields["seq"], "seq"),
        "name": fields["name"],
        "start_ns": _integer(fields["start_ns"], "start_ns"),
        "end_ns": _integer(fields["end_ns"], "end_ns"),
        "pid": _integer(fields["pid"], "pid"),
        "rc": _integer(fields["rc"], "rc"),
        "post": _integer(fields["post"], "post"),
        "invalid": _integer(fields["invalid"], "invalid"),
        "values": [],
        "raw": line,
    }


def _parse_value(line_no: int, line: str, fields: dict[str, str]) -> dict[str, Any]:
    required = ("seq", "reg", "value")
    missing = [name for name in required if name not in fields]
    if missing:
        raise ValueError("missing VALUE fields: " + ", ".join(missing))
    reg = _integer(fields["reg"], "reg")
    value = _integer(fields["value"], "value")
    if not (0 <= reg <= 0xFFFFFFFF and 0 <= value <= 0xFFFFFFFF):
        raise ValueError("VALUE reg/value exceeds 32 bits")
    return {
        "line": line_no,
        "seq": _integer(fields["seq"], "seq"),
        "reg": _hex(reg, 6),
        "value": _hex(value),
        "raw": line,
    }


def _parse_missed(line_no: int, line: str, fields: dict[str, str]) -> dict[str, Any]:
    if "symbol" not in fields or "nmissed" not in fields:
        raise ValueError("missing MISSED symbol or nmissed")
    return {
        "line": line_no,
        "symbol": fields["symbol"],
        "nmissed": _integer(fields["nmissed"], "nmissed"),
        "raw": line,
    }


def _value_map(stage: dict[str, Any]) -> tuple[dict[int, int], list[int]]:
    values: dict[int, int] = {}
    duplicates: list[int] = []
    for item in stage["values"]:
        reg = int(item["reg"], 16)
        value = int(item["value"], 16)
        if reg in values:
            duplicates.append(reg)
        else:
            values[reg] = value
    return values, duplicates


def _mask_observations(stages: list[dict[str, Any]]) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    for stage in sorted(stages, key=lambda item: item["seq"]):
        values, _ = _value_map(stage)
        families: dict[str, list[str | None]] = {}
        for name, regs in (("raw", RAW_REGS), ("ctrl", CTRL_REGS), ("status", STATUS_REGS)):
            families[name] = [None if reg not in values else _hex(values[reg]) for reg in regs]
        observations.append({"seq": stage["seq"], "stage": stage["name"], **families})

    transitions: list[dict[str, Any]] = []
    for before, after in zip(observations, observations[1:]):
        changed: dict[str, list[int]] = {}
        for family in ("raw", "ctrl", "status"):
            changed[family] = [
                index
                for index, (old, new) in enumerate(zip(before[family], after[family]))
                if old is not None and new is not None and old != new
            ]
        transitions.append(
            {
                "from_seq": before["seq"],
                "from_stage": before["stage"],
                "to_seq": after["seq"],
                "to_stage": after["stage"],
                "changed_slots": changed,
                "changed": any(changed.values()),
            }
        )
    return {
        "observations": observations,
        "transitions": transitions,
        "any_observed_change": any(item["changed"] for item in transitions),
        "scope": (
            "Compares emitted stage observations only; changes that occur and are restored "
            "between observations are unobserved."
        ),
    }


def _validate_run(run: dict[str, Any]) -> None:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not run["delimiter_complete"]:
        _error(errors, "missing_end")
    if not run["begin_bdf"]:
        _error(errors, "missing_begin_bdf")
    if run["delimiter_complete"] and run["begin_bdf"] != run["end_bdf"]:
        _error(
            errors,
            "bdf_mismatch",
            begin_bdf=run["begin_bdf"],
            end_bdf=run["end_bdf"],
        )
    if run["declared_records"] is None:
        _error(errors, "missing_declared_record_count")
    elif run["declared_records"] != len(run["stages"]):
        _error(
            errors,
            "record_count_mismatch",
            declared=run["declared_records"],
            parsed=len(run["stages"]),
        )
    if run["dropped"] is None:
        _error(errors, "missing_dropped_count")
    elif run["dropped"] != 0:
        _error(errors, "records_dropped", dropped=run["dropped"])
    if run["pmu_present"] not in (None, -1, 0, 1):
        _error(errors, "invalid_pmu_present", observed=run["pmu_present"])
    if run["unparsed_lines"]:
        _error(errors, "unparsed_capture_lines", count=len(run["unparsed_lines"]))
    if run["orphan_values"]:
        _error(errors, "orphan_values", count=len(run["orphan_values"]))

    observed_armed = run.get("armed_live_gr_type")
    if observed_armed is not None:
        if observed_armed != EXPECTED_LIVE_GR_TYPE:
            _error(
                errors,
                "unexpected_live_gr_type",
                expected=EXPECTED_LIVE_GR_TYPE,
                observed=observed_armed,
            )
    else:
        warnings.append(
            {
                "code": "armed_line_not_present",
                "expected_live_gr_type": EXPECTED_LIVE_GR_TYPE,
            }
        )

    seqs = [stage["seq"] for stage in run["stages"]]
    if len(seqs) != len(set(seqs)):
        _error(errors, "duplicate_stage_seq", seqs=sorted({seq for seq in seqs if seqs.count(seq) > 1}))
    if seqs != list(range(len(seqs))):
        _error(errors, "noncontiguous_stage_seq", observed=seqs)

    by_name: dict[str, list[dict[str, Any]]] = {}
    previous_start: int | None = None
    for stage in run["stages"]:
        by_name.setdefault(stage["name"], []).append(stage)
        if stage["start_ns"] > stage["end_ns"]:
            _error(errors, "invalid_stage_time", seq=stage["seq"], stage=stage["name"])
        if previous_start is not None and stage["start_ns"] < previous_start:
            _error(errors, "nonmonotonic_stage_time", seq=stage["seq"], stage=stage["name"])
        previous_start = stage["start_ns"]
        if stage["invalid"] != 0:
            invalid_flags = []
            if stage["invalid"] & 1:
                invalid_flags.append("all_ones_value")
            if stage["invalid"] & 2:
                invalid_flags.append("value_buffer_overflow")
            if stage["invalid"] & 4:
                invalid_flags.append("bad_gpc_count_or_missing_pmc_gr_bit")
            unknown_invalid = stage["invalid"] & ~7
            if unknown_invalid:
                invalid_flags.append(f"unknown_bits_{_hex(unknown_invalid)}")
            _error(
                errors,
                "collector_invalid",
                seq=stage["seq"],
                stage=stage["name"],
                invalid=stage["invalid"],
                flags=invalid_flags,
            )
        if stage["rc"] < 0:
            _error(errors, "negative_return", seq=stage["seq"], stage=stage["name"], rc=stage["rc"])

        values, duplicates = _value_map(stage)
        if duplicates:
            _error(
                errors,
                "duplicate_register",
                seq=stage["seq"],
                stage=stage["name"],
                registers=[_hex(reg, 6) for reg in sorted(set(duplicates))],
            )
        all_ones = [_hex(reg, 6) for reg, value in values.items() if value == 0xFFFFFFFF]
        if all_ones:
            _error(
                errors,
                "all_ones_value",
                seq=stage["seq"],
                stage=stage["name"],
                registers=all_ones,
            )
        missing_physical = [_hex(reg, 6) for reg in PHYSICAL_REGS if reg not in values]
        if missing_physical:
            _error(
                errors,
                "missing_physical_registers",
                seq=stage["seq"],
                stage=stage["name"],
                registers=missing_physical,
            )

        logical_present = any(
            reg == LOGICAL_GPC_COUNT_REG
            or reg == COMPUTE_PIPE_CONTROL_REG
            or (PPC_TPC_MASK_BASE <= reg < PPC_TPC_MASK_BASE + MAX_GPCS * GPC_STRIDE)
            or (GPC_TPC_COUNT_BASE <= reg < GPC_TPC_COUNT_BASE + MAX_GPCS * GPC_STRIDE)
            for reg in values
        )
        if stage["name"] not in LOGICAL_STAGES and logical_present:
            _error(errors, "logical_values_at_unsafe_stage", seq=stage["seq"], stage=stage["name"])

        if stage["name"] in LOGICAL_STAGES:
            if PMC_REG in values and not (values[PMC_REG] & 0x1000):
                _error(errors, "pmc_gr_bit_clear", seq=stage["seq"], stage=stage["name"])
            if LOGICAL_GPC_COUNT_REG not in values:
                _error(errors, "missing_logical_gpc_count", seq=stage["seq"], stage=stage["name"])
                continue
            gpc_count = values[LOGICAL_GPC_COUNT_REG] & 0x1F
            if not 1 <= gpc_count <= MAX_GPCS:
                _error(
                    errors,
                    "invalid_logical_gpc_count",
                    seq=stage["seq"],
                    stage=stage["name"],
                    count=gpc_count,
                )
                continue
            if COMPUTE_PIPE_CONTROL_REG not in values:
                _error(
                    errors,
                    "missing_compute_pipe_control",
                    seq=stage["seq"],
                    stage=stage["name"],
                )
            tpc_counts: list[int] = []
            missing_counts: list[str] = []
            missing_ppc: list[str] = []
            for gpc in range(gpc_count):
                count_reg = GPC_TPC_COUNT_BASE + GPC_STRIDE * gpc
                if count_reg not in values:
                    missing_counts.append(_hex(count_reg, 6))
                else:
                    # gf100_gr_oneinit stores this register into the u8 tpc_nr field.
                    tpc_counts.append(values[count_reg] & 0xFF)
                for ppc in range(PPCS_PER_GPC):
                    ppc_reg = PPC_TPC_MASK_BASE + GPC_STRIDE * gpc + 4 * ppc
                    if ppc_reg not in values:
                        missing_ppc.append(_hex(ppc_reg, 6))
            if missing_counts:
                _error(
                    errors,
                    "missing_gpc_tpc_counts",
                    seq=stage["seq"],
                    stage=stage["name"],
                    registers=missing_counts,
                )
            if missing_ppc:
                _error(
                    errors,
                    "missing_ppc_masks",
                    seq=stage["seq"],
                    stage=stage["name"],
                    registers=missing_ppc,
                )
            stage["logical_topology"] = {
                "gpc_count": gpc_count,
                "tpc_counts_low_byte": tpc_counts,
                "tpc_count_sum": None if missing_counts else sum(tpc_counts),
                "ppc_masks_complete": not missing_ppc,
            }

    actual_core: list[tuple[str, int]] = []
    unique_stages: dict[str, dict[str, Any]] = {}
    for name in NONRESET_SEQUENCE:
        matches = by_name.get(name, [])
        if not matches:
            _error(errors, "missing_stage", stage=name)
        elif len(matches) > 1:
            _error(errors, "duplicate_stage", stage=name, seqs=[item["seq"] for item in matches])
        else:
            unique_stages[name] = matches[0]
            actual_core.append((name, matches[0]["seq"]))

    reset_classification: dict[str, Any] = {
        "initial_pair": None,
        "post_ctxctl_pairs": [],
        "unclassified": [],
    }
    if "post_return" in unique_stages and "oneinit_entry" in unique_stages:
        low = unique_stages["post_return"]["seq"]
        high = unique_stages["oneinit_entry"]["seq"]
        masks = [
            item for item in by_name.get("gr_reset_mask_return", []) if low < item["seq"] < high
        ]
        resets = [
            item for item in by_name.get("gr_reset_return", []) if low < item["seq"] < high
        ]
        if len(masks) != 1 or len(resets) != 1 or masks[0]["seq"] + 1 != resets[0]["seq"]:
            _error(
                errors,
                "ambiguous_initial_reset",
                mask_seqs=[item["seq"] for item in masks],
                reset_seqs=[item["seq"] for item in resets],
            )
        else:
            reset_classification["initial_pair"] = {
                "mask_seq": masks[0]["seq"],
                "reset_seq": resets[0]["seq"],
            }
            actual_core.extend(
                [
                    ("gr_reset_mask_return", masks[0]["seq"]),
                    ("gr_reset_return", resets[0]["seq"]),
                ]
            )
    else:
        _error(errors, "initial_reset_window_unavailable")

    if "ctxctl_return" in unique_stages:
        ctxctl_return_seq = unique_stages["ctxctl_return"]["seq"]
        trailing = sorted(
            [
                item
                for name in ("gr_reset_mask_return", "gr_reset_return")
                for item in by_name.get(name, [])
                if item["seq"] > ctxctl_return_seq
            ],
            key=lambda item: item["seq"],
        )
        cursor = 0
        while cursor < len(trailing):
            pair = trailing[cursor : cursor + 2]
            if (
                len(pair) == 2
                and pair[0]["name"] == "gr_reset_mask_return"
                and pair[1]["name"] == "gr_reset_return"
                and pair[0]["seq"] + 1 == pair[1]["seq"]
            ):
                reset_classification["post_ctxctl_pairs"].append(
                    {"mask_seq": pair[0]["seq"], "reset_seq": pair[1]["seq"]}
                )
                cursor += 2
            else:
                reset_classification["unclassified"].extend(item["seq"] for item in pair)
                cursor += max(1, len(pair))

    classified_reset_seqs = set()
    if reset_classification["initial_pair"]:
        classified_reset_seqs.update(reset_classification["initial_pair"].values())
    for pair in reset_classification["post_ctxctl_pairs"]:
        classified_reset_seqs.update(pair.values())
    all_reset_seqs = {
        item["seq"]
        for name in ("gr_reset_mask_return", "gr_reset_return")
        for item in by_name.get(name, [])
    }
    unclassified = sorted(all_reset_seqs - classified_reset_seqs)
    reset_classification["unclassified"] = sorted(
        set(reset_classification["unclassified"]) | set(unclassified)
    )
    if reset_classification["unclassified"]:
        _error(errors, "unclassified_reset_events", seqs=reset_classification["unclassified"])
    run["reset_classification"] = reset_classification

    if len(actual_core) == len(CORE_SEQUENCE):
        observed_names = [name for name, _ in sorted(actual_core, key=lambda item: item[1])]
        if observed_names != list(CORE_SEQUENCE):
            _error(errors, "stage_order", expected=list(CORE_SEQUENCE), observed=observed_names)

    expected_rc = {
        "nvidia_baseline": 0,
        "post_entry": 0,
        "post_return": 0,
        "oneinit_entry": 0,
        "oneinit_return": 0,
        "ctxctl_entry": 0,
        "ctxctl_return": 0,
    }
    for name, wanted in expected_rc.items():
        matches = by_name.get(name, [])
        if len(matches) == 1 and matches[0]["rc"] != wanted:
            _error(
                errors,
                "unexpected_return",
                seq=matches[0]["seq"],
                stage=name,
                expected=wanted,
                observed=matches[0]["rc"],
            )

    post_entry = unique_stages.get("post_entry")
    post_return = unique_stages.get("post_return")
    if post_entry is not None and post_return is not None:
        for stage in (post_entry, post_return):
            if stage["post"] not in (0, 1):
                _error(
                    errors,
                    "post_value_not_boolean",
                    seq=stage["seq"],
                    stage=stage["name"],
                    observed=stage["post"],
                )
        if post_entry["post"] != post_return["post"]:
            _error(
                errors,
                "post_value_mismatch",
                entry=post_entry["post"],
                returned=post_return["post"],
            )
        if 1 in (post_entry["post"], post_return["post"]) and run["pmu_present"] != 1:
            _error(
                errors,
                "forced_post_requires_pmu",
                pmu_present=run["pmu_present"],
            )
    for stage in by_name.get("gr_reset_mask_return", []):
        if stage["rc"] != 0x1000:
            _error(
                errors,
                "unexpected_return",
                seq=stage["seq"],
                stage=stage["name"],
                expected=0x1000,
                observed=stage["rc"],
            )
    for stage in by_name.get("gr_reset_return", []):
        if stage["rc"] != 0:
            _error(
                errors,
                "unexpected_return",
                seq=stage["seq"],
                stage=stage["name"],
                expected=0,
                observed=stage["rc"],
            )

    missed_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for item in run["missed"]:
        missed_by_symbol.setdefault(item["symbol"], []).append(item)
        if item["nmissed"] != 0:
            _error(errors, "probe_missed", symbol=item["symbol"], nmissed=item["nmissed"])
    for symbol in sorted(MISSED_SYMBOLS):
        matches = missed_by_symbol.get(symbol, [])
        if not matches:
            _error(errors, "missing_missed_counter", symbol=symbol)
        elif len(matches) > 1:
            _error(errors, "duplicate_missed_counter", symbol=symbol, count=len(matches))

    mask_changes = _mask_observations(run["stages"])
    run["mask_comparison"] = mask_changes
    run["validation"] = {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "expected_live_gr_type": EXPECTED_LIVE_GR_TYPE,
        "mask_interpretation_valid": not errors,
    }


def parse_capture(text: str) -> dict[str, Any]:
    """Parse all capture blocks and select the latest delimiter-complete run."""

    runs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    armed_types: dict[str, int] = {}

    for line_no, raw in enumerate(text.splitlines(), 1):
        if PREFIX not in raw:
            continue
        fields = _fields(raw)
        marker = raw.split(PREFIX, 1)[1].strip().split(maxsplit=1)[0] if raw.split(PREFIX, 1)[1].strip() else ""

        if marker == "ARMED":
            try:
                armed_types[fields["bdf"]] = _integer(fields["live_gr_type"], "live_gr_type")
            except (KeyError, ValueError):
                pass
            continue
        if marker == "BEGIN":
            if current is not None:
                _validate_run(current)
                runs.append(current)
            current = _new_run(line_no, raw, fields)
            current["armed_live_gr_type"] = armed_types.get(current["begin_bdf"])
            continue
        if current is None:
            continue

        current["raw_lines"].append(raw)
        if marker == "STAGE":
            try:
                current["stages"].append(_parse_stage(line_no, raw, fields))
            except ValueError as exc:
                current["unparsed_lines"].append(
                    {"line": line_no, "kind": "STAGE", "reason": str(exc), "raw": raw}
                )
        elif marker == "VALUE":
            try:
                value = _parse_value(line_no, raw, fields)
                candidates = [stage for stage in current["stages"] if stage["seq"] == value["seq"]]
                if len(candidates) == 1:
                    candidates[0]["values"].append(value)
                else:
                    current["orphan_values"].append(value)
            except ValueError as exc:
                current["unparsed_lines"].append(
                    {"line": line_no, "kind": "VALUE", "reason": str(exc), "raw": raw}
                )
        elif marker == "MISSED":
            try:
                current["missed"].append(_parse_missed(line_no, raw, fields))
            except ValueError as exc:
                current["unparsed_lines"].append(
                    {"line": line_no, "kind": "MISSED", "reason": str(exc), "raw": raw}
                )
        elif marker == "END":
            current["end_line"] = line_no
            current["end_bdf"] = fields.get("bdf")
            current["delimiter_complete"] = True
            _validate_run(current)
            runs.append(current)
            current = None
        else:
            current["unparsed_lines"].append(
                {"line": line_no, "kind": marker or "unknown", "reason": "unknown record", "raw": raw}
            )

    if current is not None:
        _validate_run(current)
        runs.append(current)

    complete = [index for index, run in enumerate(runs) if run["delimiter_complete"]]
    selected_index = complete[-1] if complete else None
    return {
        "format": "cmp100_stage_ro-v1",
        "expected_live_gr_type": EXPECTED_LIVE_GR_TYPE,
        "run_count": len(runs),
        "latest_complete_run_index": selected_index,
        "runs": runs,
    }


def _read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default="-", help="kernel log file, or - for stdin")
    parser.add_argument("--all", action="store_true", help="emit every parsed run")
    args = parser.parse_args(argv)

    result = parse_capture(_read_input(args.input))
    index = result["latest_complete_run_index"]
    if args.all:
        output: Any = result
    else:
        output = {
            "format": result["format"],
            "expected_live_gr_type": result["expected_live_gr_type"],
            "run_count": result["run_count"],
            "selected_run_index": index,
            "selected_run": None if index is None else result["runs"][index],
        }
    json.dump(output, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    if index is None:
        return 2
    selected = output.get("selected_run") if isinstance(output, dict) else None
    if not args.all and selected is not None and not selected["validation"]["valid"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
