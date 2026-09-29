"""SHPPNORM — Shape_Polygon_Normal_Chunk."""
import struct
from ..chunk_writer import Chunk


class ShapePolygonNormalChunk(Chunk):
    """Per-polygon normals as float32 triples."""

    identifier = "SHPPNORM"

    def __init__(self, normals):
        # normals: list of (x, y, z) floats, one per polygon.
        self.normals = list(normals)

    def payload(self) -> bytes:
        """Serialise 12 bytes per polygon normal."""
        buf = bytearray()
        for x, y, z in self.normals:
            buf += struct.pack("<3f", x, y, z)
        return bytes(buf)