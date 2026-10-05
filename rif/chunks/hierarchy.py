"""OBJCHIER / OBJHIERD / OBHIERNM — object hierarchy chunks.

Layouts verified against hierchnk.cpp:

    OBJHIERD payload:
        u32  num_extra_data
        i32  extra_data[num_extra_data]
        cstr object_name     (NUL-terminated ASCII, padded to 4-byte boundary)

    OBHIERNM payload:
        cstr hierarchy_name

    OBJCHIER is a container that holds OBJCHIER (recursive) / OBJHIERD /
    OBHIERNM / HIERBBOX / OBANSEQS / OBANALLS children. We only need the
    first two to reproduce the sentry-family skeleton.

NOTE on identifiers (see S6 F5 bug fix):
    All chunk IDs in the AvP RIF format are exactly 8 bytes wide. The
    hierarchy-name chunk is spelled OBHIERNM (no J): O-B-H-I-E-R-N-M.
    Verified against sentry.txt @0xB1C and hive_c.rif. The 9-char
    spelling "OBJHIERNM" that appeared in earlier revisions of this
    file cannot exist in the format; emitting it shifted the chunk tree
    by one byte and caused "Error loading custom.rif" in every export
    that contained a hierarchy. Fixed in S6 F5.
"""
import struct

from ..chunk_writer import Chunk, ChunkWithChildren
from ..types import pack_string


class ObjectHierarchyChunk(ChunkWithChildren):
    """OBJCHIER — container; children OBJCHIER (recursive) and OBJHIERD."""

    def __init__(self, children=None):
        super().__init__("OBJCHIER", children)


class ObjectHierarchyDataChunk(Chunk):
    """OBJHIERD — leaf referencing an RBOBJECT by object name."""

    identifier = "OBJHIERD"

    def __init__(self, object_name: str, extra_data=None):
        self.object_name = object_name
        self.extra_data = list(extra_data) if extra_data else []

    def payload(self) -> bytes:
        """Serialise num_extra_data, optional extras and NUL-terminated name."""
        buf = bytearray()
        buf += struct.pack("<I", len(self.extra_data))
        for v in self.extra_data:
            buf += struct.pack("<i", v)
        buf += pack_string(self.object_name)
        return bytes(buf)


class ObjectHierarchyNameChunk(Chunk):
    """OBHIERNM — hierarchy name tag (root of the tree, usually).

    Identifier is exactly 8 chars: O-B-H-I-E-R-N-M. Verified against
    sentry.txt @0xB1C. Do NOT spell it "OBJHIERNM" (9 chars) — that
    breaks the chunk tree and causes "Error loading" in game.
    """

    identifier = "OBHIERNM"

    def __init__(self, hierarchy_name: str):
        self.hierarchy_name = hierarchy_name

    def payload(self) -> bytes:
        """Serialise hierarchy name as padded ASCII string."""
        return pack_string(self.hierarchy_name)