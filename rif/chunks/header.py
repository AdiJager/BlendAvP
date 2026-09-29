"""SHPHEAD1 — Shape_Header_Chunk."""
import struct
from ..chunk_writer import Chunk
from ..types import pack_string


class ShapeHeaderChunk(Chunk):
    """Shape header: flags, bounds, vertex/poly counts, object names."""

    identifier = "SHPHEAD1"

    def __init__(self, *, flags, lock_user, file_id_num, num_verts,
                 num_polys, radius, max_xyz, min_xyz, version_no,
                 num_as_obj, object_names):
        self.flags = flags
        self.lock_user = lock_user
        self.file_id_num = file_id_num
        self.num_verts = num_verts
        self.num_polys = num_polys
        self.radius = radius
        self.max_xyz = max_xyz
        self.min_xyz = min_xyz
        self.version_no = version_no
        self.num_as_obj = num_as_obj
        self.object_names = object_names

    def payload(self) -> bytes:
        """Serialise the 76-byte fixed block plus padded object-name string."""
        buf = bytearray()
        buf += struct.pack("<i", self.flags)
        buf += self.lock_user.encode("ascii")[:16].ljust(16, b"\0")
        buf += struct.pack("<i", self.file_id_num)
        buf += struct.pack("<i", self.num_verts)
        buf += struct.pack("<i", self.num_polys)
        buf += struct.pack("<f", self.radius)
        # Bounds: max.x, min.x, max.y, min.y, max.z, min.z
        buf += struct.pack("<i", self.max_xyz[0])
        buf += struct.pack("<i", self.min_xyz[0])
        buf += struct.pack("<i", self.max_xyz[1])
        buf += struct.pack("<i", self.min_xyz[1])
        buf += struct.pack("<i", self.max_xyz[2])
        buf += struct.pack("<i", self.min_xyz[2])
        buf += struct.pack("<i", self.version_no)
        buf += struct.pack("<i", self.num_as_obj)
        buf += pack_string(self.object_names)
        return bytes(buf)