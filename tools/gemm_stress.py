#!/usr/bin/env python3
"""Sustained GEMM stress test for Tensor-core paths.

Unlike tools/benchmark_tensor.py (a short FP16 validation), this tool hammers
the GPU with large matrix multiplications: size sweeps, sustained timed runs,
correctness checks against an FP32 reference, throughput gates, and optional
power/thermal telemetry.

Volta (GV100 / CMP 100-210) notes:
  * The Tensor path is FP16. FP32 runs on CUDA cores and is useful as a
    contrast baseline (--tensor-ratio).
  * BF16 is accepted as a flag but has no Tensor acceleration on Volta;
    configs that the backend rejects are recorded as ERROR, not crashes.

Examples:
  Sustained 60 s hammering of 8k and 16k FP16 GEMMs on all GPUs:
    gemm_stress.py --sizes 8192 16384 --seconds 60

  Prove the Tensor path is engaged (FP16 must beat FP32 by 3x):
    gemm_stress.py --sizes 8192 --tensor-ratio 3 --telemetry

  Overnight soak on two cards with machine-readable output:
    gemm_stress.py --devices 1 2 --sizes 16384 --seconds 28800 \\
        --parallel --json soak.json --csv soak.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

try:
    import torch
except ImportError:
    print("ERROR: PyTorch is required (see repo baseline, e.g. 2.6.0+cu124)",
          file=sys.stderr)
    raise SystemExit(1)


DTYPE_MAP = {
    "fp16": torch.float16,
    "bf16": torch.bfloat16,
    "fp32": torch.float32,
}
ITEMSIZE = {"fp16": 2, "bf16": 2, "fp32": 4}

TELEMETRY_FIELDS = (
    "power.draw,temperature.gpu,clocks.current.sm,clocks.current.memory"
)

# Device-name substrings identifying AMD GPUs. Under a ROCm PyTorch build,
# torch.cuda enumerates AMD cards too; this project targets NVIDIA hardware,
# so AMD devices are always excluded from stress runs.
AMD_NAME_HINTS = (
    "amd", "radeon", "instinct", "firepro", "gfx", "vega", "navi",
    "polaris", "fiji", "hawaii",
)


def is_amd_device(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in AMD_NAME_HINTS)


def select_devices(requested: list[int] | None,
                   names: dict[int, str]) -> tuple[list[int], list[int]]:
    """Split candidate devices into (usable, amd_skipped).

    Unknown indices pass through untouched so torch can reject them with
    its natural error; only positively identified AMD devices are skipped.
    """
    candidates = list(requested) if requested is not None else sorted(names)
    keep: list[int] = []
    skipped: list[int] = []
    for idx in candidates:
        if idx in names and is_amd_device(names[idx]):
            skipped.append(idx)
        else:
            keep.append(idx)
    return keep, skipped


# Tensor cores per SM by compute capability. CUDA exposes no tensor-core
# count; the per-SM width is a fixed architectural constant, so the device
# total is queried SMs times this table. Unknown arches yield None.
TENSOR_CORES_PER_SM = {
    (7, 0): 8,  # Volta (GV100 / CMP 100-210)
    (7, 5): 8,  # Turing
    (8, 0): 4,  # Ampere
    (8, 6): 4,
    (8, 9): 4,
    (9, 0): 4,  # Hopper
}


def tensor_core_count(major: int, minor: int, sms: int) -> int | None:
    """Derived tensor-core total, or None when it cannot be determined."""
    per_sm = TENSOR_CORES_PER_SM.get((major, minor))
    if per_sm is None or sms <= 0:
        return None
    return per_sm * sms


def batch_devices(devices: list[int], size: int) -> list[list[int]]:
    """Split devices into consecutive batches of at most size."""
    if size < 1:
        raise ValueError("--max-parallel must be >= 1")
    return [devices[i:i + size] for i in range(0, len(devices), size)]


# Cooperative stop flag for Ctrl+C. KeyboardInterrupt reaches only the main
# thread, so the handler sets this and every running loop winds down within
# about one GEMM iteration instead of swallowing the interrupt.
_STOP = threading.Event()


def request_stop() -> None:
    """Ask all running loops to wind down promptly."""
    _STOP.set()


def stop_requested() -> bool:
    return _STOP.is_set()


def describe_device(device_index: int) -> str:
    """One-line capability summary, e.g. 'sm_70 | 80 SMs | 640 tensor'."""
    try:
        props = torch.cuda.get_device_properties(device_index)
    except RuntimeError:
        return "properties unavailable"
    sms = props.multi_processor_count
    count = tensor_core_count(props.major, props.minor, sms)
    arch = f"sm_{props.major}{props.minor}"
    if count is None:
        return f"{arch} | {sms} SMs | tensor count unknown"
    return f"{arch} | {sms} SMs | {count} tensor"

_telemetry_warned = False


@dataclass
class Config:
    m: int
    n: int
    k: int
    dtype: str
    layout: str

    @property
    def label(self) -> str:
        return (f"{self.m}x{self.n}x{self.k} {self.dtype.upper()} "
                f"{self.layout.upper()}")


@dataclass
class ConfigResult:
    config: Config
    status: str = "SKIP"  # PASS | FAIL | ERROR | SKIP
    reason: str = ""
    iters: int = 0
    median_tflops: float = 0.0
    min_tflops: float = 0.0
    max_tflops: float = 0.0
    drift_pct: float | None = None
    max_rel_err: float | None = None
    validate_mode: str = "off"
    elapsed_s: float = 0.0
    telemetry_start: dict = field(default_factory=dict)
    telemetry_end: dict = field(default_factory=dict)


def parse_shapes(sizes: list[int], shapes: list[str]) -> list[tuple[int, int, int]]:
    dims: list[tuple[int, int, int]] = [(s, s, s) for s in sizes]
    for spec in shapes:
        parts = spec.lower().replace("x", ",").split(",")
        if len(parts) != 3:
            raise ValueError(f"bad --shapes entry {spec!r}, want MxNxK")
        try:
            m, n, k = (int(p) for p in parts)
        except ValueError:
            raise ValueError(f"bad --shapes entry {spec!r}, want MxNxK")
        if min(m, n, k) <= 0:
            raise ValueError(f"bad --shapes entry {spec!r}, dims must be > 0")
        dims.append((m, n, k))
    return dims


def make_operands(m: int, n: int, k: int, dtype: str, layout: str,
                  init: str, device: torch.device) -> tuple[torch.Tensor,
                                                            torch.Tensor]:
    """Build A (MxK) and B (KxN) effective operands.

    Transposed layouts keep non-contiguous .t() views so cuBLAS really sees
    the transpose path instead of a silently contiguous copy.
    """
    tdtype = DTYPE_MAP[dtype]
    a_shape = (k, m) if layout[0] == "t" else (m, k)
    b_shape = (n, k) if layout[1] == "t" else (k, n)
    if init == "uniform":
        a = torch.rand(a_shape, device=device, dtype=torch.float32) - 0.5
        b = torch.rand(b_shape, device=device, dtype=torch.float32) - 0.5
        a = a.to(tdtype)
        b = b.to(tdtype)
    else:
        a = torch.randn(a_shape, device=device, dtype=torch.float32).to(tdtype)
        b = torch.randn(b_shape, device=device, dtype=torch.float32).to(tdtype)
    if layout[0] == "t":
        a = a.t()
    if layout[1] == "t":
        b = b.t()
    return a, b


def rel_frobenius_err(c: torch.Tensor, ref: torch.Tensor) -> float:
    num = torch.linalg.vector_norm((c.float() - ref).flatten()).item()
    den = torch.linalg.vector_norm(ref.flatten()).item()
    if den == 0.0:
        return 0.0 if num == 0.0 else float("inf")
    return num / den


@torch.no_grad()
def validate_full(a: torch.Tensor, b: torch.Tensor, c: torch.Tensor) -> float:
    ref = torch.mm(a.float(), b.float())
    return rel_frobenius_err(c, ref)


@torch.no_grad()
def validate_block(a: torch.Tensor, b: torch.Tensor, c: torch.Tensor,
                   block: int) -> float:
    # C[:b, :b] == A[:b, :] @ B[:, :b]: the full inner dimension K is kept
    # and only the output tile is shrunk, so the stressed output itself is
    # checked against an FP32 reference of the matching tile. (A separate
    # small re-run is NOT compared bitwise: cuBLAS may pick a different
    # kernel/tile order for different shapes, so exact equality is not
    # guaranteed.)
    bb = min(block, a.shape[0], b.shape[1])
    ref = torch.mm(a[:bb, :].float(), b[:, :bb].float())
    return rel_frobenius_err(c[:bb, :bb], ref)


def read_telemetry(device_index: int) -> dict:
    """Sample power/temp/clocks via nvidia-smi; {} when unavailable."""
    global _telemetry_warned
    if shutil.which("nvidia-smi") is None:
        if not _telemetry_warned:
            print("warning: nvidia-smi not found, telemetry disabled",
                  file=sys.stderr)
            _telemetry_warned = True
        return {}
    try:
        out = subprocess.run(
            ["nvidia-smi", "-i", str(device_index),
             f"--query-gpu={TELEMETRY_FIELDS}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return {}
    try:
        power, temp, sm, mem = (x.strip() for x in out.split(","))
        return {"power_w": float(power), "temp_c": float(temp),
                "sm_mhz": float(sm), "mem_mhz": float(mem)}
    except ValueError:
        return {}


def timed_mm(a: torch.Tensor, b: torch.Tensor) -> tuple[torch.Tensor, float]:
    start = torch.cuda.Event(enable_timing=True)
    end = torch.cuda.Event(enable_timing=True)
    start.record()
    c = torch.mm(a, b)
    end.record()
    end.synchronize()
    return c, start.elapsed_time(end) / 1000.0


def estimate_bytes(m: int, n: int, k: int, dtype: str,
                   full_validate: bool, block: int = 2048) -> int:
    elems = m * k + k * n + m * n
    total = elems * ITEMSIZE[dtype]
    if full_validate:
        total += elems * 4  # transient FP32 copies + reference
    else:
        bb = min(block, m, n)
        total += (2 * bb * k + bb * bb) * 4  # block-validation transient
    return int(total * 1.1)  # headroom


def run_config(device_index: int, cfg: Config, args: argparse.Namespace,
               budget_bytes: int) -> ConfigResult:
    res = ConfigResult(config=cfg)
    device = torch.device(f"cuda:{device_index}")
    torch.cuda.set_device(device)
    flops = 2.0 * cfg.m * cfg.n * cfg.k

    # Pick validation mode under the memory budget.
    if args.validate == "off":
        res.validate_mode = "off"
    elif args.validate == "full":
        res.validate_mode = "full"
    elif args.validate == "block":
        res.validate_mode = "block"
    else:  # auto
        full_bytes = estimate_bytes(cfg.m, cfg.n, cfg.k, cfg.dtype, True)
        res.validate_mode = ("full" if full_bytes <= budget_bytes else "block")
    need_full = res.validate_mode == "full"
    need_bytes = estimate_bytes(cfg.m, cfg.n, cfg.k, cfg.dtype, need_full,
                                args.validate_block)
    if need_bytes > budget_bytes:
        res.status = "SKIP"
        res.reason = (f"estimated {need_bytes / 1e9:.1f} GB exceeds "
                     f"{budget_bytes / 1e9:.1f} GB budget")
        return res

    try:
        a, b = make_operands(cfg.m, cfg.n, cfg.k, cfg.dtype, cfg.layout,
                             args.init, device)
    except RuntimeError as exc:
        res.status = "ERROR"
        res.reason = f"operand alloc failed: {exc}"
        return res

    try:
        with torch.no_grad():
            for _ in range(args.warmup):
                c = torch.mm(a, b)
        torch.cuda.synchronize(device)
    except RuntimeError as exc:
        res.status = "ERROR"
        res.reason = f"warmup failed ({exc}); dtype may lack backend support"
        return res

    if args.telemetry:
        res.telemetry_start = read_telemetry(device_index)

    def check() -> float:
        if res.validate_mode == "full":
            return validate_full(a, b, c)
        return validate_block(a, b, c, args.validate_block)

    samples: list[float] = []
    worst_err = 0.0
    timed_iters = 0
    wall_start = time.monotonic()
    last_log = wall_start
    sustained = args.seconds > 0
    target = float("inf") if sustained else args.repeats
    c = torch.empty(0, device=device)
    try:
        with torch.no_grad():
            while timed_iters < target and not stop_requested():
                c, secs = timed_mm(a, b)
                if secs <= 0:
                    continue
                samples.append(flops / secs / 1e12)
                timed_iters += 1
                if res.validate_mode != "off" and (
                        timed_iters == 1 or timed_iters == target
                        or (args.validate_every > 0
                            and timed_iters % args.validate_every == 0)):
                    worst_err = max(worst_err, check())
                now = time.monotonic()
                if sustained and now - wall_start >= args.seconds:
                    break
            if sustained and res.validate_mode != "off" and samples:
                worst_err = max(worst_err, check())  # last iter
                if (sustained and args.log_interval > 0
                        and now - last_log >= args.log_interval):
                    last_log = now
                    med = statistics.median(samples)
                    print(f"  [gpu{device_index} {cfg.label}] "
                          f"t={now - wall_start:6.0f}s iters={timed_iters} "
                          f"median={med:8.3f} TFLOPS", flush=True)
    except KeyboardInterrupt:
        request_stop()
        res.reason = "interrupted; partial results"
    except RuntimeError as exc:
        res.status = "ERROR"
        res.reason = f"timed GEMM failed: {exc}"
        return res
    finally:
        res.elapsed_s = time.monotonic() - wall_start
        if args.telemetry:
            res.telemetry_end = read_telemetry(device_index)

    if not samples:
        res.status = "ERROR"
        res.reason = "no timed iterations completed"
        return res

    # Full finite check on the final output is cheap; always do it.
    try:
        finite = bool(torch.isfinite(c).all().item())
    except RuntimeError:
        finite = False
    if not finite:
        res.status = "FAIL"
        res.reason = "non-finite result"
        return res

    res.iters = timed_iters
    res.median_tflops = statistics.median(samples)
    res.min_tflops = min(samples)
    res.max_tflops = max(samples)
    if len(samples) >= 4:
        half = len(samples) // 2
        first = statistics.median(samples[:half])
        second = statistics.median(samples[half:])
        res.drift_pct = (100.0 * (second - first) / first if first
                         else 0.0)
    if res.validate_mode != "off":
        res.max_rel_err = worst_err

    failures: list[str] = []
    if res.max_rel_err is not None and res.max_rel_err > args.tol:
        failures.append(f"rel_err {res.max_rel_err:.3e} > tol {args.tol:g}")
    if args.min_tflops > 0 and res.median_tflops < args.min_tflops:
        failures.append(f"median {res.median_tflops:.3f} < {args.min_tflops:g}")
    if failures:
        res.status = "FAIL"
        res.reason = "; ".join(failures)
    elif res.reason or stop_requested():
        res.status = "FAIL"
        if stop_requested() and "interrupted" not in res.reason:
            res.reason = ((res.reason + "; " if res.reason else "")
                           + "interrupted; partial results")
    else:
        res.status = "PASS"
    return res


def run_device(device_index: int, configs: list[Config],
               args: argparse.Namespace) -> tuple[str, list[ConfigResult]]:
    name = torch.cuda.get_device_name(device_index)
    torch.manual_seed(args.seed + device_index)
    free, _ = torch.cuda.mem_get_info(device_index)
    budget = int(min(args.max_mem_gb * 1e9, free * 0.9))
    results: list[ConfigResult] = []
    for cfg in configs:
        if stop_requested():
            break
        results.append(run_config(device_index, cfg, args, budget))
        if args.fail_fast and results[-1].status in ("FAIL", "ERROR"):
            break
    return name, results


def run_all(devices: list[int], configs: list[Config],
            args: argparse.Namespace,
            runner=run_device) -> tuple[
                dict[int, tuple[str, list[ConfigResult]]], bool]:
    """Run every device in batches; return (results, interrupted).

    A Ctrl+C sets the stop flag, cancels batches that never started, and
    returns whatever completed so the caller can still report and exit 130.
    """
    if args.max_parallel > 0:
        batch_size = args.max_parallel
    elif args.parallel:
        batch_size = len(devices)
    else:
        batch_size = 1
    per_device: dict[int, tuple[str, list[ConfigResult]]] = {}
    batches = batch_devices(devices, batch_size)
    try:
        for num, batch in enumerate(batches, 1):
            if stop_requested():
                break
            if len(batches) > 1:
                print(f"--- batch {num}/{len(batches)}: devices {batch} ---",
                      flush=True)
            if len(batch) > 1:
                with ThreadPoolExecutor(
                        max_workers=len(batch),
                        thread_name_prefix="gemm-stress") as pool:
                    try:
                        for idx, out in zip(batch, pool.map(
                                lambda i: runner(i, configs, args), batch)):
                            per_device[idx] = out
                    except KeyboardInterrupt:
                        request_stop()
                        pool.shutdown(wait=True, cancel_futures=True)
                        raise
            else:
                per_device[batch[0]] = runner(batch[0], configs, args)
    except KeyboardInterrupt:
        request_stop()
    return per_device, stop_requested()


def apply_tensor_ratio(per_device: dict[int, tuple[str, list[ConfigResult]]],
                       args: argparse.Namespace) -> None:
    """Require FP16 medians to beat the FP32 baseline by --tensor-ratio."""
    if args.tensor_ratio <= 0:
        return
    for _, (_, results) in per_device.items():
        baselines: dict[tuple[int, int, int, str], float] = {}
        for res in results:
            c = res.config
            if (c.dtype == "fp32" and res.status == "PASS"
                    and res.median_tflops > 0):
                baselines[(c.m, c.n, c.k, c.layout)] = res.median_tflops
        for res in results:
            c = res.config
            if c.dtype != "fp16" or res.status != "PASS":
                continue
            base = baselines.get((c.m, c.n, c.k, c.layout))
            if base is None or base <= 0:
                res.status = "FAIL"
                res.reason = "no FP32 baseline for tensor-ratio check"
                continue
            ratio = res.median_tflops / base
            if ratio < args.tensor_ratio:
                res.status = "FAIL"
                res.reason = (f"tensor-ratio {ratio:.2f}x "
                             f"< {args.tensor_ratio:g}x "
                             f"(fp16 {res.median_tflops:.1f} vs "
                             f"fp32 {base:.1f} TFLOPS)")


def fmt_telemetry(res: ConfigResult) -> str:
    if not res.telemetry_end:
        return ""
    e = res.telemetry_end
    s = res.telemetry_start or {}
    parts = [f"{e.get('power_w', float('nan')):.0f}W",
             f"{e.get('temp_c', float('nan')):.0f}C",
             f"SM{e.get('sm_mhz', float('nan')):.0f}",
             f"MEM{e.get('mem_mhz', float('nan')):.0f}"]
    if s:
        parts.append(f"(dP{e.get('power_w', 0) - s.get('power_w', 0):+.0f}W "
                     f"dT{e.get('temp_c', 0) - s.get('temp_c', 0):+.0f}C)")
    return " ".join(parts)


def report(per_device: dict[int, tuple[str, list[ConfigResult]]],
           args: argparse.Namespace) -> int:
    n_pass = n_fail = n_err = n_skip = 0
    for idx in sorted(per_device):
        name, results = per_device[idx]
        print(f"GPU {idx}: {name}")
        for res in results:
            c = res.config
            if res.status == "PASS":
                n_pass += 1
            elif res.status == "FAIL":
                n_fail += 1
            elif res.status == "ERROR":
                n_err += 1
            else:
                n_skip += 1
            line = f"  [{res.status:5s}] {c.label:28s}"
            if res.iters:
                line += (f" med={res.median_tflops:8.3f} "
                         f"range={res.min_tflops:8.3f}-{res.max_tflops:8.3f} "
                         f"TFLOPS iters={res.iters}")
                if res.drift_pct is not None:
                    line += f" drift={res.drift_pct:+.1f}%"
                if res.max_rel_err is not None:
                    line += f" rel_err={res.max_rel_err:.2e}"
                tel = fmt_telemetry(res)
                if tel:
                    line += f" [{tel}]"
            if res.reason:
                line += f" :: {res.reason}"
            print(line)
    print("-" * 72)
    print(f"PASS={n_pass} FAIL={n_fail} ERROR={n_err} SKIP={n_skip}")
    if n_fail or n_err:
        print("RESULT: FAIL")
        return 1
    if n_pass == 0:
        print("RESULT: FAIL (nothing ran)")
        return 1
    print("RESULT: PASS")
    return 0


def write_outputs(per_device: dict[int, tuple[str, list[ConfigResult]]],
                  args: argparse.Namespace) -> None:
    rows: list[dict] = []
    for idx in sorted(per_device):
        name, results = per_device[idx]
        for res in results:
            c = res.config
            rows.append({
                "device": idx, "name": name, "m": c.m, "n": c.n, "k": c.k,
                "dtype": c.dtype, "layout": c.layout, "status": res.status,
                "reason": res.reason, "iters": res.iters,
                "median_tflops": res.median_tflops,
                "min_tflops": res.min_tflops, "max_tflops": res.max_tflops,
                "drift_pct": res.drift_pct, "max_rel_err": res.max_rel_err,
                "validate_mode": res.validate_mode,
                "elapsed_s": round(res.elapsed_s, 3),
                "telemetry_start": res.telemetry_start,
                "telemetry_end": res.telemetry_end,
            })
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"results": rows}, fh, indent=2)
        print(f"wrote {args.json}")
    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=[k for k in rows[0]
                                                   if k not in
                                                   ("telemetry_start",
                                                    "telemetry_end")])
            writer.writeheader()
            for row in rows:
                writer.writerow({k: v for k, v in row.items()
                                 if k in writer.fieldnames})
        print(f"wrote {args.csv}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gemm_stress.py",
        usage="%(prog)s [options]",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Sustained GEMM hammer for Tensor-core paths.\n"
                    "Throws large matrix multiplications at the selected "
                    "GPUs, checks numerics against an FP32 reference, and "
                    "gates on throughput so the run is safe to trust.",
        epilog="examples:\n"
               "  quick single-card check\n"
               "    %(prog)s --devices 1 --sizes 8192 --repeats 10\n"
               "  prove the Tensor path is engaged (FP16 must beat FP32)\n"
               "    %(prog)s --sizes 8192 --tensor-ratio 3 --telemetry\n"
               "  soak every card, 2 at a time to respect a power budget\n"
               "    %(prog)s --devices 0 1 2 3 4 5 6 7 --sizes 8192 16384 \\\n"
               "        --seconds 300 --max-parallel 2 --tensor-ratio 3 \\\n"
               "        --telemetry --json soak.json --csv soak.csv\n"
               "\nexit status: 0 only when every config passes; 1 on any\n"
               "FAIL/ERROR, or when nothing ran.\n"
               "Ctrl+C stops promptly with partial results (exit 130).")
    group = parser.add_argument_group("device selection")
    group.add_argument("--devices", type=int, nargs="*",
                       help="GPU indices to stress (default: all)")

    group = parser.add_argument_group("workload shape")
    group.add_argument("--sizes", type=int, nargs="*", default=None,
                       help="square NxNxN sizes to sweep (default: 8192 "
                            "when no --shapes given)")
    group.add_argument("--shapes", nargs="*", default=[],
                       help="extra MxNxK shapes, e.g. 4096x2048x8192")
    group.add_argument("--dtype", nargs="*", default=["fp16"],
                       choices=sorted(DTYPE_MAP),
                       help="dtypes to stress (default: fp16)")
    group.add_argument("--layout", default="nn", choices=("nn", "nt", "tn",
                                                          "tt"),
                       help="transpose variant (default: nn)")
    group.add_argument("--init", default="randn",
                       choices=("randn", "uniform"),
                       help="operand fill (default: randn)")
    group.add_argument("--seed", type=int, default=1234,
                       help="base RNG seed, offset per device "
                            "(default: 1234)")

    group = parser.add_argument_group("run length")
    group.add_argument("--warmup", type=int, default=5,
                       help="untimed warm-up iterations per config "
                            "(default: 5)")
    group.add_argument("--repeats", type=int, default=20,
                       help="timed iterations per config in fixed mode "
                            "(default: 20)")
    group.add_argument("--seconds", type=float, default=0,
                       help="sustained seconds per config; >0 overrides "
                            "--repeats (default: 0)")

    group = parser.add_argument_group("correctness and pass gates")
    group.add_argument("--validate", default="auto",
                       choices=("auto", "full", "block", "off"),
                       help="correctness check vs FP32 reference "
                            "(default: auto)")
    group.add_argument("--validate-block", type=int, default=2048,
                       help="corner-tile edge for block validation "
                            "(default: 2048)")
    group.add_argument("--validate-every", type=int, default=0,
                       help="re-validate every K iters (0: first+last only)")
    group.add_argument("--tol", type=float, default=0.02,
                       help="max relative Frobenius error vs FP32 "
                            "(default: 0.02)")
    group.add_argument("--min-tflops", type=float, default=0,
                       help="fail configs below this median TFLOPS "
                            "(default: 0)")
    group.add_argument("--tensor-ratio", type=float, default=0,
                       help="require FP16 median >= R x FP32 baseline per "
                            "shape; auto-adds an FP32 baseline run "
                            "(default: 0)")
    group.add_argument("--max-mem-gb", type=float, default=12,
                       help="skip configs estimated above this device "
                            "budget in GB (default: 12)")

    group = parser.add_argument_group("execution")
    group.add_argument("--telemetry", action="store_true",
                       help="sample nvidia-smi power/temp/clocks per config")
    group.add_argument("--parallel", action="store_true",
                       help="stress all devices concurrently (one thread "
                            "each)")
    group.add_argument("--max-parallel", type=int, default=0, metavar="N",
                       help="stress at most N devices at once, batching "
                            "the rest (0: sequential, or all-at-once "
                            "with --parallel)")
    group.add_argument("--fail-fast", action="store_true",
                       help="stop a device on first FAIL/ERROR")
    group.add_argument("--log-interval", type=float, default=10,
                       help="progress seconds in sustained mode, 0 to "
                            "silence (default: 10)")

    group = parser.add_argument_group("output")
    group.add_argument("--json", metavar="FILE",
                       help="write machine-readable results to FILE")
    group.add_argument("--csv", metavar="FILE",
                       help="write machine-readable results to FILE")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not torch.cuda.is_available():
        print("ERROR: CUDA is not available", file=sys.stderr)
        return 1
    if args.seconds < 0 or args.repeats < 1 or args.warmup < 0:
        print("ERROR: --seconds >= 0, --repeats >= 1, --warmup >= 0",
              file=sys.stderr)
        return 1

    names: dict[int, str] = {}
    for idx in range(torch.cuda.device_count()):
        try:
            names[idx] = torch.cuda.get_device_name(idx)
        except RuntimeError:
            names[idx] = ""  # unqueryable; run_device reports it naturally
    devices, skipped_amd = select_devices(args.devices, names)
    for idx in skipped_amd:
        print(f"note: skipping AMD device {idx} ({names[idx]}); "
              f"AMD cards are always excluded from stress runs")
    if not devices:
        print("ERROR: no usable CUDA devices (all requested devices "
              "are AMD-excluded or none exist)", file=sys.stderr)
        return 1
    sizes = args.sizes or []
    if not sizes and not args.shapes:
        sizes = [8192]
    try:
        dims = parse_shapes(sizes, args.shapes)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    dtypes = list(args.dtype)
    if args.tensor_ratio > 0 and "fp32" not in dtypes:
        dtypes.append("fp32")
    configs = [Config(m, n, k, dt, args.layout)
               for (m, n, k) in dims for dt in dtypes]

    mode = (f"sustained {args.seconds:g}s/config" if args.seconds > 0
            else f"{args.repeats} iters/config")
    print("GEMM STRESS - TENSOR-CORE HAMMER")
    print(f"PyTorch {torch.__version__} | CUDA {torch.version.cuda}")
    print(f"devices={devices} | {mode} | warmup={args.warmup} | "
          f"validate={args.validate} tol={args.tol:g}")
    for idx in devices:
        print(f"  cuda:{idx} {names.get(idx, '?')} | "
              f"{describe_device(idx)}")
    print("-" * 72)

    if args.max_parallel < 0:
        print("ERROR: --max-parallel >= 0", file=sys.stderr)
        return 1

    per_device, interrupted = run_all(devices, configs, args)
    if interrupted:
        print("interrupted by user; partial results below", flush=True)
    else:
        apply_tensor_ratio(per_device, args)
    if args.json or args.csv:
        write_outputs(per_device, args)
    rc = report(per_device, args)
    return 130 if interrupted else rc


if __name__ == "__main__":
    raise SystemExit(main())
