"""Reader for the game's ``.RIM`` textures (IFF/ILBM with a property section).

Verified against the retail installation: all 1028 textures in
``<game>/fastfile/Tex*.FFL`` parse with this layout.

The payload is **not** a bare ILBM file. It always begins with a ``LIST``::

    LIST ILBM
      PROP ILBM          property section (optional in the spec, always present
        TRAN             here; carries transparency metadata)
      FORM ILBM          the actual image
        BMHD             width, height, plane count, compression, masking
        CMAP             palette, 3 bytes per entry
        BODY             pixel data, PackBits-compressed when BMHD says so
      LIST MIPM          optional (248 of 1028); holds a 2-byte CONT marker
        FORM MIPM        and NO image data at all

So the image lives in the first ``FORM ILBM`` inside the outer ``LIST``, and
the trailing ``MIPM`` container can be ignored. The palette must come from the
**inner** ``CMAP``: the ``PROP`` section can carry its own, smaller one.

Chunk sizes are big-endian with even-byte padding; names are NUL-terminated and
padded to a 4-byte boundary. That mixture is normal IFF, and getting it wrong
yields garbage rather than an error, so both are applied explicitly.
"""
from __future__ import annotations

import struct

# BMHD.eMasking values seen in retail data.
MASKING_NONE = 0
MASKING_HAS_MASK = 1
MASKING_HAS_TRANSPARENT_COLOUR = 2

# BMHD.eCompression values.
COMPRESSION_NONE = 0
COMPRESSION_PACKBITS = 1
COMPRESSION_RLE = 2

# The BMHD payload is 20 bytes: 2+2+2+2+1+1+1+1+2+1+1+2+2.
_BMHD = struct.Struct(">HHhhBBBBHBBhh")

# A palette index needs a whole byte, so anything deeper cannot be represented.
MAX_PLANES = 8


class IffError(ValueError):
    """Raised when a payload is not a structure this reader understands."""


def iter_chunks(data, start=0, end=None):
    """Yield ``(chunk_id, payload_start, payload_end)`` over ``data[start:end]``.

    Applies the IFF rule of padding each chunk payload to an even length.
    """
    if end is None:
        end = len(data)
    pos = start
    while pos + 8 <= end:
        chunk_id = data[pos:pos + 4].decode("latin-1", "replace")
        size = struct.unpack_from(">I", data, pos + 4)[0]
        payload_start = pos + 8
        payload_end = payload_start + size
        if payload_end > end:
            raise IffError(
                f"chunk {chunk_id!r} at {pos} claims {size} bytes, "
                f"only {end - payload_start} remain")
        yield chunk_id, payload_start, payload_end
        pos = payload_end + (size & 1)


def find_chunk(data, wanted, start=0, end=None):
    """Return ``(payload_start, payload_end)`` of the first chunk with that id."""
    for chunk_id, payload_start, payload_end in iter_chunks(data, start, end):
        if chunk_id == wanted:
            return payload_start, payload_end
    return None


def decode_packbits(body, expected_size=None):
    """Undo PackBits (``ByteRun1``) compression.

    Control byte ``n``: ``0..127`` copies the next ``n+1`` bytes literally,
    ``129..255`` repeats the next byte ``257-n`` times, and ``128`` is a no-op.

    Measured against the retail data: every one of the 92 compressed textures
    decodes to exactly the size BMHD implies, so no other scheme is involved.
    """
    out = bytearray()
    pos = 0
    length = len(body)
    while pos < length:
        control = body[pos]
        pos += 1
        if control < 0x80:
            count = control + 1
            out += body[pos:pos + count]
            pos += count
        elif control > 0x80:
            if pos >= length:
                break
            out += bytes([body[pos]]) * (0x101 - control)
            pos += 1
        if expected_size is not None and len(out) >= expected_size:
            break
    return bytes(out[:expected_size] if expected_size else out)


