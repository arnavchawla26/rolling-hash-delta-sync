import random

import pytest

from deltasync.delta import CopyOp, DataOp, compute_delta, delta_stats
from deltasync.patch import (
    DeltaFormatError,
    apply_delta,
    deserialize_delta,
    serialize_delta,
)
from deltasync.signature import build_signature


def roundtrip(basis: bytes, new: bytes, block_size: int = 64) -> bytes:
    sig = build_signature(basis, block_size=block_size)
    ops = compute_delta(new, sig)
    return apply_delta(basis, ops)


def test_identical_files_round_trip_as_pure_copies():
    data = bytes((i * 17) % 256 for i in range(2000))
    sig = build_signature(data, block_size=100)
    ops = compute_delta(data, sig)
    assert all(isinstance(op, CopyOp) for op in ops)
    assert apply_delta(data, ops) == data


def test_completely_different_files_round_trip_as_literal_data():
    basis = bytes((i * 3) % 256 for i in range(500))
    new = bytes((i * 251 + 9) % 256 for i in range(500))
    assert roundtrip(basis, new, block_size=64) == new


@pytest.mark.parametrize("seed", range(15))
def test_random_edits_always_round_trip(seed):
    rng = random.Random(seed)
    basis = bytes(rng.randint(0, 255) for _ in range(rng.randint(200, 3000)))

    # Apply a handful of random edits: substitutions, insertions, deletions.
    new = bytearray(basis)
    for _ in range(rng.randint(1, 8)):
        if not new:
            break
        kind = rng.choice(["substitute", "insert", "delete"])
        pos = rng.randint(0, len(new) - 1)
        if kind == "substitute":
            new[pos] = rng.randint(0, 255)
        elif kind == "insert":
            new[pos:pos] = bytes(rng.randint(0, 255) for _ in range(rng.randint(1, 30)))
        else:
            end = min(len(new), pos + rng.randint(1, 30))
            del new[pos:end]

    block_size = rng.choice([8, 16, 32, 64])
    result = roundtrip(basis, bytes(new), block_size=block_size)
    assert result == bytes(new), f"seed={seed} block_size={block_size}"


def test_insertion_at_the_very_start_still_matches_the_rest():
    # The classic rsync stress case: inserting one byte at the front shifts
    # every subsequent block boundary, so a naive fixed-offset diff would
    # find zero matches. The rolling byte-by-byte scan should still recover
    # (re-align on) most of the unchanged tail.
    basis = bytes((i * 29) % 256 for i in range(4096))
    new = bytes([7]) + basis  # shift everything by one byte
    sig = build_signature(basis, block_size=128)
    ops = compute_delta(new, sig)
    stats = delta_stats(ops)

    assert apply_delta(basis, ops) == new
    # Most of the file should be recovered as copies despite the shift.
    assert stats["copied_bytes"] >= len(basis) - 128 * 2


def test_appending_data_only_adds_a_trailing_literal_op():
    basis = bytes((i * 5) % 256 for i in range(2000))
    new = basis + b"tacked on at the end, not a multiple of the block size"
    sig = build_signature(basis, block_size=100)
    ops = compute_delta(new, sig)

    assert apply_delta(basis, ops) == new
    assert isinstance(ops[-1], DataOp)
    assert ops[-1].data == new[len(basis):]
    # everything before the append should have been pure copies
    assert all(isinstance(op, CopyOp) for op in ops[:-1])


def test_prepending_and_truncating_together():
    basis = bytes((i * 41) % 256 for i in range(5000))
    new = b"HEADER" + basis[1000:4000]  # drop the front/tail, add a header
    result = roundtrip(basis, new, block_size=256)
    assert result == new


def test_empty_basis_forces_all_literal():
    new = b"brand new content, no basis to copy from"
    sig = build_signature(b"", block_size=64)
    ops = compute_delta(new, sig)
    assert len(ops) == 1 and isinstance(ops[0], DataOp)
    assert apply_delta(b"", ops) == new


def test_empty_new_file_yields_no_ops():
    sig = build_signature(b"some basis content" * 20, block_size=32)
    ops = compute_delta(b"", sig)
    assert ops == []
    assert apply_delta(b"anything", ops) == b""


