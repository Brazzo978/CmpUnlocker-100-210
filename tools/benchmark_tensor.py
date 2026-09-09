#!/usr/bin/env python3
"""Read/compute-only FP16 GEMM benchmark for the CMP100 Tensor path."""

from __future__ import annotations

import argparse
import statistics
import sys

import torch


def benchmark(
    device_index: int, n: int, warmup: int, repeats: int
) -> tuple[float, float, float]:
    device = torch.device(f"cuda:{device_index}")
    torch.cuda.set_device(device)
    torch.manual_seed(0xC0DE + device_index)

    a = torch.randn((n, n), device=device, dtype=torch.float16)
    b = torch.randn((n, n), device=device, dtype=torch.float16)

    for _ in range(warmup):
        result = torch.mm(a, b)
    torch.cuda.synchronize(device)

    samples: list[float] = []
    for _ in range(repeats):
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        result = torch.mm(a, b)
        end.record()
        end.synchronize()
        elapsed_seconds = start.elapsed_time(end) / 1000.0
        samples.append((2.0 * n**3) / elapsed_seconds / 1.0e12)

    if not bool(torch.isfinite(result).all().item()):
        raise RuntimeError(f"GPU {device_index} produced a non-finite result")

    return statistics.median(samples), min(samples), max(samples)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=8192)
    parser.add_argument("--warmup", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--devices", type=int, nargs="*")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not torch.cuda.is_available():
        print("ERROR: CUDA is not available", file=sys.stderr)
        return 1

    devices = (
        args.devices
        if args.devices is not None
        else list(range(torch.cuda.device_count()))
    )
    print("CMP100-210 VOLTA TENSOR PATH - FP16 GEMM VALIDATION")
    print(f"PyTorch {torch.__version__} | CUDA {torch.version.cuda}")
    print(
        f"matrix={args.n}x{args.n} | "
        f"warmup={args.warmup} | repeats={args.repeats}"
    )
    print("-" * 72)

    for device_index in devices:
        name = torch.cuda.get_device_name(device_index)
        median, minimum, maximum = benchmark(
            device_index, args.n, args.warmup, args.repeats
        )
        print(f"GPU {device_index}: {name}")
        print(f"  FP16 Tensor GEMM median : {median:8.3f} TFLOPS")
        print(f"  sample range            : {minimum:8.3f} - {maximum:8.3f} TFLOPS")
        print("  result validation       : PASS")

    print("-" * 72)
    print("PASS: selected GPUs completed FP16 Tensor GEMM validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