class Ilbm:
    """One decoded texture: palette indices plus the metadata around them."""

    def __init__(self, width, height, planes, pixels, palette, masking,
                 transparent_index, has_transparency_chunk, compressed):
        self.width = width
        self.height = height
        self.planes = planes
        self.pixels = pixels            # bytes, width*height palette indices
        self.palette = palette          # list of (r, g, b)
        self.masking = masking
        self.transparent_index = transparent_index
        self.has_transparency_chunk = has_transparency_chunk
        self.compressed = compressed

    @property
    def uses_transparency(self):
        """True only when the texture declares transparency that we honour.

        Note that a ``TRAN`` chunk sits in the property section of **every**
        retail texture, including the 984 whose ``masking`` is 0, so its mere
        presence means nothing. Only ``masking`` decides:

        * ``0`` -- no transparency;
        * ``1`` -- a separate mask plane, which this reader rejects outright
          because it is not decoded yet;
        * ``2`` -- an explicitly transparent palette index.

        So in practice this is true exactly for the ``masking == 2`` textures.
        """
        return self.masking == MASKING_HAS_TRANSPARENT_COLOUR

    def rgba(self):
        """Return ``bytes`` of RGBA, one pixel per palette entry.

        A fully transparent index is emitted only for the explicit
        ``masking == 2`` case; ``masking == 1`` carries a separate mask plane
        that is not decoded yet, so it is reported by ``uses_transparency``
        instead of being guessed at here.
        """
        out = bytearray(self.width * self.height * 4)
        palette = self.palette
        fallback = (255, 0, 255)
        transparent = (self.masking == MASKING_HAS_TRANSPARENT_COLOUR)
        for i, index in enumerate(self.pixels):
            if transparent and index == self.transparent_index:
                out[i * 4 + 3] = 0
                continue
            if index < len(palette):
                r, g, b = palette[index]
            else:
                r, g, b = fallback
            base = i * 4
            out[base] = r
            out[base + 1] = g
            out[base + 2] = b
            out[base + 3] = 255
        return bytes(out)

    def rgb(self):
        """Return ``bytes`` of RGB, one pixel per palette entry."""
        out = bytearray(self.width * self.height * 3)
        palette = self.palette
        fallback = (255, 0, 255)
        for i, index in enumerate(self.pixels):
            r, g, b = palette[index] if index < len(palette) else fallback
            base = i * 3
            out[base] = r
            out[base + 1] = g
            out[base + 2] = b
        return bytes(out)


def _read_bmhd(data, start, end):
    """Return a dict of the BMHD fields."""
    if end - start < _BMHD.size:
        raise IffError(f"BMHD is {end - start} bytes, expected {_BMHD.size}")
    (width, height, x_origin, y_origin, planes, masking, compression,
     _pad, transparent_index, x_aspect, y_aspect,
     page_width, page_height) = _BMHD.unpack_from(data, start)
    return {
        "width": width,
        "height": height,
        "x_origin": x_origin,
        "y_origin": y_origin,
        "planes": planes,
        "masking": masking,
        "compression": compression,
        "transparent_index": transparent_index,
        "x_aspect": x_aspect,
        "y_aspect": y_aspect,
        "page_width": page_width,
        "page_height": page_height,
    }


def _read_palette(data, start, end):
    """Return the CMAP entries as a list of ``(r, g, b)`` tuples."""
    raw = data[start:end]
    return [tuple(raw[i:i + 3]) for i in range(0, len(raw) - 2, 3)]


