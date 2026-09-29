"""SHPRAWVT — Shape_Vertex_Chunk."""
import struct
from ..chunk_writer import Chunk


class ShapeVertexChunk(Chunk):
    """Vertex positions in fixed-point int32 (65536 == 1.0)."""

    identifier = "SHPRAWVT"

    def __init__(self, verts):
        # Each entry: (x, y, z) int32 triple.
        self.verts = list(verts)

    def payload(self) -> bytes:
        """Serialise 12 bytes per vertex."""
        buf = bytearray()
        for x, y, z in self.verts:
            buf += struct.pack("<3i", x, y, z)
        return bytes(buf)