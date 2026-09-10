import io

from deltasync.signature import FileSignature, build_signature, strong_checksum


def test_build_signature_from_bytes_splits_into_fixed_blocks():
    data = bytes(range(256)) * 4  # 1024 bytes
    sig = build_signature(data, block_size=100)
    assert sig.block_size == 100
    assert len(sig.blocks) == 11  # 10 full blocks of 100 + 1 of 24
    assert [b.length for b in sig.blocks[:-1]] == [100] * 10
    assert sig.blocks[-1].length == 24
    # offsets are contiguous and non-overlapping
    for i, block in enumerate(sig.blocks):
        assert block.offset == i * 100
        assert block.index == i


def test_build_signature_from_file_like_matches_bytes_version():
    data = bytes((i * 37) % 256 for i in range(5000))
    from_bytes = build_signature(data, block_size=512)
    from_file = build_signature(io.BytesIO(data), block_size=512)

    assert [(b.weak, b.strong) for b in from_bytes.blocks] == [
        (b.weak, b.strong) for b in from_file.blocks
    ]


def test_empty_source_yields_no_blocks():
    sig = build_signature(b"", block_size=64)
    assert sig.blocks == []


def test_candidates_for_groups_colliding_blocks():
    # Two identical blocks must share both weak and strong checksums and
    # both be returned as candidates for that weak value.
    block = b"repeatme_16bytes"  # 16 bytes
    data = block * 4  # four identical 16-byte blocks
    sig = build_signature(data, block_size=16)
    assert len(sig.blocks) == 4
    weak = sig.blocks[0].weak
    candidates = sig.candidates_for(weak)
    assert len(candidates) == 4
    assert all(c.strong == sig.blocks[0].strong for c in candidates)


def test_candidates_for_unknown_weak_is_empty():
    sig = build_signature(b"hello world this is a test", block_size=8)
    assert sig.candidates_for(0xDEADBEEF) == []


def test_signature_round_trips_through_dict():
    data = bytes((i * 53 + 7) % 256 for i in range(3000))
    sig = build_signature(data, block_size=256, strong_digest_size=20)
    restored = FileSignature.from_dict(sig.to_dict())

    assert restored.block_size == sig.block_size
    assert restored.strong_digest_size == sig.strong_digest_size
    assert len(restored.blocks) == len(sig.blocks)
    for a, b in zip(sig.blocks, restored.blocks):
        assert a == b
    # the rebuilt weak index must work, not just the flat block list
    for block in sig.blocks:
        assert block in restored.candidates_for(block.weak)


def test_strong_checksum_is_deterministic_and_sensitive_to_input():
    a = strong_checksum(b"hello")
    b = strong_checksum(b"hello")
    c = strong_checksum(b"hellp")
    assert a == b
    assert a != c
    assert len(a) == 16  # default digest size
    assert len(strong_checksum(b"hello", digest_size=32)) == 32