def test_new_file_shorter_than_block_size_is_one_literal_op():
    sig = build_signature(b"x" * 1000, block_size=256)
    new = b"short"
    ops = compute_delta(new, sig)
    assert ops == [DataOp(new)]
    assert apply_delta(b"x" * 1000, ops) == new


def test_a_single_byte_change_only_invalidates_its_own_block():
    basis = bytes((i * 13) % 256 for i in range(3200))  # 50 blocks of 64
    new = bytearray(basis)
    new[64 * 20 + 5] ^= 0xFF  # flip a bit in the middle of block 20
    sig = build_signature(basis, block_size=64)
    ops = compute_delta(bytes(new), sig)

    assert apply_delta(basis, ops) == bytes(new)
    # 49 of the 50 blocks are untouched and should come back as copies;
    # only the edited block (or its neighborhood) should be literal.
    stats = delta_stats(ops)
    assert stats["copied_bytes"] >= 64 * 45


def test_delta_is_much_smaller_than_new_file_for_a_localized_edit():
    # This is the actual value proposition of rsync-style sync: measure it,
    # don't just assert round-trip correctness. A 200 KB file with one
    # small edit should produce a delta far smaller than the file itself.
    rng = random.Random(42)
    basis = bytes(rng.randint(0, 255) for _ in range(200_000))
    new = bytearray(basis)
    new[100_000:100_050] = bytes(rng.randint(0, 255) for _ in range(50))

    sig = build_signature(basis, block_size=2048)
    ops = compute_delta(bytes(new), sig)
    encoded = serialize_delta(ops, sig.block_size, sig.strong_digest_size)

    assert apply_delta(basis, ops) == bytes(new)
    assert len(encoded) < len(new) * 0.05  # delta should be under 5% of file size


# ---- serialization ----------------------------------------------------


def test_serialize_deserialize_round_trip_preserves_ops():
    basis = bytes((i * 7) % 256 for i in range(1000))
    new = basis[:400] + b"NEWSTUFF" * 5 + basis[600:]
    sig = build_signature(basis, block_size=50)
    ops = compute_delta(new, sig)

    encoded = serialize_delta(ops, sig.block_size, sig.strong_digest_size)
    decoded_ops, block_size, digest_size = deserialize_delta(encoded)

    assert block_size == sig.block_size
    assert digest_size == sig.strong_digest_size
    assert decoded_ops == ops
    assert apply_delta(basis, decoded_ops) == new


def test_deserialize_rejects_bad_magic():
    with pytest.raises(DeltaFormatError, match="magic"):
        deserialize_delta(b"NOTA" + b"\x00" * 20)


def test_deserialize_rejects_truncated_input():
    basis = b"x" * 500
    new = b"y" * 500
    sig = build_signature(basis, block_size=50)
    ops = compute_delta(new, sig)
    encoded = serialize_delta(ops, sig.block_size, sig.strong_digest_size)

    with pytest.raises(DeltaFormatError):
        deserialize_delta(encoded[: len(encoded) - 6])  # chop off part of a length/CRC


def test_deserialize_detects_bit_flip_corruption_via_crc():
    # A same-length, single-bit corruption in the middle of the payload
    # must be caught by the CRC even though the byte stream still parses
    # as a structurally valid op stream.
    basis = bytes((i * 19) % 256 for i in range(2000))
    new = basis[:900] + b"CHANGED-SECTION-HERE" + basis[950:]
    sig = build_signature(basis, block_size=64)
    ops = compute_delta(new, sig)
    encoded = bytearray(serialize_delta(ops, sig.block_size, sig.strong_digest_size))

    # Flip one bit somewhere in the middle of the body (not the header, to
    # make sure a structurally-valid-but-wrong stream is still caught).
    flip_pos = len(encoded) // 2
    encoded[flip_pos] ^= 0x01

    with pytest.raises(DeltaFormatError, match="CRC-32"):
        deserialize_delta(bytes(encoded))


def test_apply_delta_rejects_copy_op_outside_basis_bounds():
    with pytest.raises(ValueError, match="outside basis"):
        apply_delta(b"short", [CopyOp(offset=0, length=100)])
