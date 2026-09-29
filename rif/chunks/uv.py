"""SHPUVCRD — Shape_UV_Coord_Chunk."""
import struct
from ..chunk_writer import Chunk


class ShapeUVCoordChunk(Chunk):
    """UV coordinates in texture pixel space (not normalised)."""

    identifier = "SHPUVCRD"

    def __init__(self, uv_lists):
        # uv_lists: one list per polygon, each a list of (u, v) floats.
        self.uv_lists = list(uv_lists)

    def payload(self) -> bytes:
        """Serialise count, then per-poly vertex count and UV pairs."""
        buf = bytearray()
        buf += struct.pack("<i", len(self.uv_lists))
        for uvs in self.uv_lists:
            buf += struct.pack("<i", len(uvs))
            for u, v in uvs:
                buf += struct.pack("<2f", u, v)
        return bytes(buf)