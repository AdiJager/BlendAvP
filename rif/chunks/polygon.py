"""SHPPOLYS — Shape_Polygon_Chunk."""
import struct
from ..chunk_writer import Chunk


POLY_SIZE = 36
TERMINATOR = -1


class ShapePolygonChunk(Chunk):
    """Polygon records: 36 bytes each (engine type, indices, colour)."""

    identifier = "SHPPOLYS"

    def __init__(self, polys):
        # Each poly: dict with keys engine_type, normal_index, flags,
        # colour, vert_ind (5-int tuple, -1 terminated).
        self.polys = list(polys)

    def payload(self) -> bytes:
        """Serialise all polygon records back-to-back."""
        buf = bytearray()
        for poly in self.polys:
            buf += struct.pack("<i", poly["engine_type"])
            buf += struct.pack("<i", poly["normal_index"])
            buf += struct.pack("<i", poly["flags"])
            buf += struct.pack("<I", poly["colour"])
            buf += struct.pack("<5i", *poly["vert_ind"])
        return bytes(buf)