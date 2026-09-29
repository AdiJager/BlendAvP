"""Base chunk classes for REBINFF2 serialisation."""
import struct


class Chunk:
    """Leaf chunk: 12-byte header (8 id + 4 size) plus payload."""

    identifier = ""

    def payload(self) -> bytes:
        """Return the payload bytes (header excluded)."""
        raise NotImplementedError

    def serialize(self) -> bytes:
        """Serialise the full chunk including its 12-byte header."""
        payload = self.payload()
        total = 12 + len(payload)
        ident = self.identifier.encode("ascii")
        if len(ident) > 8:
            raise ValueError(f"Identifier too long: {self.identifier!r}")
        ident = ident.ljust(8, b"\0")
        return ident + struct.pack("<I", total) + payload

    def size(self) -> int:
        """Total size in bytes (header + payload)."""
        return 12 + len(self.payload())


class ChunkWithChildren(Chunk):
    """Container chunk whose payload is its children back-to-back."""

    def __init__(self, identifier: str, children=None):
        self.identifier = identifier
        self.children = list(children) if children else []

    def payload(self) -> bytes:
        """Concatenate serialised children."""
        return b"".join(child.serialize() for child in self.children)