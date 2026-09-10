"""``deltasync``: an rsync-style delta sync CLI.

Subcommands
-----------
signature BASIS -o SIG.json [--block-size N]
    Compute a signature for BASIS and write it as JSON.

delta NEW SIG.json -o DELTA.bin
    Compute a binary delta that turns the file the signature was built from
    into NEW, using only NEW and the signature (not the original basis file
    -- this is the point: the machine holding the basis file only ever needs
    to send its signature, not its data, and the machine holding the new
    version only ever needs to send the delta back).

patch BASIS DELTA.bin -o OUT [--verify NEW]
    Reconstruct a file from BASIS + DELTA.bin. With --verify, also compares
    the result byte-for-byte against NEW and reports success/failure.

diff BASIS NEW [--block-size N]
    Convenience command: builds the signature and delta in memory (no files
    written) and prints a compression-ratio-style summary -- how many bytes
    would be copied vs. sent as literal data, and the delta's encoded size
    relative to NEW's raw size.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .delta import compute_delta, delta_stats
from .patch import DeltaFormatError, apply_delta, deserialize_delta, serialize_delta
from .signature import DEFAULT_BLOCK_SIZE, DEFAULT_STRONG_DIGEST_SIZE, FileSignature, build_signature

import json


def _cmd_signature(args: argparse.Namespace) -> int:
    basis_path = Path(args.basis)
    data = basis_path.read_bytes()
    sig = build_signature(data, block_size=args.block_size, strong_digest_size=args.digest_size)
    out_path = Path(args.output)
    out_path.write_text(json.dumps(sig.to_dict()))
    print(
        f"signature: {len(sig.blocks)} block(s) of up to {args.block_size} bytes "
        f"-> {out_path} ({out_path.stat().st_size} bytes)"
    )
    return 0


def _cmd_delta(args: argparse.Namespace) -> int:
    sig_path = Path(args.signature)
    sig = FileSignature.from_dict(json.loads(sig_path.read_text()))
    new_data = Path(args.new_file).read_bytes()

    ops = compute_delta(new_data, sig)
    stats = delta_stats(ops)
    encoded = serialize_delta(ops, sig.block_size, sig.strong_digest_size)

    out_path = Path(args.output)
    out_path.write_bytes(encoded)

    ratio = (len(encoded) / len(new_data) * 100) if new_data else 0.0
    print(
        f"delta: {stats['copy_ops']} copy op(s) ({stats['copied_bytes']} bytes), "
        f"{stats['data_ops']} literal op(s) ({stats['literal_bytes']} bytes) "
        f"-> {out_path} ({len(encoded)} bytes, {ratio:.1f}% of {args.new_file}'s {len(new_data)} bytes)"
    )
    return 0


def _cmd_patch(args: argparse.Namespace) -> int:
    basis = Path(args.basis).read_bytes()
    encoded = Path(args.delta).read_bytes()
    try:
        ops, _block_size, _digest_size = deserialize_delta(encoded)
    except DeltaFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    result = apply_delta(basis, ops)
    Path(args.output).write_bytes(result)
    print(f"patch: reconstructed {len(result)} bytes -> {args.output}")

    if args.verify:
        expected = Path(args.verify).read_bytes()
        if expected == result:
            print(f"verify: OK ({args.output} matches {args.verify})")
        else:
            print(f"verify: MISMATCH ({args.output} does NOT match {args.verify})", file=sys.stderr)
            return 1
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    basis = Path(args.basis).read_bytes()
    new_data = Path(args.new_file).read_bytes()

    sig = build_signature(basis, block_size=args.block_size, strong_digest_size=args.digest_size)
    ops = compute_delta(new_data, sig)
    stats = delta_stats(ops)
    encoded = serialize_delta(ops, sig.block_size, sig.strong_digest_size)

    reconstructed = apply_delta(basis, ops)
    correct = reconstructed == new_data

    ratio = (len(encoded) / len(new_data) * 100) if new_data else 0.0
    print(f"basis:     {args.basis} ({len(basis)} bytes)")
    print(f"new:       {args.new_file} ({len(new_data)} bytes)")
    print(f"block size: {args.block_size} bytes, {len(sig.blocks)} basis block(s)")
    print(
        f"delta:     {stats['copy_ops']} copy op(s) covering {stats['copied_bytes']} bytes, "
        f"{stats['data_ops']} literal op(s) covering {stats['literal_bytes']} bytes"
    )
    print(f"encoded delta size: {len(encoded)} bytes ({ratio:.1f}% of new file size)")
    print(f"round-trip check:   {'OK' if correct else 'MISMATCH'}")
    return 0 if correct else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deltasync", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_sig = sub.add_parser("signature", help="compute a signature for a basis file")
    p_sig.add_argument("basis", help="path to the old/basis file")
    p_sig.add_argument("-o", "--output", required=True, help="path to write the signature JSON to")
    p_sig.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE)
    p_sig.add_argument("--digest-size", type=int, default=DEFAULT_STRONG_DIGEST_SIZE)
    p_sig.set_defaults(func=_cmd_signature)

    p_delta = sub.add_parser("delta", help="compute a delta from a new file and a signature")
    p_delta.add_argument("new_file", help="path to the new version of the file")
    p_delta.add_argument("signature", help="path to a signature JSON produced by 'signature'")
    p_delta.add_argument("-o", "--output", required=True, help="path to write the binary delta to")
    p_delta.set_defaults(func=_cmd_delta)

    p_patch = sub.add_parser("patch", help="reconstruct a file from a basis file and a delta")
    p_patch.add_argument("basis", help="path to the old/basis file")
    p_patch.add_argument("delta", help="path to a binary delta produced by 'delta'")
    p_patch.add_argument("-o", "--output", required=True, help="path to write the reconstructed file to")
    p_patch.add_argument("--verify", help="optional path to the expected new file, to verify against")
    p_patch.set_defaults(func=_cmd_patch)

    p_diff = sub.add_parser("diff", help="one-shot signature+delta+verify summary, no files written")
    p_diff.add_argument("basis", help="path to the old/basis file")
    p_diff.add_argument("new_file", help="path to the new version of the file")
    p_diff.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE)
    p_diff.add_argument("--digest-size", type=int, default=DEFAULT_STRONG_DIGEST_SIZE)
    p_diff.set_defaults(func=_cmd_diff)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
