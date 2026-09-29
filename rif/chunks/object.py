"""RBOBJECT, OBJHEAD1, OBINTDT and OBJNOTES."""
import struct
from ..chunk_writer import Chunk, ChunkWithChildren
from ..types import pack_string


class ObjectChunk(ChunkWithChildren):
    """RBOBJECT — Object_Chunk."""

    def __init__(self, children=None):
        super().__init__("RBOBJECT", children)


class ObjectHeaderChunk(Chunk):
    """OBJHEAD1 — Object_Header_Chunk."""

    identifier = "OBJHEAD1"

    def __init__(self, *, flags, lock_user, location, orientation,
                 index_num, version_no, shape_id_no, o_name):
        self.flags = flags
        self.lock_user = lock_user
        self.location = location          # (x, y, z) int32
        self.orientation = orientation    # (x, y, z, w) float32
        self.index_num = index_num
        self.version_no = version_no
        self.shape_id_no = shape_id_no    # matches SHPHEAD1.file_id_num
        self.o_name = o_name

    def payload(self) -> bytes:
        """Serialise 60-byte fixed block plus padded object name."""
        buf = bytearray()
        buf += struct.pack("<i", self.flags)
        buf += self.lock_user.encode("ascii")[:16].ljust(16, b"\0")
        buf += struct.pack("<3i", *self.location)
        buf += struct.pack("<4f", *self.orientation)
        buf += struct.pack("<i", self.index_num)
        buf += struct.pack("<i", self.version_no)
        buf += struct.pack("<i", self.shape_id_no)
        buf += pack_string(self.o_name)
        return bytes(buf)


class ObjectInterfaceDataChunk(ChunkWithChildren):
    """OBINTDT — Object_Interface_Data_Chunk (holds OBJNOTES)."""

    def __init__(self, children=None):
        super().__init__("OBINTDT", children)


class ObjectNotesChunk(Chunk):
    """OBJNOTES — Object_Notes_Chunk."""

    identifier = "OBJNOTES"

    def __init__(self, notes: str):
        self.notes = notes

    def payload(self) -> bytes:
        """Serialise notes as padded ASCII string."""
        return pack_string(self.notes)