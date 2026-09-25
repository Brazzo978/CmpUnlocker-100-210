#!/usr/bin/env python3
"""Hash-pinned offline inventory of PRE_OS/DEVINIT firmware; no binary export.

Uses the previously validated NVGI logical PCI-AT+FwSec pointer mapping.
Only PMU applications 1 and 4, descriptor v1, are accepted in these inputs.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path
from analyze_devinit_tpc_path import ROMS, require

SOURCE = "https://github.com/OE4T/linux-nvgpu/blob/21d928824dc7ca3dc17603a53b11edc6641ace2d/drivers/gpu/nvgpu/include/nvgpu/bios.h"


def inventory(label, path):
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == ROMS[label][0], "ROM hash mismatch")
    fw_base = 0x20A00 if label == "CMP" else 0x1FC00

    def mapped(pointer):
        require(pointer >= 0xE400, "Expected FwSec-space pointer")
        offset = fw_base + pointer - 0xE400
        require(offset < len(data), "Pointer outside ROM")
        return offset

    pointer = struct.unpack_from("<I", data, 0xD8D)[0]
    table = mapped(pointer)
    require(data[table:table+6] == bytes([1, 6, 6, 5, 1, 0x30]), "Unexpected table layout")
    entries = []
    for index in (0, 3):
        app, target, ptr = struct.unpack_from("<BBI", data, table+6+index*6)
        require((app, target) == ((1, 1) if index == 0 else (4, 1)), "Unexpected application")
        offset = mapped(ptr)
        desc = struct.unpack_from("<12I", data, offset)
        stored, unpacked, entry, interface, phys, imem, virt, secbase, secsize, dmoff, dmphys, dmsize = desc
        require(stored & 1 == 0, "Expected v1 descriptor")
        require(stored == unpacked and imem >= secsize, "Unsupported compression or IMEM sizes")
        boot = imem - secsize
        require(dmoff == imem and stored == imem+dmsize, "Non-packed descriptor")
        require(offset+0x30+stored <= len(data), "Payload outside ROM")
        segments = {}
        for name, start, size in [("bootloader", offset+0x30, boot),
                                 ("secure_imem", offset+0x30+boot, secsize),
                                 ("dmem", offset+0x30+dmoff, dmsize)]:
            segments[name] = {"file_offset": hex(start), "bytes": size,
                              "sha256": hashlib.sha256(data[start:start+size]).hexdigest()}
        entries.append({"application_id": app, "application": "PRE_OS" if app == 1 else "DEVINIT",
                        "target_id": target, "descriptor_file_offset": hex(offset),
                        "descriptor_version": 1, "entry_point": hex(entry), "segments": segments})
    return {"rom": label, "rom_bytes": len(data), "rom_sha256": ROMS[label][0],
            "falcon_table_file_offset": hex(table), "applications": entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmp_rom", type=Path)
    parser.add_argument("v100_rom", type=Path)
    args = parser.parse_args()
    try:
        results = [inventory("CMP", args.cmp_rom), inventory("V100", args.v100_rom)]
    except (ValueError, OSError, struct.error) as error:
        parser.exit(1, f"Error: {error}\n")
    print(json.dumps({"scope": "Binary identity only; no disassembly or causality established",
                      "application_ids_source": SOURCE, "results": results}, indent=2))


if __name__ == "__main__":
    main()
