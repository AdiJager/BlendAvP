"""SHPVNORM — Shape_Vertex_Normal_Chunk."""
import struct
from ..chunk_writer import Chunk


class ShapeVertexNormalChunk(Chunk):
    """Per-vertex normals as float32 triples."""

    identifier = "SHPVNORM"

    def __init__(self, normals):
        # normals: list of (x, y, z) floats, one per vertex.
        self.normals = list(normals)

    def payload(self) -> bytes:
        """Serialise 12 bytes per vertex normal."""
        buf = bytearray()
        for x, y, z in self.normals:
            buf += struct.pack("<3f", x, y, z)
        return bytes(buf)