"""A rolling weak checksum in the style of the one used by rsync.

The checksum for a window data[i : i + L] is the pair

    a(i) = sum(data[i : i + L])                              mod M
    b(i) = sum((L - j) * data[i + j] for j in range(L))       mod M

combined into a single integer as ``a | (b << 16)``. ``M`` is the prime
65521 (the same modulus Adler-32 uses), which keeps ``a`` and ``b`` inside
16 bits so the combined value fits a 32-bit integer.

The point of this checksum, as opposed to just re-hashing every window from
scratch, is that it can be updated in O(1) when the window slides forward by
one byte (``roll``), which is what makes an rsync-style byte-at-a-time scan
of the new file affordable.

Update derivation
------------------
Sliding the window from [i, i+L) to [i+1, i+1+L) drops ``data[i]`` (the
"out" byte) and adds ``data[i+L]`` (the "in" byte):

    a(i+1) = a(i) - out + in

For b, expanding the sums and re-indexing shows:

    b(i+1) = b(i) + a(i) - (L + 1) * out + in

using the *old* a(i), not the already-updated a(i+1) -- swapping in the new
a there is a natural-looking mistake (it type-checks and even passes a
one-shot "does this single step match brute force" test, since that kind of
test recomputes a(i) from the raw data rather than reusing the previous
step's running value) but silently double-subtracts ``out`` and only
diverges from the true rolling checksum once updates are chained across
multiple steps, which is why the test suite chains many rolls in sequence
rather than only checking isolated single steps.

Both updates are verified against the brute-force O(L) definition in
tests/test_rolling_checksum.py across randomized windows, positions, and
window lengths, including the modular-wraparound edge cases (repeated bytes,
all-zero windows, windows that make a or b cross the modulus boundary).
"""

from __future__ import annotations

MODULUS = 65521  # largest prime below 2**16, same modulus Adler-32 uses


def weak_checksum(data: bytes) -> int:
    """Compute the rolling weak checksum of ``data`` from scratch (O(len(data)))."""
    length = len(data)
    a = 0
    b = 0
    for offset, byte in enumerate(data):
        a += byte
        b += (length - offset) * byte
    a %= MODULUS
    b %= MODULUS
    return a | (b << 16)


class RollingChecksum:
    """A weak checksum for a fixed-size window that can be rolled forward in O(1).

    Usage::

        rc = RollingChecksum(data[0:block_size])
        value = rc.digest()
        rc.roll(out_byte=data[0], in_byte=data[block_size])
        value = rc.digest()   # now the checksum of data[1:block_size+1]
    """

    __slots__ = ("length", "_a", "_b")

    def __init__(self, window: bytes):
        self.length = len(window)
        a = 0
        b = 0
        for offset, byte in enumerate(window):
            a += byte
            b += (self.length - offset) * byte
        self._a = a % MODULUS
        self._b = b % MODULUS

    def digest(self) -> int:
        return self._a | (self._b << 16)

    def roll(self, out_byte: int, in_byte: int) -> int:
        """Slide the window forward by one byte and return the new digest.

        ``out_byte`` is the byte leaving the window (its current first byte);
        ``in_byte`` is the byte entering the window (the byte immediately
        after the current window).
        """
        L = self.length
        old_a = self._a
        new_a = (old_a - out_byte + in_byte) % MODULUS
        # NOTE: this must use old_a, not new_a -- b(i+1) = b(i) + a(i) -
        # (L+1)*out + in. Using new_a here double-subtracts out_byte, which
        # only shows up as a failure once rolls are chained (a single
        # from-scratch comparison per step can't catch it, since each such
        # comparison recomputes a(i) fresh rather than reusing the previous
        # step's output) -- caught by tests/test_rolling_checksum.py's
        # multi-step chained-roll tests, not a one-shot formula check.
        new_b = (self._b + old_a - (L + 1) * out_byte + in_byte) % MODULUS
        self._a = new_a
        self._b = new_b
        return self.digest()

    def reset(self, window: bytes) -> int:
        """Reinitialize from a fresh window (used after a COPY match, where the
        scan jumps ahead by a whole block instead of rolling byte-by-byte)."""
        self.__init__(window)  # type: ignore[misc]
        return self.digest()
