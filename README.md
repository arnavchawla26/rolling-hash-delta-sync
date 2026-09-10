# rolling-hash-delta-sync

An rsync-style binary delta synchronization tool, built from scratch: given
an old ("basis") version of a file and a new version, it computes a small
*delta* that describes how to turn the old version into the new one — and
can apply that delta on the machine that only has the old version, without
ever needing the new file to cross the wire in full.

This is the same core idea behind `rsync`, `librsync`, and Dropbox/Google
Drive's "block-level sync": don't re-send a whole file just because a few
bytes changed somewhere in the middle.

## How it works

1. **Signature** (`deltasync signature`) — the machine holding the *old*
   file splits it into fixed-size blocks and computes, for each block, a
   cheap **weak checksum** (a rolling Adler-32-style sum) and a
   collision-resistant **strong checksum** (`blake2b`, truncated to 128
   bits by default). Only this signature — not the file itself — needs to
   be sent to the machine that has the new version.

2. **Delta** (`deltasync delta`) — the machine holding the *new* file slides
   a byte-at-a-time window over it. At every position it checks whether the
   window's weak checksum appears in the signature; if so, it confirms with
   the strong checksum (weak checksums collide sometimes — that's expected
   and fine, it's why there's a second, stronger check). On a real match, it
   emits a `COPY(offset, length)` op referencing the matching block in the
   *old* file and jumps the window forward by a whole block. On no match, it
   emits the current byte as literal data and slides forward by one byte.
   The point of the **rolling** part of the weak checksum is that recomputing
   it after a 1-byte slide is O(1), not O(block size) — that's what makes
   scanning byte-by-byte through the whole new file affordable.

3. **Patch** (`deltasync patch`) — the machine holding the *old* file (and
   now the delta) reconstructs the new file by resolving every `COPY` op
   against its own basis file and concatenating in every literal `DATA` op.

The classic stress case this design has to handle: inserting or deleting
even a single byte near the start of a large file shifts every subsequent
fixed-offset block boundary, so a naive "diff corresponding byte ranges"
approach would find zero matches after the edit point. Because the scan
looks for a block match at *every* byte offset (not just at block-aligned
offsets), it re-synchronizes and keeps finding matches in the shifted
tail — see `test_insertion_at_the_very_start_still_matches_the_rest` in the
test suite, and `shifted_binary_blob` in the benchmark.

## Delta file format

Deltas serialize to a small binary container: a 4-byte magic (`DSYN`), a
version byte, the block size and strong-digest-size used, an op count, the
ops themselves, and a **CRC-32 trailer** over everything before it. The CRC
exists because a same-length, single-bit corruption in a delta can still
parse as a structurally valid op stream while reconstructing the wrong
file — a size-only or "did it parse" check would never catch that; only
comparing an actual checksum does. See `tests/test_delta_and_patch.py::test_deserialize_detects_bit_flip_corruption_via_crc`.

## Tech stack

Pure Python (3.9+), standard library only (`hashlib.blake2b`, `zlib.crc32`,
`struct`, `argparse`) — no third-party runtime dependencies. `pytest` and
`pyflakes` for development.

## Installing / running

```bash
pip install -e ".[dev]"

# Compute a signature for the old version of a file
deltasync signature old_version.bin -o sig.json --block-size 4096

# On the machine with the new version: compute a delta against that
# signature (never needs old_version.bin itself)
deltasync delta new_version.bin sig.json -o changes.deltasync

# On the machine with the old version: apply the delta to reconstruct
# the new version, and verify it against a known-good copy if you have one
deltasync patch old_version.bin changes.deltasync -o reconstructed.bin --verify new_version.bin

# Or, for a quick one-shot summary (signature + delta + round-trip check,
# no files written) when both versions are on the same machine:
deltasync diff old_version.bin new_version.bin
```

Run the test suite:

```bash
pip install -e ".[dev]"
pytest
pyflakes src tests benchmarks
```

Run the savings benchmark (measures actual delta size vs. re-sending the
whole file, across a growing log file, a source file with scattered edits,
and a binary blob with a mid-file insertion):

```bash
python benchmarks/compare_savings.py
```

Sample output on this machine:

| fixture | new size | delta size | % of new size |
|---|---|---|---|
| growing_log_file (append-only) | 135,732 B | 5,856 B | 4.3% |
| shifted_binary_blob (mid-file insert) | 304,096 B | 11,095 B | 3.7% |
| edited_source_file (scattered small edits, default 1024B blocks) | 9,662 B | 5,679 B | 58.8% |

That third row is a real, measured result, not an oversight: with an
edit-dense file where changes are scattered across roughly 10% of many
small (~140-byte) functions and a default block size of 1024 bytes, most
blocks end up containing at least one changed byte, so most blocks fail
their strong-checksum check and get sent as literal data. Delta sync's
savings come from the *ratio of unchanged-to-changed regions inside each
block* — a smaller block size trades more per-block signature/op overhead
for finer-grained matching around scattered edits, and vice versa for
sparser localized edits. `deltasync signature --block-size` is exposed
specifically so the block size can be tuned to the edit pattern of what's
being synced, rather than assuming one size fits every case.

## Project layout

```
src/deltasync/
  rolling_checksum.py  — O(1)-updatable weak checksum
  signature.py          — basis-file signature (weak+strong hash per block)
  delta.py               — the byte-at-a-time scan that produces COPY/DATA ops
  patch.py                — binary delta (de)serialization + reconstruction
  cli.py                    — the `deltasync` command-line tool
tests/                        — 67 tests: rolling-checksum correctness against
                                 a brute-force oracle, signature building,
                                 delta/patch round-trips (including randomized
                                 fuzz edits and the insertion-shift stress
                                 case), CLI subprocess tests, and delta-format
                                 corruption detection
benchmarks/
  generate_fixtures.py   — reproducible synthetic (basis, new) file pairs
  compare_savings.py       — measures real delta-size savings per fixture
```

## Current status

**v1 shipped.** Signature generation, delta computation, patch application,
binary delta serialization with CRC-32 integrity checking, and the
`deltasync` CLI (`signature` / `delta` / `patch` / `diff` subcommands) are
all implemented and tested (67 tests, `pyflakes`-clean). The savings
benchmark runs against three realistic fixture scenarios and confirms both
correctness (every fixture round-trips exactly) and the actual bandwidth
savings (not just the theoretical ones).

Real bug caught and fixed before this shipped: the rolling checksum's
update formula for its second term (`b`) initially reused the
*already-updated* `a` instead of the old one — a subtle off-by-one-term
error that happens to satisfy a naive "one isolated step matches brute
force" check (since that kind of check recomputes `a` fresh from the raw
data rather than reusing a running value) but silently diverges once
updates are chained across a real multi-step scan. Caught by chaining many
rolls in sequence in `tests/test_rolling_checksum.py` rather than only
checking single steps in isolation — see the comment in
`rolling_checksum.py::RollingChecksum.roll` for the full derivation.

Not yet built (possible follow-ups, not started): a network transport
(the CLI operates on local files only — the "signature crosses one way,
delta crosses the other" protocol is implemented as pure functions, but
there's no actual client/server or SSH-style remote invocation on top of
it); multi-file / directory tree sync; adaptive block sizing; compression
of literal `DATA` op payloads before they go into the delta container.
