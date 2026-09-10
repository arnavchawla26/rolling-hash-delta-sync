import random

import pytest

from deltasync.rolling_checksum import MODULUS, RollingChecksum, weak_checksum


def brute_force(data: bytes) -> int:
    """The O(L) from-scratch definition, used as the oracle for the O(1)
    rolling update."""
    length = len(data)
    a = sum(data) % MODULUS
    b = sum((length - j) * byte for j, byte in enumerate(data)) % MODULUS
    return a | (b << 16)


def test_weak_checksum_matches_brute_force_definition():
    assert weak_checksum(b"") == 0
    assert weak_checksum(b"a") == brute_force(b"a")
    assert weak_checksum(b"hello world") == brute_force(b"hello world")


@pytest.mark.parametrize("seed", range(20))
def test_rolling_matches_brute_force_over_random_data(seed):
    rng = random.Random(seed)
    length = rng.randint(50, 400)
    data = bytes(rng.randint(0, 255) for _ in range(length))
    window = rng.choice([1, 4, 8, 16, 37, 64])
    if window >= length:
        pytest.skip("window too large for this random length")

    rc = RollingChecksum(data[0:window])
    assert rc.digest() == brute_force(data[0:window])

    for i in range(0, length - window - 1):
        expected = brute_force(data[i + 1 : i + 1 + window])
        actual = rc.roll(out_byte=data[i], in_byte=data[i + window])
        assert actual == expected, f"mismatch at i={i}, seed={seed}, window={window}"
        assert rc.digest() == expected


def test_rolling_handles_all_zero_window():
    data = bytes(30)
    rc = RollingChecksum(data[0:8])
    for i in range(0, len(data) - 8 - 1):
        assert rc.roll(out_byte=data[i], in_byte=data[i + 8]) == 0


def test_rolling_handles_repeated_byte_window():
    # A constant byte still exercises the modular arithmetic (a and b both
    # grow with the window sum, not with byte diversity).
    data = bytes([200] * 40)
    window = 10
    rc = RollingChecksum(data[0:window])
    for i in range(0, len(data) - window - 1):
        expected = brute_force(data[i + 1 : i + 1 + window])
        assert rc.roll(out_byte=data[i], in_byte=data[i + window]) == expected


def test_rolling_wraps_around_modulus():
    # Force a and b to cross the MODULUS boundary repeatedly by using
    # large bytes and a long window.
    data = bytes([255] * 20 + [0] * 20 + [255] * 20)
    window = 50
    rc = RollingChecksum(data[0:window])
    for i in range(0, len(data) - window - 1):
        expected = brute_force(data[i + 1 : i + 1 + window])
        assert rc.roll(out_byte=data[i], in_byte=data[i + window]) == expected


def test_reset_reinitializes_from_scratch():
    rc = RollingChecksum(b"aaaaaaaa")
    rc.roll(out_byte=ord("a"), in_byte=ord("z"))
    rc.reset(b"different")
    assert rc.digest() == weak_checksum(b"different")


def test_two_different_windows_can_collide_but_usually_dont():
    # Not a correctness assertion about collisions (weak checksums are
    # *expected* to collide sometimes -- that's why delta.py always
    # confirms with a strong hash) -- just a sanity check that the
    # checksum isn't a constant or otherwise degenerate.
    values = {weak_checksum(bytes([i]) * 8) for i in range(256)}
    assert len(values) > 200
