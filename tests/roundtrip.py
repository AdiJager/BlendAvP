"""Standalone round-trip sanity check for the REBINFF2 writer.

Usage:
    python -m rif_exporter.tests.roundtrip

Does not require Blender — builds a synthetic mesh-like object
with the small subset of attributes the builder uses.
"""
import sys
from types import SimpleNamespace

# Allow running as a script without installing the addon.
if __package__ is None:
    sys.path.insert(0, ".")
    from rif_exporter.rif.builder import build_rif_bytes
else:
    from ..rif.builder import build_rif_bytes


def _fake_mesh():
    """Return a duck-typed mesh representing an axis-aligned cube."""
    verts = [
        SimpleNamespace(co=SimpleNamespace(x=x, y=y, z=z))
        for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)
    ]
    # 6 quads over the 8 cube vertices (indices refer to verts above).
    quads = [
        (0, 1, 3, 2), (4, 6, 7, 5),
        (0, 4, 5, 1), (2, 3, 7, 6),
        (0, 2, 6, 4), (1, 5, 7, 3),
    ]
    polys = [
        SimpleNamespace(vertices=q, loop_indices=range(i * 4, i * 4 + 4))
        for i, q in enumerate(quads)
    ]
    return SimpleNamespace(
        name="TestCube",
        vertices=verts,
        polygons=polys,
        uv_layers=SimpleNamespace(active=None),
    )


def main():
    """Build a cube RIF and print top-level chunk structure."""
    data = build_rif_bytes(_fake_mesh(), scale=1.0, object_name="TestCube")
    print(f"total bytes: {len(data)}")
    _dump_top_level(data)


def _dump_top_level(data):
    """Print the identifier and size of every top-level chunk."""
    offset = 0
    while offset + 12 <= len(data):
        ident = data[offset:offset + 8].rstrip(b"\0").decode("ascii")
        size = int.from_bytes(data[offset + 8:offset + 12], "little")
        print(f"  @{offset:08x}  {ident:<8}  size={size}")
        offset += size


if __name__ == "__main__":
    main()