"""Compute a delta between a "basis" (old) file's signature and a new version
of the file, using the classic rsync algorithm: slide a rolling weak checksum
byte-by-byte over the new data; on a weak-checksum hit, confirm with a strong
hash and, if it matches, emit a COPY of the matching basis block and jump the
window forward by a whole block; otherwise emit the byte as a literal and
advance by one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Union

from .rolling_checksum import RollingChecksum
from .signature import FileSignature, strong_checksum


@dataclass(frozen=True)
class CopyOp:
    """Copy ``length`` bytes from the basis file starting at ``offset``."""

    offset: int
    length: int


@dataclass(frozen=True)
class DataOp:
    """Literal bytes not found (as a whole block) in the basis file."""

    data: bytes


Op = Union[CopyOp, DataOp]


def compute_delta(new_data: bytes, signature: FileSignature) -> List[Op]:
    block_size = signature.block_size
    n = len(new_data)
    if n == 0:
        return []

    ops: List[Op] = []
    literal_buffer = bytearray()

    def flush_literal() -> None:
        if literal_buffer:
            ops.append(DataOp(bytes(literal_buffer)))
            literal_buffer.clear()

    if n < block_size:
        # Too short to contain even one full basis-sized block; nothing to
        # match against (matches are only ever whole blocks), so the whole
        # file is a literal.
        return [DataOp(bytes(new_data))]

    last_start = n - block_size
    rc = RollingChecksum(new_data[0:block_size])
    i = 0

    while i <= last_start:
        weak = rc.digest()
        candidates = signature.candidates_for(weak)
        matched = None
        if candidates:
            window = new_data[i : i + block_size]
            strong = strong_checksum(window, signature.strong_digest_size)
            for cand in candidates:
                if cand.length == block_size and cand.strong == strong:
                    matched = cand
                    break

        if matched is not None:
            flush_literal()
            ops.append(CopyOp(offset=matched.offset, length=matched.length))
            i += block_size
            if i <= last_start:
                rc.reset(new_data[i : i + block_size])
        else:
            literal_buffer.append(new_data[i])
            next_i = i + 1
            if next_i <= last_start:
                rc.roll(out_byte=new_data[i], in_byte=new_data[i + block_size])
            i = next_i

    if i < n:
        literal_buffer.extend(new_data[i:n])
    flush_literal()

    return ops


def delta_stats(ops: List[Op]) -> dict:
    """Summary stats used by the CLI and benchmarks: bytes copied vs. sent
    literally, and the resulting delta size (a COPY op costs a small fixed
    header; a DATA op costs its literal bytes plus a header)."""
    copied = sum(op.length for op in ops if isinstance(op, CopyOp))
    literal = sum(len(op.data) for op in ops if isinstance(op, DataOp))
    copy_ops = sum(1 for op in ops if isinstance(op, CopyOp))
    data_ops = sum(1 for op in ops if isinstance(op, DataOp))
    # Rough serialized-size estimate: each op has a 1-byte tag; a CopyOp then
    # two 8-byte integers (offset, length); a DataOp then an 8-byte length
    # prefix plus its literal bytes. See patch.serialize_ops for the exact
    # on-disk format.
    encoded_size = copy_ops * (1 + 8 + 8) + data_ops * (1 + 8) + literal
    return {
        "copied_bytes": copied,
        "literal_bytes": literal,
        "copy_ops": copy_ops,
        "data_ops": data_ops,
        "encoded_delta_size": encoded_size,
    }
