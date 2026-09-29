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
    """Map normalised Blender UV to RIF pixel space with V-flip (Y-down).

    The community importer scales by 1/pixels (see UV_SCALES[0] = 1/128),
    so we must write u*pixels (NOT u*(pixels-1)) to make the round-trip
    lossless at u=1.0. V-flip is intentional for game compatibility: the
    importer does NOT flip, so its preview appears vertically mirrored.
    """
    return (u * pixels, (1.0 - v) * pixels)


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