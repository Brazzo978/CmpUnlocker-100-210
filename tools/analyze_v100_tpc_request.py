#!/usr/bin/env python3
"""Offline input reconstruction and high-level model of V100 PC 0x2b0d.

Not a Falcon emulator and not a register writer. Assumes six GPC slots,
seven TPC bits per slot, as in the inspected GV100 configuration.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path
from analyze_devinit_tpc_path import ROMS, require
from compare_gv100_init_firmware import inventory


def model(ctrl, count, guard, active_gpcs=0x3f):
    """Reconstruct relevant mask arithmetic, excluding stack/canary machinery."""
    require(len(ctrl) == 6 and 0 <= count <= 255, "Invalid bounded model input")
    masks = [value & 0x7f for value in ctrl]
    counts = [value.bit_count() for value in masks]
    if count == 0:
        return {"reason": "zero request", "writes": {}}
    if count > sum(counts):
        return {"reason": "request exceeds existing CTRL set bits", "writes": {}}
    selected = []
    for _ in range(count):
        # Strict greater-than comparison preserves first index on a tie.
        index = max(range(6), key=lambda i: counts[i])
        masks[index] &= masks[index] - 1
        counts[index] -= 1
        selected.append(index)
    if (guard & 0xff) == 0:
        return {"reason": "low-byte guard zero", "writes": {}, "selected_slots": selected}
    return {"reason": "conditional CTRL writeback", "selected_slots": selected,
            "writes": {hex(0x21838 + 4*i): masks[i] for i in range(6) if active_gpcs & (1 << i)}}


def input_record(label, path):
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == ROMS[label][0], "ROM SHA mismatch")
    app = next(x for x in inventory(label, path)["applications"] if x["application"] == "DEVINIT")
    dmem = int(app["segments"]["dmem"]["file_offset"], 16)
    require(data[dmem+0xa0:dmem+0xa4] == bytes([1,4,8,3]), "Unexpected appinfo header")
    require(struct.unpack_from("<II", data, dmem+0xa4) == (1,0x20), "Missing v1 interface")
    require(struct.unpack_from("<II", data, dmem+0xac) == (1,0x58), "Missing v2 interface")
    require(struct.unpack_from("<HH", data, dmem+0x58) == (2,0x10), "Unexpected v2 interface")
    table_physical, script_physical = struct.unpack_from("<II", data, dmem+0x60)
    require(struct.unpack_from("<I", data, dmem+0x28)[0] == table_physical, "Table base mismatch")
    require(struct.unpack_from("<I", data, dmem+0x30)[0] == script_physical, "Script base mismatch")
    pointer, size = struct.unpack_from("<HH", data, 0xc83+24)
    start = pointer + 0xa00
    header_offset = struct.unpack_from("<H", data, start+2)[0]
    require(header_offset == 0x10 and size > header_offset + 8, "Unexpected script header")
    header = start + header_offset
    return {"rom": label, "sha256": ROMS[label][0], "bootscripts_file_offset": hex(start),
            "bootscripts_dmem_base": hex(script_physical), "secondary_header_offset": hex(header_offset),
            "header_pointer_in_v100_dmem_558": hex(script_physical+header_offset),
            "request_byte_file_offset": hex(header+7), "request_byte_value": data[header+7],
            "secondary_header_first_8_bytes": data[header:header+8].hex(),
            "scope": "Stored bytes and V100 caller mapping; no assertion of unmodified runtime memory or CMP code parity"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmp_rom", type=Path)
    parser.add_argument("v100_rom", type=Path)
    args = parser.parse_args()
    examples = [
        ("observed zero CTRL / stored zero request", [0]*6, 0, 1),
        ("hypothetical request six / observed zero CTRL", [0]*6, 6, 1),
        ("synthetic CTRL bit present / clear one", [3,0,0,0,0,0], 1, 1),
        ("same synthetic input / guard low byte zero", [3,0,0,0,0,0], 1, 0x100),
    ]
    print(json.dumps({"scope": "Offline reconstruction, no hardware execution",
        "inputs": [input_record("CMP", args.cmp_rom), input_record("V100", args.v100_rom)],
        "model_examples": [{"name": name, "ctrl": ctrl, "request": n, "guard": guard,
                            "result": model(ctrl,n,guard)} for name,ctrl,n,guard in examples]}, indent=2))


if __name__ == "__main__":
    main()
