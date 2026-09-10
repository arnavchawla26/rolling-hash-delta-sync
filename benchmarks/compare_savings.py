#!/usr/bin/env python3
"""Measures, rather than assumes, how much a deltasync sync saves over
re-sending the whole new file, across a few realistic fixture scenarios.

Run with: python benchmarks/compare_savings.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from deltasync.delta import compute_delta, delta_stats  # noqa: E402
from deltasync.patch import apply_delta, serialize_delta  # noqa: E402
from deltasync.signature import build_signature  # noqa: E402
from generate_fixtures import FIXTURES  # noqa: E402


def run_one(name: str, make_fixture, block_size: int = 1024) -> None:
    basis, new = make_fixture()

    t0 = time.perf_counter()
    sig = build_signature(basis, block_size=block_size)
    t1 = time.perf_counter()
    ops = compute_delta(new, sig)
    t2 = time.perf_counter()
    encoded = serialize_delta(ops, sig.block_size, sig.strong_digest_size)
    t3 = time.perf_counter()

    reconstructed = apply_delta(basis, ops)
    assert reconstructed == new, f"{name}: round-trip FAILED"

    stats = delta_stats(ops)
    ratio = len(encoded) / len(new) * 100 if new else 0.0

    print(f"[{name}]")
    print(f"  basis size:        {len(basis):>10,} bytes")
    print(f"  new size:          {len(new):>10,} bytes")
    print(f"  delta size:        {len(encoded):>10,} bytes  ({ratio:.2f}% of new size)")
    print(f"  copy/literal ops:  {stats['copy_ops']} / {stats['data_ops']}")
    print(
        f"  timing:            signature {(t1 - t0) * 1000:.1f}ms, "
        f"delta {(t2 - t1) * 1000:.1f}ms, serialize {(t3 - t2) * 1000:.1f}ms"
    )
    print("  round-trip:        OK")
    print()


def main() -> None:
    for name, make_fixture in FIXTURES.items():
        run_one(name, make_fixture)


if __name__ == "__main__":
    main()
