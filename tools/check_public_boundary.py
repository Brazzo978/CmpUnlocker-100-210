#!/usr/bin/env python3
"""Reject files outside the public research repository's narrow boundary."""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPROVED_TOOLS = {
    "tools/benchmark_tensor.py",
    "tools/check_public_boundary.py",
    "tools/collect_state.sh",
}
DENIED_SUFFIXES = {
    ".bin",
    ".efi",
    ".fw",
    ".ko",
    ".rom",
}
WRITE_CAPABLE_PATTERNS = {
    "device-memory access": re.compile(r"/dev/(?:mem|port)"),
    "kernel-module operation": re.compile(
        r"\b(?:insmod|rmmod|modprobe)\b"
    ),
    "PCI configuration write": re.compile(r"\bsetpci\b"),
    "driver interception": re.compile(r"\b(?:kprobe|kretprobe)\b"),
    "MMIO write API": re.compile(r"\b(?:writel|writeq|ioremap)\s*\("),
    "service mutation": re.compile(
        r"\bsystemctl\s+(?:start|stop|restart|enable|disable)\b"
    ),
}


def tracked_files() -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [
        item.decode("utf-8")
        for item in completed.stdout.split(b"\0")
        if item
    ]


def main() -> int:
    failures: list[str] = []
    files = tracked_files()

    for relative in files:
        path = ROOT / relative
        suffix = path.suffix.lower()

        if suffix in DENIED_SUFFIXES:
            failures.append(f"forbidden binary/payload type: {relative}")

        if relative.startswith("tools/") and relative not in APPROVED_TOOLS:
            failures.append(f"tool is not allowlisted: {relative}")

        if relative == "tools/check_public_boundary.py":
            continue

        if relative.startswith("tools/"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for label, pattern in WRITE_CAPABLE_PATTERNS.items():
                if pattern.search(text):
                    failures.append(f"{label} found in {relative}")

    if failures:
        print("PUBLICATION BOUNDARY: FAIL", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"PUBLICATION BOUNDARY: PASS ({len(files)} tracked files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
