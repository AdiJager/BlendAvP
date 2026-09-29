"""File root chunks: REBINFF2 and RIFVERIN."""
import struct
from ..chunk_writer import Chunk, ChunkWithChildren


class GodFatherChunk(ChunkWithChildren):
    """REBINFF2 — top-level file container."""

    def __init__(self, children=None):
        super().__init__("REBINFF2", children)


class RIFVersionChunk(Chunk):
    """RIFVERIN — file format version (always 0 in uncompressed RIF)."""

    identifier = "RIFVERIN"

    def __init__(self, version: int = 0):
        self.version = version

    def payload(self) -> bytes:
        """Return 4-byte little-endian version int."""
        return struct.pack("<i", self.version)