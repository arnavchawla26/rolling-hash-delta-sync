"""Generates synthetic (basis, new) file pairs for the savings benchmark.

Kept as a generator script rather than committing large binary/text fixtures
directly -- the fixtures are reproducible from a fixed seed and regenerated
on demand instead of bloating the repository.
"""

from __future__ import annotations

import random


def growing_log_file(seed: int = 1, initial_lines: int = 2000, appended_lines: int = 50):
    """Simulates the most common real-world rsync use case: a log file that
    only ever grows by appending new lines at the end."""
    rng = random.Random(seed)
    levels = ["INFO", "WARN", "ERROR", "DEBUG"]

    def make_lines(n, start=0):
        return [
            f"2026-09-{rng.randint(1,28):02d} {rng.randint(0,23):02d}:{rng.randint(0,59):02d}:{rng.randint(0,59):02d} "
            f"[{rng.choice(levels)}] worker-{rng.randint(0,15)} handled request {start + i} "
            f"in {rng.randint(1,900)}ms"
            for i in range(n)
        ]

    basis_lines = make_lines(initial_lines)
    basis = ("\n".join(basis_lines) + "\n").encode()
    new_lines = basis_lines + make_lines(appended_lines, start=initial_lines)
    new = ("\n".join(new_lines) + "\n").encode()
    return basis, new


def edited_source_file(seed: int = 2, num_functions: int = 80):
    """Simulates editing a handful of functions in the middle of a large
    source file -- most of the file is unchanged, a few scattered chunks
    differ."""
    rng = random.Random(seed)

    def make_function(i):
        return (
            f"def worker_{i}(x, y, z):\n"
            f"    total = x * {rng.randint(1,50)} + y - {rng.randint(1,50)}\n"
            f"    if total > {rng.randint(0,1000)}:\n"
            f"        return total // {rng.randint(1,9)}\n"
            f"    return z + {rng.randint(1,1000)}\n\n"
        )

    functions = [make_function(i) for i in range(num_functions)]
    basis = "".join(functions).encode()

    edited = list(functions)
    edit_indices = rng.sample(range(num_functions), k=max(1, num_functions // 10))
    for i in edit_indices:
        edited[i] = make_function(i) + f"    # edited variant of worker_{i}\n\n"
    new = "".join(edited).encode()
    return basis, new


def shifted_binary_blob(seed: int = 3, size: int = 300_000, insert_at: int = 150_000, insert_size: int = 4096):
    """Inserts a chunk in the middle of an otherwise-random binary blob --
    the stress case where every block boundary after the insertion point
    shifts, and only the rolling byte-by-byte scan can still find matches."""
    rng = random.Random(seed)
    basis = bytes(rng.randint(0, 255) for _ in range(size))
    inserted = bytes(rng.randint(0, 255) for _ in range(insert_size))
    new = basis[:insert_at] + inserted + basis[insert_at:]
    return basis, new


FIXTURES = {
    "growing_log_file": growing_log_file,
    "edited_source_file": edited_source_file,
    "shifted_binary_blob": shifted_binary_blob,
}
