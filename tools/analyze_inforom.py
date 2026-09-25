#!/usr/bin/env python3
"""Inspect and compare NVIDIA nvflash ``--save`` InfoROM images.

The tool is deliberately read-only.  It decodes only structure that can be
confirmed directly from the file: the ROM directory, object headers, printable
strings, byte occupancy, hashes, and pairwise differences.  Unknown fields are
left unnamed instead of being guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import string
import struct
import sys


PRINTABLE = set(bytes(string.printable, "ascii")) - set(b"\r\n\t\x0b\x0c")


def ascii_runs(data: bytes, minimum: int = 4):
    start = None
    for index, value in enumerate(data + b"\x00"):
        if value in PRINTABLE:
            if start is None:
                start = index
        elif start is not None:
            if index - start >= minimum:
                yield start, data[start:index].decode("ascii")
            start = None


def directory_entries(data: bytes):
    # The saved images use a 0x40-byte ROM header.  Entries begin at 0x0e and
    # consist of a three-byte object tag followed by a little-endian offset.
    if len(data) < 0x40 or data[:3] != b"ROM":
        return
    cursor = 0x0E
    while cursor + 5 <= 0x40:
        tag = data[cursor : cursor + 3]
        if not all(byte in PRINTABLE for byte in tag):
            break
        offset = struct.unpack_from("<H", data, cursor + 3)[0]
        yield tag.decode("ascii"), offset
        cursor += 5


def analyze(path: pathlib.Path):
    data = path.read_bytes()
    print(f"FILE {path}")
    print(f"  size={len(data)} sha256={hashlib.sha256(data).hexdigest()}")
    print(f"  zero_bytes={data.count(0)} nonzero_bytes={len(data) - data.count(0)}")

    entries = list(directory_entries(data) or [])
    if not entries:
        print("  directory=unrecognized")
    else:
        print(f"  rom_header={data[:3].decode('ascii')} version={data[3]}.{data[4]}")
        for index, (tag, offset) in enumerate(entries):
            next_offset = entries[index + 1][1] if index + 1 < len(entries) else len(data)
            if offset + 8 > len(data):
                print(f"  object={tag} offset=0x{offset:x} invalid")
                continue
            object_tag = data[offset : offset + 3].decode("ascii", errors="replace")
            version = data[offset + 3]
            subversion = data[offset + 4]
            declared_size = struct.unpack_from("<H", data, offset + 5)[0]
            actual_span = max(0, min(next_offset, len(data)) - offset)
            payload = data[offset : offset + actual_span]
            print(
                f"  object={tag} offset=0x{offset:03x} tag={object_tag} "
                f"version={version}.{subversion} declared_size=0x{declared_size:x} "
                f"directory_span=0x{actual_span:x} nonzero={actual_span - payload.count(0)}"
            )

    for offset, value in ascii_runs(data):
        print(f"  ascii offset=0x{offset:03x} value={value!r}")
    return data


def compare(paths: list[pathlib.Path], blobs: list[bytes]):
    if len(blobs) < 2:
        return
    base_path, base = paths[0], blobs[0]
    for path, data in zip(paths[1:], blobs[1:]):
        common = min(len(base), len(data))
        diffs = [(i, base[i], data[i]) for i in range(common) if base[i] != data[i]]
        print(f"COMPARE {base_path} {path}")
        print(f"  differing_bytes={len(diffs)} common_size={common} size_delta={len(data)-len(base)}")
        for offset, before, after in diffs:
            before_chr = chr(before) if before in PRINTABLE else "."
            after_chr = chr(after) if after in PRINTABLE else "."
            print(
                f"  diff offset=0x{offset:03x} "
                f"left=0x{before:02x}({before_chr}) right=0x{after:02x}({after_chr})"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=pathlib.Path)
    args = parser.parse_args()
    missing = [str(path) for path in args.images if not path.is_file()]
    if missing:
        parser.error("missing input: " + ", ".join(missing))

    blobs = [analyze(path) for path in args.images]
    compare(args.images, blobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
