#!/usr/bin/env python3
"""Compare bounded TPC CTRL blocks in two hash-pinned GV100 ROM dumps.

Offline only. Does not establish boot-time values or execution reachability.
ROM files are inputs, not distributed with this tool.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

ROMS = {
    "CMP": ("e77a5ca44212927a06004fd9910d5456302750e11a773c84817e8fef5ee8e46b", 0x80D2, 0x8CD0),
    "V100": ("c45cf4cca3531de82b7b1a587b81f9f31d58e5589fafad0bb431bb95d89d61d8", 0x807D, 0x8C5C),
}
SPEC = "https://github.com/NVIDIA/open-gpu-doc/blob/9fdf5c4062007929d9f4e6cbad9c9771fe61b880/Devinit/devinit.xml"


def condition_skip(incoming_skip, register_value, mask, compare):
    """0xAC failsets: a match preserves state; only RESUME clears skip."""
    return bool(incoming_skip or (register_value & mask) != compare)


def direct_condition(data, offset):
    require(0 <= offset and offset + 13 <= len(data), "Truncated direct condition")
    require(data[offset] == 0xAC, "Expected NV_REG_CONDITION_DIRECT")
    register, mask, value = struct.unpack_from("<III", data, offset + 1)
    return {"file_offset": hex(offset), "register": hex(register),
            "mask": hex(mask), "value": hex(value), "access": "read",
            "condition_effect": "mismatch sets skip; match preserves incoming condition"}


def guarded_family(data, second, label):
    """Adjacent, directly called subscripts. No global flow simulation."""
    parent, call_start, count = (0x8C87, 0x8C89, 5) if label == "CMP" else (0x8C25, 0x8C4E, 4)
    require(data[parent] == 0x75, "Expected parent CONDITION")
    end = call_start + 3 * count
    require(data[end:end + 2] == b"\x72\x71", "Bad parent boundary")
    inline = []
    pos = parent + 2
    while pos < call_start:
        require(data[pos] == 0x6E, "Expected parent NV_REG")
        reg, mask, value = struct.unpack_from("<III", data, pos + 1)
        inline.append({"register": hex(reg), "and_mask": hex(mask), "or_mask": hex(value)})
        pos += 13
    require(pos == call_start, "Misaligned parent call boundary")
    blocks = []
    for i in range(count):
        call = call_start + 3 * i
        require(data[call] == 0x5B, "Expected direct CALL")
        start = 0xA00 + struct.unpack_from("<H", data, call + 1)[0]
        guard = direct_condition(data, start)
        pos, writes = start + 13, []
        while data[pos] not in (0x72, 0x71):
            op = data[pos]
            if op == 0x6E:
                reg, mask, value = struct.unpack_from("<III", data, pos + 1)
                writes.append({"opcode": "NV_REG", "register": hex(reg),
                               "and_mask": hex(mask), "or_mask": hex(value),
                               "expression": "(old & and_mask) | or_mask"})
                pos += 13
            elif op == 0x7A:
                reg, value = struct.unpack_from("<II", data, pos + 1)
                writes.append({"opcode": "ZM_REG", "register": hex(reg), "value": value})
                pos += 9
            elif op == 0x58:
                reg, count = struct.unpack_from("<IB", data, pos + 1)
                values = list(struct.unpack_from(f"<{count}I", data, pos + 6))
                writes.append({"opcode": "ZM_REG_SEQUENCE", "base": hex(reg), "values": values})
                pos += 6 + count * 4
            else:
                raise ValueError(f"Unexpected opcode {op:#x} at {pos:#x}")
            require(pos - start < 128, "Subscript exceeded bounded scope")
        require(data[pos:pos + 2] == b"\x72\x71", "Expected child RESUME/DONE")
        blocks.append({"call_offset": hex(call), "guard": guard, "writes": writes,
                       "end_exclusive": hex(pos + 2)})
    require(any(b["guard"]["file_offset"] == hex(second) for b in blocks), "Missing TPC call")
    return {"parent_offset": hex(parent), "condition_index": data[parent + 1],
            "parent_inline_read_modify_writes": inline, "blocks": blocks}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sequence(data, offset):
    require(data[offset] == 0x58, "Expected ZM_REG_SEQUENCE")
    base, count = struct.unpack_from("<IB", data, offset + 1)
    require(base == 0x21838 and count == 6, "Unexpected TPC sequence shape")
    values = list(struct.unpack_from("<6I", data, offset + 6))
    return {"file_offset": hex(offset), "base": hex(base), "values": values}


def decode(label, path):
    data = path.read_bytes()
    digest, first, second = ROMS[label]
    require(hashlib.sha256(data).hexdigest() == digest, f"{label}: SHA-256 mismatch")
    require(data[first] == 0x75, "Expected CONDITION")
    index = data[first + 1]
    # Validated NVGI/PCI image base mapping, shared with width-path analyzer.
    condition_base = 0xA00 + struct.unpack_from("<H", data, 0xC83 + 6)[0]
    predicate = struct.unpack_from("<III", data, condition_base + 12 * index)
    require(data[first + 2] == 0x7A, "Expected ZM_REG")
    register, value = struct.unpack_from("<II", data, first + 3)
    require((register, value) == (0x2181C, 0), "Unexpected preceding register write")
    require(data[second] == 0xAC, "Expected direct register condition")
    payload = data[second + 1:second + 13]
    require(payload.hex() == "702402000100000000000000", "Unexpected 0xac payload")
    require(data[second + 43:second + 45] == bytes([0x72, 0x71]), "Expected RESUME/DONE boundary")
    return {
        "rom": label, "sha256": digest,
        "condition": {"file_offset": hex(first), "table_base": hex(condition_base),
                      "index": index, "register": hex(predicate[0]),
                      "mask": hex(predicate[1]), "value": hex(predicate[2])},
        "preceding_write": {"register": hex(register), "value": value},
        "tpc_sequences": [sequence(data, first + 11), sequence(data, second + 13)],
        "direct_condition": direct_condition(data, second),
        "guarded_family": guarded_family(data, second, label),
        "offline_predicate_evaluation": {
            "input_origin": "Earlier runtime captures; not boot-time values or an execution trace",
            "gpu0_22470": "0x23", "gpu1_22470": "0x1",
            "skip_with_incoming_allow": [condition_skip(False, x, 1, 0) for x in (0x23, 1)]},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmp_rom", type=Path)
    parser.add_argument("v100_rom", type=Path)
    args = parser.parse_args()
    try:
        results = [decode("CMP", args.cmp_rom), decode("V100", args.v100_rom)]
    except (ValueError, OSError, struct.error) as error:
        parser.exit(1, f"Error: {error}\n")
    print(json.dumps({"scope": "Bounded static decoding; execution not observed", "specification": SPEC,
                      "results": results}, indent=2))


if __name__ == "__main__":
    main()
