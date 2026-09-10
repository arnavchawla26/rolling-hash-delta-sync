"""deltasync: an rsync-style binary delta synchronization library.

Public API:
    - rolling_checksum.RollingChecksum   : O(1)-updatable weak checksum
    - signature.build_signature          : signature of a "basis" (old) file
    - delta.compute_delta                : diff a new file against a signature
    - patch.apply_delta                  : reconstruct the new file from basis + delta
"""

from .rolling_checksum import RollingChecksum, weak_checksum
from .signature import BlockSignature, FileSignature, build_signature
from .delta import CopyOp, DataOp, compute_delta
from .patch import apply_delta

__version__ = "0.1.0"

__all__ = [
    "RollingChecksum",
    "weak_checksum",
    "BlockSignature",
    "FileSignature",
    "build_signature",
    "CopyOp",
    "DataOp",
    "compute_delta",
    "apply_delta",
]
