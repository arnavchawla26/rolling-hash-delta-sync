"""Build a signature for a "basis" (old) file: one (weak, strong) hash pair per
fixed-size block, plus a weak-checksum -> candidate-blocks index for fast
lookup during delta computation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import BinaryIO, Dict, List, Union

from .rolling_checksum import weak_checksum

DEFAULT_BLOCK_SIZE = 4096
DEFAULT_STRONG_DIGEST_SIZE = 16  # bytes; blake2b truncated to 128 bits


def strong_checksum(data: bytes, digest_size: int = DEFAULT_STRONG_DIGEST_SIZE) -> bytes:
    return hashlib.blake2b(data, digest_size=digest_size).digest()


@dataclass(frozen=True)
class BlockSignature:
    index: int
    offset: int
    length: int
    weak: int
    strong: bytes


@dataclass
class FileSignature:
    block_size: int
    strong_digest_size: int
    blocks: List[BlockSignature] = field(default_factory=list)
    # weak checksum -> list of block indices sharing that weak checksum,
    # ordered by index. A dict of lists rather than a dict of single blocks
    # because distinct blocks legitimately collide on the 32-bit weak sum.
    _weak_index: Dict[int, List[int]] = field(default_factory=dict, repr=False)

    def candidates_for(self, weak: int) -> List[BlockSignature]:
        return [self.blocks[i] for i in self._weak_index.get(weak, ())]

    def _index_block(self, block: BlockSignature) -> None:
        self._weak_index.setdefault(block.weak, []).append(block.index)

    def to_dict(self) -> dict:
        """A JSON-serializable form, for persisting a signature to disk."""
        return {
            "block_size": self.block_size,
            "strong_digest_size": self.strong_digest_size,
            "blocks": [
                {
                    "index": b.index,
                    "offset": b.offset,
                    "length": b.length,
                    "weak": b.weak,
                    "strong": b.strong.hex(),
                }
                for b in self.blocks
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FileSignature":
        sig = cls(block_size=data["block_size"], strong_digest_size=data["strong_digest_size"])
        for raw in data["blocks"]:
            block = BlockSignature(
                index=raw["index"],
                offset=raw["offset"],
                length=raw["length"],
                weak=raw["weak"],
                strong=bytes.fromhex(raw["strong"]),
            )
            sig.blocks.append(block)
            sig._index_block(block)
        return sig


def build_signature(
    source: Union[bytes, BinaryIO],
    block_size: int = DEFAULT_BLOCK_SIZE,
    strong_digest_size: int = DEFAULT_STRONG_DIGEST_SIZE,
) -> FileSignature:
    """Split ``source`` into fixed-size blocks (the final block may be shorter)
    and compute a (weak, strong) checksum pair for each.

    ``source`` may be raw bytes or a binary file-like object opened for
    reading; a file-like object is read in ``block_size`` chunks so signature
    generation doesn't require holding the whole basis file in memory.
    """
    if block_size <= 0:
        raise ValueError("block_size must be positive")

    sig = FileSignature(block_size=block_size, strong_digest_size=strong_digest_size)

    if isinstance(source, (bytes, bytearray)):
        chunks = (source[i : i + block_size] for i in range(0, len(source), block_size))
    else:
        def _reader():
            while True:
                chunk = source.read(block_size)
                if not chunk:
                    return
                yield chunk

        chunks = _reader()

    offset = 0
    for index, chunk in enumerate(chunks):
        if not chunk:
            continue
        block = BlockSignature(
            index=index,
            offset=offset,
            length=len(chunk),
            weak=weak_checksum(chunk),
            strong=strong_checksum(chunk, strong_digest_size),
        )
        sig.blocks.append(block)
        sig._index_block(block)
        offset += len(chunk)

    return sig
