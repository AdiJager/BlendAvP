"""REBSHAPE — top-level shape container."""
from ..chunk_writer import ChunkWithChildren


class ShapeChunk(ChunkWithChildren):
    """REBSHAPE — container for a single static mesh."""

    def __init__(self, children=None):
        super().__init__("REBSHAPE", children)