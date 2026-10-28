"""Shared constants and packing helpers for RIF chunks."""
import struct


DEFAULT_LOCK_USER = "Player"
DEFAULT_NOTES = "Enter notes here"
FIXED_POINT_ONE = 65536

# Community importer maps RIF ints to metres with Scale=0.001 (mm → m).
# Calibrated against desk1.rif: bounds ±808 int ↔ ±0.808 m.
RIF_UNITS_PER_METER = 1000.0

# UV pixel space for desk1-family shapes (no SHPTEXFN yet; see S3).
# Community importer uses uv_scale = 1/128 by default (UV_SCALES[0]).
# RIF pixel origin is top-left (DirectX-style); Blender is bottom-left.
DEFAULT_UV_PIXELS = 128.0
UV_PIXEL_MAX = DEFAULT_UV_PIXELS


def meters_to_rif(meters: float) -> int:
    """Convert Blender metres to RIF integer world units (millimetres)."""
    return int(round(meters * RIF_UNITS_PER_METER))


def rif_to_meters(rif_int: int) -> float:
    """Convert RIF integer world units (millimetres) back to Blender metres."""
    return rif_int / RIF_UNITS_PER_METER


def uv_to_rif_pixels(u: float, v: float,
                     width: float = DEFAULT_UV_PIXELS,
                     height: float = None):
    """Map a normalised Blender UV to the engine's texture-pixel space.

    V is flipped. The engine counts rows from the top of the IFF --
    d3d_render.cpp:942-943 samples (V + 0.5) / height -- and the RIM loader does
    not reverse them (awTexLd.cpp:1352: AreRowsReversed returns false), so its
    v = 0 is the top row. Blender's v = 0 is the bottom row.

    The 0.5 puts the vertex on a texel centre: the loader truncates the stored
    float to an integer texel and the renderer samples centres, so without it a
    UV of exactly 1.0 would store the row one past the image and wrap. The
    reader adds the same 0.5 back, which is what makes the round trip exact.

    Tiling is passed through: UVs may be negative or exceed the bitmap, and the
    engine wraps.

    width and height are the texture's real size in pixels, not a constant: the
    game ships textures from 64x64 to 512x512, and writing 128 for one of those
    makes the engine sample a quarter of the image at twice the size. height
    defaults to width, which is what every shipped level texture is.
    """
    if height is None:
        height = width
    return (u * width - 0.5, (1.0 - v) * height - 0.5)


def blender_to_rif_axis(x: float, y: float, z: float):
    """Map Blender world coords to RIF world coords (inverse of importer).

    Community importer (shape_processor.py::xform) does:
        apply_axis(x, y, z) = (x, z, -y)
    after applying the object transform. To make our writer a lossless
    inverse, we apply the inverse rotation here:
        blender (x, y, z) → RIF (x, -z, y)
    The function is linear and has det = +1 (it is a rotation), so it is
    also valid for direction vectors such as vertex/face normals.
    """
    return (x, -z, y)


def rif_to_blender_axis(x: float, y: float, z: float):
    """Map RIF world coords to Blender (mirrors community importer)."""
    return (x, z, -y)


def float_to_fixed(value: float, scale: float = 1.0) -> int:
    """Legacy 16.16 fixed-point packer; kept for callers outside the builder."""
    return int(round(value * FIXED_POINT_ONE / scale))


def pack_string(text: str) -> bytes:
    """Pack ASCII string with null terminator, padded to 4-byte boundary."""
    raw = text.encode("ascii")
    span = (len(raw) + 4) & ~3
    return raw.ljust(span, b"\0")


def pack_ints(values) -> bytes:
    """Pack a sequence of int32 values as little-endian."""
    return struct.pack(f"<{len(values)}i", *values)


def pack_floats(values) -> bytes:
    """Pack a sequence of float32 values as little-endian."""
    return struct.pack(f"<{len(values)}f", *values)