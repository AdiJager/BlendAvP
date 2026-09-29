"""Convert a Blender mesh into REBINFF2 bytes."""
from math import sqrt

from .types import (
    DEFAULT_LOCK_USER, DEFAULT_NOTES,
    meters_to_rif, uv_to_rif_pixels,
)
from .chunks.root import GodFatherChunk, RIFVersionChunk
from .chunks.shape import ShapeChunk
from .chunks.header import ShapeHeaderChunk
from .chunks.vertex import ShapeVertexChunk
from .chunks.vnormal import ShapeVertexNormalChunk
from .chunks.pnormal import ShapePolygonNormalChunk
from .chunks.polygon import ShapePolygonChunk
from .chunks.uv import ShapeUVCoordChunk
from .chunks.centre import ShapeCentreChunk
from .chunks.object import (
    ObjectChunk, ObjectHeaderChunk,
    ObjectInterfaceDataChunk, ObjectNotesChunk,
)


SHAPE_FLAGS = 0
SHAPE_VERSION_NO = 1
POLY_FLAGS = 0x00C00000
POLY_ENGINE_TYPE = 7
OBJ_FLAGS = 0x00000100
OBJ_VERSION_NO = 1

# poly_colour bit layout, confirmed against community importer config.py.
POLY_BITMAP_MASK = 0x00000FFF
POLY_UV_HI_MASK  = 0x0000F000


def build_rif_bytes(mesh, object_name: str = None) -> bytes:
    """Serialise a Blender mesh to a REBINFF2 byte buffer (metres → mm)."""
    if object_name is None:
        object_name = mesh.name or "Mesh"

    verts_int, vert_normals, polys, poly_normals, uv_lists = \
        _extract_geometry(mesh)

    bounds = _compute_bounds(verts_int)
    centre = _compute_centre(bounds)
    radius = _compute_radius(verts_int, centre)
    uv_pixels = _uv_to_pixels(uv_lists)

    file_id_num = 1

    shape_children = [
        ShapeHeaderChunk(
            flags=SHAPE_FLAGS,
            lock_user=DEFAULT_LOCK_USER,
            file_id_num=file_id_num,
            num_verts=len(verts_int),
            num_polys=len(polys),
            radius=radius,
            max_xyz=bounds["max"],
            min_xyz=bounds["min"],
            version_no=SHAPE_VERSION_NO,
            num_as_obj=1,
            object_names=object_name,
        ),
        ShapeCentreChunk(centre, radius),
        ShapeVertexChunk(verts_int),
        ShapeVertexNormalChunk(vert_normals),
        ShapePolygonNormalChunk(poly_normals),
        ShapePolygonChunk(polys),
        ShapeUVCoordChunk(uv_pixels),
    ]
    shape = ShapeChunk(shape_children)

    obj_header = ObjectHeaderChunk(
        flags=OBJ_FLAGS,
        lock_user=DEFAULT_LOCK_USER,
        location=(0, 0, 0),
        orientation=(-0.0, -0.0, -0.0, 0.0),
        index_num=0,
        version_no=OBJ_VERSION_NO,
        shape_id_no=file_id_num,
        o_name=object_name,
    )
    obj = ObjectChunk([
        obj_header,
        ObjectInterfaceDataChunk([ObjectNotesChunk(DEFAULT_NOTES)]),
    ])

    root = GodFatherChunk([
        RIFVersionChunk(0),
        shape,
        obj,
    ])
    return root.serialize()


def _extract_geometry(mesh):
    """Return (verts_int, vert_normals, polys, poly_normals, uv_lists)."""
    verts_int = []
    vert_normals = []
    for vert in mesh.vertices:
        co = vert.co
        verts_int.append((
            meters_to_rif(co.x),
            meters_to_rif(co.y),
            meters_to_rif(co.z),
        ))
        n = vert.normal
        vert_normals.append((n.x, n.y, n.z))

    uv_layer = mesh.uv_layers.active
    polys = []
    poly_normals = []
    uv_lists = []

    poly_index = 0
    for poly in mesh.polygons:
        indices = list(poly.vertices)
        n = poly.normal
        face_normal = (n.x, n.y, n.z)

        if uv_layer is not None:
            loop_uvs = [tuple(uv_layer.data[li].uv)
                        for li in poly.loop_indices]
        else:
            loop_uvs = [(0.0, 0.0)] * len(indices)

        if len(indices) > 4:
            for i in range(1, len(indices) - 1):
                tri = [indices[0], indices[i], indices[i + 1]]
                tri_uvs = [loop_uvs[0], loop_uvs[i], loop_uvs[i + 1]]
                polys.append(_make_poly_record(tri, poly_index))
                poly_normals.append(face_normal)
                uv_lists.append(tri_uvs)
                poly_index += 1
        else:
            polys.append(_make_poly_record(indices, poly_index))
            poly_normals.append(face_normal)
            uv_lists.append(loop_uvs)
            poly_index += 1

    return verts_int, vert_normals, polys, poly_normals, uv_lists


def _make_poly_record(vert_indices, poly_index, bitmap_idx=0):
    """Build a polygon record dict, padding vertex list with -1.

    poly_colour layout (uint32, confirmed against community config.py):
        bits  0..11  bitmap_idx  (POLY_BITMAP_MASK = 0x00000FFF)
        bits 12..15  uv_idx hi nibble (POLY_UV_HI_MASK; only used if >0)
        bits 16..31  uv_idx low 16 bits
    We write uv_idx == poly_index, matching desk1/sentry: one SHPUVCRD
    record per polygon, indexed by polygon order.
    """
    count = len(vert_indices)
    if count < 3 or count > 4:
        raise ValueError(f"Polygon must have 3 or 4 verts, got {count}")
    padded = list(vert_indices) + [-1] * (5 - count)
    colour = ((poly_index & 0xFFFF) << 16) | (bitmap_idx & POLY_BITMAP_MASK)
    return {
        "engine_type": POLY_ENGINE_TYPE,
        "normal_index": poly_index,
        "flags": POLY_FLAGS,
        "colour": colour,
        "vert_ind": tuple(padded[:5]),
    }


def _compute_bounds(verts_int):
    """Return dict with max=(x,y,z) and min=(x,y,z) int32 tuples."""
    if not verts_int:
        return {"max": (0, 0, 0), "min": (0, 0, 0)}
    xs = [v[0] for v in verts_int]
    ys = [v[1] for v in verts_int]
    zs = [v[2] for v in verts_int]
    return {
        "max": (max(xs), max(ys), max(zs)),
        "min": (min(xs), min(ys), min(zs)),
    }


def _compute_centre(bounds):
    """Return integer midpoint of the axis-aligned bounding box."""
    return tuple(
        (bounds["max"][i] + bounds["min"][i]) // 2
        for i in range(3)
    )


def _compute_radius(verts_int, centre):
    """Return float distance from centre to the farthest vertex."""
    if not verts_int:
        return 0.0
    cx, cy, cz = centre
    return max(
        sqrt((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2)
        for x, y, z in verts_int
    )


def _uv_to_pixels(uv_lists):
    """Convert normalised Blender UVs to RIF texture pixel space."""
    return [
        [uv_to_rif_pixels(u, v) for (u, v) in uvs]
        for uvs in uv_lists
    ]