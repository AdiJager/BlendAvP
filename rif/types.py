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
                     pixels: float = DEFAULT_UV_PIXELS):
    """Map normalised Blender UV to RIF pixel space.

    No V-flip. It used to flip, on the assumption that the game is Y-down and
    the importer is not -- but the importer this add-on ships reads V unchanged,
    so the two disagreed and every textured surface was mirrored in game while
    looking right in Blender. Writing V as it is keeps a round trip identical.

    Scaling is by pixels, not pixels - 1: the reader multiplies back by
    1/128, so u=1.0 has to come back as 128 to be lossless.
    """
    return (u * pixels, v * pixels)


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