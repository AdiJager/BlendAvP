"""Standalone round-trip sanity check for the REBINFF2 writer.

Runs WITHOUT Blender — it imports the `rif` package directly instead of the
addon package, whose __init__ pulls in bpy.

Usage (from the avp_rif_exporter directory):
    python tests/roundtrip.py

Builds a synthetic mesh-like object with the small subset of attributes the
builder reads. Every quad is wound outward, so the exported SHPPNORM data is
meaningful (the tables below were verified with scratch/derive_quads.py:
cross(v1-v0, v2-v0) matches the face normal).
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rif.builder import build_rif_bytes  # noqa: E402


# face normal -> outward-wound quad over the 8 corners built in _fake_mesh()
_QUADS = {
    (-1.0, 0.0, 0.0): (0, 1, 2, 3),
    (1.0, 0.0, 0.0): (4, 6, 5, 7),
    (0.0, -1.0, 0.0): (0, 4, 1, 5),
    (0.0, 1.0, 0.0): (2, 3, 6, 7),
    (0.0, 0.0, -1.0): (0, 2, 4, 6),
    (0.0, 0.0, 1.0): (1, 5, 3, 7),
}


def _fake_mesh():
    """Return a duck-typed mesh representing an axis-aligned cube."""
    verts = []
    for i, (x, y, z) in enumerate(
            (x, y, z)
            for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)):
        acc = [0.0, 0.0, 0.0]
        for normal, quad in _QUADS.items():
            if i in quad:
                for k in range(3):
                    acc[k] += normal[k]
        mag = sum(c * c for c in acc) ** 0.5
        verts.append(SimpleNamespace(
            co=SimpleNamespace(x=x, y=y, z=z),
            normal=SimpleNamespace(x=acc[0] / mag, y=acc[1] / mag,
                                   z=acc[2] / mag),
        ))

    polys = [
        SimpleNamespace(
            vertices=quad,
            loop_indices=list(range(i * 4, i * 4 + 4)),
            material_index=0,
            normal=SimpleNamespace(x=normal[0], y=normal[1], z=normal[2]),
        )
        for i, (normal, quad) in enumerate(_QUADS.items())
    ]

    uv_data = [SimpleNamespace(uv=(li * 0.1 % 1.0, li * 0.2 % 1.0))
               for li in range(24)]
    return SimpleNamespace(
        name="TestCube",
        vertices=verts,
        polygons=polys,
        materials=[],
        uv_layers=SimpleNamespace(active=SimpleNamespace(data=uv_data)),
    )


def main():
    """Build a cube RIF and print top-level chunk structure."""
    data = build_rif_bytes(_fake_mesh(), object_name="TestCube")
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
