#!/usr/bin/env python3
"""Reject files outside the public research repository's narrow boundary."""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPROVED_FILES = {
    ".github/workflows/public-boundary.yml",
    ".gitignore",
    "DISCLOSURE.md",
    "LICENSE",
    "PUBLICATION_BOUNDARY.md",
    "README.md",
    "SECURITY.md",
    "docs/GEN3-LIMIT.md",
    "docs/METHODOLOGY.md",
    "docs/PCIE-RESULTS.md",
    "docs/TENSOR-RESULTS.md",
    "results/SHA256SUMS",
    "results/tensor-benchmark.png",
    "results/tensor-benchmark.txt",
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
SENSITIVE_PATTERNS = {
    "private IPv4 address": re.compile(
        r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
    ),
    "private key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "credential assignment": re.compile(
        r"(?i)\b(?:password|passwd|token|api[_-]?key)\s*[:=]"
    ),
    "proprietary register literal": re.compile(r"\b0x[0-9a-fA-F]{5,}\b"),
    "private RM symbol": re.compile(r"\b_nv\d+rm\b"),
    "private implementation marker": re.compile(
        r"(?i)\b(?:build_payloads|nouveau_acr_hook|dependency-map|"
        r"ucode_load|fecs_sig|replace_on_return|arm_write)\b"
    ),
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

        if relative not in APPROVED_FILES:
            failures.append(f"file is not allowlisted: {relative}")

        if suffix in DENIED_SUFFIXES:
            failures.append(f"forbidden binary/payload type: {relative}")

        if relative == "tools/check_public_boundary.py":
            continue

        if suffix in {"", ".md", ".py", ".sh", ".txt", ".yml"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            for label, pattern in SENSITIVE_PATTERNS.items():
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
