"""SHPCENTR — Shape_Centre_Chunk."""
import struct
from ..chunk_writer import Chunk


class ShapeCentreChunk(Chunk):
    """Shape centre point (int32) and float radius."""

    identifier = "SHPCENTR"

    def __init__(self, centre, radius):
        self.centre = centre  # (x, y, z) int32
        self.radius = radius  # float32

    def payload(self) -> bytes:
        """Serialise 3 int32 + 1 float32 = 16 bytes."""
        return struct.pack("<3if", *self.centre, self.radius)