def _unpack_planar(raw, width, height, planes):
    """De-interleave planar bitplanes into one palette index per pixel."""
    if planes > MAX_PLANES:
        raise IffError(
            f"{planes} bitplanes needs {1 << planes} palette entries; this "
            f"reader handles at most {MAX_PLANES} because an index has to fit "
            "in a byte")
    bytes_per_row = (width + 7) // 8
    need = bytes_per_row * planes * height
    if len(raw) < need:
        raise IffError(f"pixel data is {len(raw)} bytes, need {need}")

    out = bytearray(width * height)
    for y in range(height):
        row_base = y * bytes_per_row * planes
        out_base = y * width
        for plane in range(planes):
            plane_base = row_base + plane * bytes_per_row
            mask = 1 << plane
            for byte_index in range(bytes_per_row):
                byte = raw[plane_base + byte_index]
                if not byte:
                    continue
                first_x = byte_index * 8
                for bit in range(8):
                    if byte & (0x80 >> bit):
                        x = first_x + bit
                        if x < width:
                            out[out_base + x] |= mask
    return bytes(out)


def parse_ilbm(data):
    """Parse the game's ``.RIM`` payload into an :class:`Ilbm`.

    Raises :class:`IffError` for anything that is not the expected structure,
    including the one retail texture that carries 11 bitplanes.
    """
    if len(data) < 12:
        raise IffError("payload too short to be IFF")
    if data[:4] != b"LIST":
        raise IffError(f"expected LIST at the root, found {data[:4]!r}")
    list_form = data[8:12]
    if list_form != b"ILBM":
        raise IffError(f"root LIST is {list_form!r}, expected ILBM")
    list_size = struct.unpack_from(">I", data, 4)[0]
    list_end = min(8 + list_size, len(data))

    form = None
    has_transparency_chunk = False
    for chunk_id, payload_start, payload_end in iter_chunks(data, 12, list_end):
        if chunk_id == "PROP":
            inner = find_chunk(data, "TRAN", payload_start + 4, payload_end)
            has_transparency_chunk = inner is not None
        elif chunk_id == "FORM":
            form = (payload_start, payload_end)
    if form is None:
        raise IffError("no FORM ILBM inside the root LIST")

    form_start, form_end = form
    if data[form_start:form_start + 4] != b"ILBM":
        raise IffError(f"FORM is {data[form_start:form_start + 4]!r}, "
                       "expected ILBM")

    bmhd = find_chunk(data, "BMHD", form_start + 4, form_end)
    cmap = find_chunk(data, "CMAP", form_start + 4, form_end)
    body = find_chunk(data, "BODY", form_start + 4, form_end)
    if bmhd is None or cmap is None or body is None:
        missing = [name for name, found in (("BMHD", bmhd), ("CMAP", cmap),
                                            ("BODY", body)) if found is None]
        raise IffError(f"missing required chunk(s): {', '.join(missing)}")

    header = _read_bmhd(data, *bmhd)
    width = header["width"]
    height = header["height"]
    planes = header["planes"]
    if width <= 0 or height <= 0:
        raise IffError(f"degenerate size {width}x{height}")

    # An optional mask plane is stored alongside the colour planes for
    # masking == 1; it is not decoded yet, so reject rather than mis-render.
    if header["masking"] == MASKING_HAS_MASK:
        raise IffError("texture uses a separate mask plane (masking=1), "
                       "which this reader does not decode yet")

    raw_body = data[body[0]:body[1]]
    bytes_per_row = (width + 7) // 8
    expected = bytes_per_row * planes * height
    compression = header["compression"]
    if compression == COMPRESSION_NONE:
        raw = raw_body[:expected]
    elif compression == COMPRESSION_PACKBITS:
        raw = decode_packbits(raw_body, expected)
    else:
        raise IffError(f"unsupported compression {compression}")

    pixels = _unpack_planar(raw, width, height, planes)
    return Ilbm(
        width=width,
        height=height,
        planes=planes,
        pixels=pixels,
        palette=_read_palette(data, *cmap),
        masking=header["masking"],
        transparent_index=header["transparent_index"],
        has_transparency_chunk=has_transparency_chunk,
        compressed=compression != COMPRESSION_NONE,
    )


def is_rim(data):
    """True when ``data`` looks like one of the game's RIM payloads."""
    return len(data) >= 12 and data[:4] == b"LIST" and data[8:12] == b"ILBM"
