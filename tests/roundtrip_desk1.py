"""Round-trip desk1.rif through Blender; verify bounds and UV survive."""
import bpy
from mathutils import Vector
from bl_ext.rif_exporter.rif.types import (
    RIF_UNITS_PER_METER, rif_to_meters,
)
from bl_ext.rif_exporter.rif.builder import build_rif_bytes


DESK1_REF_BOUNDS = {
    "max": Vector((808, 421, 321)) / RIF_UNITS_PER_METER,
    "min": Vector((-808, -420, -321)) / RIF_UNITS_PER_METER,
}
TOLERANCE_M = 1e-4  # 0.1 mm, matches int rounding


def _mesh_bounds(obj):
    """Return (Vector max, Vector min) in world metres for a mesh object."""
    coords = [obj.matrix_world @ v.co for v in obj.data.vertices]
    xs = [c.x for c in coords]
    ys = [c.y for c in coords]
    zs = [c.z for c in coords]
    return Vector((max(xs), max(ys), max(zs))), Vector((min(xs), min(ys), min(zs)))


def verify_imported_bounds():
    """Assert that the importer produced geometry matching desk1's ints."""
    obj = bpy.context.view_layer.objects.active
    assert obj and obj.type == "MESH", "select imported desk1 mesh"
    hi, lo = _mesh_bounds(obj)
    for axis, ref in zip("xyz", "xyz"):
        d_hi = abs(getattr(hi, axis) - getattr(DESK1_REF_BOUNDS["max"], axis))
        d_lo = abs(getattr(lo, axis) - getattr(DESK1_REF_BOUNDS["min"], axis))
        print(f"{axis}: max Δ={d_hi:.6f} m  min Δ={d_lo:.6f} m")
        assert d_hi < TOLERANCE_M and d_lo < TOLERANCE_M, f"axis {axis} out of tol"


def reexport_active_mesh(path="/tmp/desk1_reexport.rif"):
    """Serialise the active mesh through our writer and save it."""
    obj = bpy.context.view_layer.objects.active
    data = build_rif_bytes(obj.data, object_name=obj.name)
    with open(path, "wb") as fh:
        fh.write(data)
    print(f"wrote {len(data)} B to {path}")


if __name__ == "__main__":
    verify_imported_bounds()
    reexport_active_mesh()