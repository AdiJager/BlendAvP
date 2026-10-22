"""`SHPFRAGS` with explicit sub-shapes — an object that breaks into pieces.

The parent keeps its own geometry: it is what the player sees and shoots, and it
is replaced by the pieces only when it is destroyed. All 2289 fragmenting shapes
in the shipped levels carry real vertices; the commonest is three corners and one
polygon, a pane or a lamp cover.

Layout, from `chnkload.cpp:851-953` and `SHPCHUNK.HPP:970-1057`:

    SHPFRAGS
    └── SUBSHAPE                 one piece each
        ├── SUBSHPHD    44 B     file_id_num, num_verts, num_polys, bbox
        ├── SHPFNAME    20 B     piece name, padded
        ├── FRAGDATA    16 B     num_fragments + three spare words
        └── FRAGLOCN    28 B     offset from the parent + four spare words

`FRAGDATA` and `FRAGLOCN` are optional in the file, and the defaults matter:

* **No `FRAGDATA` means one copy.** The loader starts at `num_frags = 1` and only
  overwrites it when the chunk is there, which is why 11162 of retail's
  sub-shapes read as one.
* **A zero offset means random**, not "centred". `Bh_debri.c:731` scatters the
  piece anywhere inside the parent's bounding box when the offset is zero. That
  is the one thing the explicit-piece version can do that the type version
  cannot: `FRGTYPSC` has no offset field at all (`chnkload.cpp:126-128`), so a
  type-based break is always random.

Writing both chunks is therefore not optional in practice. The offset is the
scatter direction -- `Bh_debri.c:800` uses the piece's own position as its
impulse -- so a piece placed to the left flies left.
"""
from __future__ import annotations

import struct

from ..chunk_writer import Chunk

FRAGMENT_HEADER_BYTES = 44
FRAGMENT_NAME_BYTES = 20
FRAGDATA_BYTES = 16
FRAGLOCN_BYTES = 28


def _padded(text, size):
    """Return `text` as bytes padded with zeros to `size`."""
    raw = text.encode("latin-1", "replace")
    if len(raw) >= size:
        return raw[:size]
    return raw.ljust(size, b"\x00")


class FragmentHeaderChunk(Chunk):
    """SUBSHPHD — the piece's shape header, 44 bytes.

    Same layout the morph sub-shapes use; the field at +4 is the shape number
    the fragment table points at, which is why it must be non-zero.
    """

    identifier = "SUBSHPHD"

    def __init__(self, file_id_num=1, num_verts=0, num_polys=0, radius=0.0,
                 maximum=(0, 0, 0), minimum=(0, 0, 0), index=0):
        self.file_id_num = int(file_id_num)
        self.num_verts = int(num_verts)
        self.num_polys = int(num_polys)
        self.radius = float(radius)
        self.maximum = tuple(int(v) for v in maximum)
        self.minimum = tuple(int(v) for v in minimum)
        self.index = int(index)

    def payload(self) -> bytes:
        """Serialise the 44-byte header."""
        buf = bytearray()
        buf += struct.pack("<2i", self.index, self.file_id_num)
        buf += struct.pack("<2i", self.num_verts, self.num_polys)
        buf += struct.pack("<f", self.radius)
        buf += struct.pack("<3i", *self.maximum)
        buf += struct.pack("<3i", *self.minimum)
        assert len(buf) == FRAGMENT_HEADER_BYTES, len(buf)
        return bytes(buf)


class FragmentNameChunk(Chunk):
    """SHPFNAME — the piece's name, 20 bytes."""

    identifier = "SHPFNAME"

    def __init__(self, name=""):
        self.name = name

    def payload(self) -> bytes:
        """Serialise the name, padded to a fixed 20 bytes."""
        return _padded(self.name, FRAGMENT_NAME_BYTES)


class FragmentDataChunk(Chunk):
    """FRAGDATA — how many copies of this piece, 16 bytes."""

    identifier = "FRAGDATA"

    def __init__(self, num_fragments=1, pad1=0, pad2=0, pad3=0):
        self.num_fragments = int(num_fragments)
        self.pad1 = int(pad1)
        self.pad2 = int(pad2)
        self.pad3 = int(pad3)

    def payload(self) -> bytes:
        """Serialise the count and three spare words."""
        buf = struct.pack("<4i", self.num_fragments, self.pad1, self.pad2,
                          self.pad3)
        assert len(buf) == FRAGDATA_BYTES, len(buf)
        return buf


class FragmentLocationChunk(Chunk):
    """FRAGLOCN — where the piece sits relative to the parent, 28 bytes."""

    identifier = "FRAGLOCN"

    def __init__(self, location=(0, 0, 0), padding=(0, 0, 0, 0)):
        self.location = tuple(int(v) for v in location)
        self.padding = tuple(int(v) for v in padding)

    def payload(self) -> bytes:
        """Serialise the offset and four spare words."""
        buf = bytearray()
        buf += struct.pack("<3i", *self.location)
        buf += struct.pack("<4i", *self.padding)
        assert len(buf) == FRAGLOCN_BYTES, len(buf)
        return bytes(buf)


class SubShapeChunk(Chunk):
    """SUBSHAPE — one piece, as a container of the four chunks above."""

    identifier = "SUBSHAPE"

    def __init__(self, children=None):
        self.children = list(children or [])

    def payload(self) -> bytes:
        """Concatenate the children."""
        return b"".join(child.serialize() for child in self.children)


class ShapeFragmentsChunk(Chunk):
    """SHPFRAGS — the piece table hanging off the parent's REBSHAPE."""

    identifier = "SHPFRAGS"

    def __init__(self, pieces=None):
        self.pieces = list(pieces or [])

    def payload(self) -> bytes:
        """Concatenate the pieces."""
        return b"".join(piece.serialize() for piece in self.pieces)


def build_piece(name, file_id_num, num_verts, num_polys, radius, maximum,
                minimum, location, copies=1):
    """Build one SUBSHAPE for a fragment piece."""
    return SubShapeChunk([
        FragmentHeaderChunk(file_id_num=file_id_num, num_verts=num_verts,
                            num_polys=num_polys, radius=radius,
                            maximum=maximum, minimum=minimum),
        FragmentNameChunk(name),
        FragmentDataChunk(copies),
        FragmentLocationChunk(location),
    ])


def build_fragments_chunk(pieces):
    """Build the SHPFRAGS chunk from a list of SUBSHAPE chunks."""
    return ShapeFragmentsChunk(pieces)
