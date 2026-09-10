"""Reconstruct a file from a basis (old) file plus a delta, and serialize /
deserialize deltas to a small binary container format.

Container format (all multi-byte integers big-endian)::

    magic            4 bytes   b"DSYN"
    version          1 byte    0x01
    block_size       4 bytes   uint32
    strong_digest_sz 1 byte    uint8
    op_count         4 bytes   uint32
    ops...
    crc32            4 bytes   uint32   CRC-32 of every byte before it

Each op starts with a 1-byte tag:

    0x01  COPY   followed by offset (uint64) and length (uint64)
    0x02  DATA   followed by length (uint64) and that many literal bytes

The trailing CRC-32 guards against silent corruption (a flipped bit that
still parses as *some* valid-looking op stream but reconstructs the wrong
file) -- the same class of gap that huffman-lz77-compressor's fuzz tests
found needed a checksum for, rather than trusting "no exception raised".
"""

from __future__ import annotations

import struct
import zlib
from typing import List

from .delta import CopyOp, DataOp, Op

MAGIC = b"DSYN"
VERSION = 1

_COPY_TAG = 0x01
_DATA_TAG = 0x02


def serialize_delta(ops: List[Op], block_size: int, strong_digest_size: int) -> bytes:
    body = bytearray()
    body += MAGIC
    body += struct.pack(">B", VERSION)
    body += struct.pack(">I", block_size)
    body += struct.pack(">B", strong_digest_size)
    body += struct.pack(">I", len(ops))

    for op in ops:
        if isinstance(op, CopyOp):
            body += struct.pack(">BQQ", _COPY_TAG, op.offset, op.length)
        elif isinstance(op, DataOp):
            body += struct.pack(">BQ", _DATA_TAG, len(op.data))
            body += op.data
        else:  # pragma: no cover - defensive
            raise TypeError(f"unknown op type: {type(op)!r}")

    crc = zlib.crc32(bytes(body)) & 0xFFFFFFFF
    body += struct.pack(">I", crc)
    return bytes(body)


class DeltaFormatError(ValueError):
    """Raised when a serialized delta is truncated, has a bad magic/version,
    or fails its CRC-32 integrity check."""


def deserialize_delta(data: bytes):
    if len(data) < len(MAGIC) + 1 + 4 + 1 + 4 + 4:
        raise DeltaFormatError("delta is too short to be well-formed")

    if data[: len(MAGIC)] != MAGIC:
        raise DeltaFormatError("bad magic bytes; not a deltasync delta")

    crc_stored = struct.unpack(">I", data[-4:])[0]
    crc_actual = zlib.crc32(data[:-4]) & 0xFFFFFFFF
    if crc_stored != crc_actual:
        raise DeltaFormatError(
            f"CRC-32 mismatch (stored {crc_stored:#010x}, computed {crc_actual:#010x}); "
            "delta is corrupted"
        )

    offset = len(MAGIC)
    (version,) = struct.unpack_from(">B", data, offset)
    offset += 1
    if version != VERSION:
        raise DeltaFormatError(f"unsupported delta format version: {version}")

    (block_size,) = struct.unpack_from(">I", data, offset)
    offset += 4
    (strong_digest_size,) = struct.unpack_from(">B", data, offset)
    offset += 1
    (op_count,) = struct.unpack_from(">I", data, offset)
    offset += 4

    ops: List[Op] = []
    body_end = len(data) - 4  # exclude trailing CRC
    for _ in range(op_count):
        if offset >= body_end:
            raise DeltaFormatError("delta truncated while reading an op tag")
        (tag,) = struct.unpack_from(">B", data, offset)
        offset += 1
        if tag == _COPY_TAG:
            if offset + 16 > body_end:
                raise DeltaFormatError("delta truncated while reading a COPY op")
            op_offset, op_length = struct.unpack_from(">QQ", data, offset)
            offset += 16
            ops.append(CopyOp(offset=op_offset, length=op_length))
        elif tag == _DATA_TAG:
            if offset + 8 > body_end:
                raise DeltaFormatError("delta truncated while reading a DATA op length")
            (length,) = struct.unpack_from(">Q", data, offset)
            offset += 8
            if offset + length > body_end:
                raise DeltaFormatError("delta truncated while reading DATA op payload")
            ops.append(DataOp(data=data[offset : offset + length]))
            offset += length
        else:
            raise DeltaFormatError(f"unknown op tag: {tag:#04x}")

    if offset != body_end:
        raise DeltaFormatError("trailing garbage after the last op")

    return ops, block_size, strong_digest_size


def apply_delta(basis: bytes, ops: List[Op]) -> bytes:
    """Reconstruct the new file from the basis file and a list of ops."""
    out = bytearray()
    basis_len = len(basis)
    for op in ops:
        if isinstance(op, CopyOp):
            end = op.offset + op.length
            if op.offset < 0 or end > basis_len:
                raise ValueError(
                    f"COPY op references basis range [{op.offset}, {end}) "
                    f"outside basis file of length {basis_len}"
                )
            out += basis[op.offset : end]
        elif isinstance(op, DataOp):
            out += op.data
        else:  # pragma: no cover - defensive
            raise TypeError(f"unknown op type: {type(op)!r}")
    return bytes(out)
