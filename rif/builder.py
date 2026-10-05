"""Convert a Blender mesh into REBINFF2 bytes.

Supports four modes:
    build_rif_bytes(mesh)                          — single mesh
    build_rif_multi([obj, ...])                    — multi REBSHAPE + RBOBJECT
    build_rif_multi([obj, ...], hierarchy={...})   — + OBJCHIER
    build_rif_level([obj, ...], level_name, ...)   — + REBENVDT + MODULEDT

Axis convention: Blender's world (X right, Y forward, Z up) is rotated to
RIF's world (X right, Y up, Z forward) via blender_to_rif_axis. Vertices,
normals and object transforms all go through the same rotation so the
round-trip through the community importer is lossless.

S6 Faza 3 (OBJ_FLAGS milestone):
    - OBJ_FLAGS corrected from 0x300 (BASE_OBJECT | MODULE_OBJECT) to
      0x200 (MODULE_OBJECT only). Verified byte-for-byte against
      battle.rif: every renderable module in the retail level uses
      0x200; the BASE_OBJECT bit is never set on modules. See
      OBCHUNK.HPP for flag definitions.
    - All other OBJ_FLAGS bits (AVPGAMEMODE*, PCLOAD, PLATFORMLOADSET,
      NOTDIFFICULTY*) are only relevant for placed objects and generators,
      not for static modules, so we intentionally do not set them.
"""
from math import sqrt

from .types import (
    DEFAULT_LOCK_USER, DEFAULT_NOTES,
    meters_to_rif, uv_to_rif_pixels, blender_to_rif_axis,
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
from .chunks.hierarchy import (
    ObjectHierarchyChunk, ObjectHierarchyDataChunk,
    ObjectHierarchyNameChunk,
)
from .chunks.environment import (
    EnvironmentDataChunk, EnvironmentHeaderChunk, RIFNameChunk,
    BMPNamesChunk, BMPNamesVersionChunk, BMPNamesExtraDataChunk,
    EnvironmentScaleChunk, SpecialObjectsChunk, PlayerStartChunk,
    AVPEnvironmentSettingsChunk,
    MATRIX_ONE,
)
from .chunks.lighting import (
    LightSetChunk, LightSetHeaderChunk, AmbienceChunk, LightScaleChunk,
    StandardLightChunk, ShapeVertexIntensitiesChunk,
    IDENTITY_MATRIX_16_16, DEFAULT_LIGHT_SPECS,
)
from .chunks.module import (
    ObjectModuleDataChunk, AdjacentModuleEntryPointsChunk,
    VModuleArrayChunk, ModuleAcousticsChunk,
)


SHAPE_FLAGS = 0
SHAPE_VERSION_NO = 1
POLY_FLAGS = 0x00C00000
POLY_ENGINE_TYPE = 7

# OBJHEAD1.flags — verified against OBCHUNK.HPP + battle.rif.
#
#   OBJECT_FLAG_BASE_OBJECT   0x0000100  base object marker
#   OBJECT_FLAG_MODULE_OBJECT 0x0000200  participates in module/collision system
#   OBJECT_FLAG_PLACED_OBJECT 0x0000400  handled by deal_with_placed_object
#   OBJECT_FLAG_PCLOAD        0x0008000  platform-specific load flag
#   (AVPGAMEMODE* bits only apply to generators/placed pickups)
#
# Every static renderable module in battle.rif uses exactly 0x200.
# The previous 0x300 (BASE_OBJECT | MODULE_OBJECT) was never observed
# in retail data and is now corrected.
OBJ_FLAGS = 0x00000200
OBJ_VERSION_NO = 1

POLY_BITMAP_MASK = 0x00000FFF
POLY_UV_HI_MASK  = 0x0000F000
BITMAP_IDX_PROP = "rif_bitmap_idx"

DEFAULT_LIGHT_SET = "NORMALLT"


# ------------------------------------------------------------------ public API

def build_rif_bytes(mesh, object_name: str = None) -> bytes:
    """Serialise a single Blender mesh to a REBINFF2 byte buffer."""
    if object_name is None:
        object_name = mesh.name or "Mesh"

    slot_bitmaps = _resolve_bitmap_indices(mesh)
    shape = _build_shape_chunk(mesh, object_name, 1, slot_bitmaps)
    obj = _build_object_chunk(
        name=object_name,
        location_rif=(0, 0, 0),
        orientation_rif=(-0.0, -0.0, -0.0, 0.0),
        index_num=0,
        shape_id_no=1,
        num_vertices=len(mesh.vertices),
        module_data=None,      # bare shape export: no module, no physics
    )
    root = GodFatherChunk([
        RIFVersionChunk(0),
        shape,
        obj,
    ])
    return root.serialize()


def build_rif_multi(objects, hierarchy=None) -> bytes:
    """Serialise a list of Blender mesh objects into one REBINFF2 buffer.

    No environment envelope, no module data — this is the export path used
    when assembling pieces before handing them to a level build.
    """
    objects = list(objects)
    if not objects:
        raise ValueError("build_rif_multi requires at least one object")

    shapes = []
    objs = []
    for i, obj in enumerate(objects):
        if obj.type != "MESH":
            raise ValueError(f"Object {obj.name!r} is not a mesh")
        file_id = i + 1
        slot_bitmaps = _resolve_bitmap_indices(obj.data)
        shapes.append(_build_shape_chunk(
            obj.data, obj.name, file_id, slot_bitmaps,
        ))

        loc, rot_q, _scale = obj.matrix_local.decompose()
        bx, by, bz = blender_to_rif_axis(loc.x, loc.y, loc.z)
        loc_rif = (meters_to_rif(bx), meters_to_rif(by), meters_to_rif(bz))
        orient_rif = _convert_orientation(rot_q)
        objs.append(_build_object_chunk(
            name=obj.name,
            location_rif=loc_rif,
            orientation_rif=orient_rif,
            index_num=i,
            shape_id_no=file_id,
            num_vertices=len(obj.data.vertices),
            module_data=None,
        ))

    children = [RIFVersionChunk(0)] + shapes + objs
    if hierarchy:
        children.append(_build_hierarchy_chunk(hierarchy))

    return GodFatherChunk(children).serialize()


def build_rif_level(objects, level_name, texture_paths=None,
                    player_start=None, player_orientation=None,
                    hierarchy=None, include_scale=False,
                    lights=None, ambient=255,
                    light_set_name=DEFAULT_LIGHT_SET,
                    include_litscale=False,
                    sky_colour=(128, 128, 128),
                    include_avpenvir=True,
                    include_module_data=True) -> bytes:
    """Assemble a level RIF: shapes + objects + MODULEDT + REBENVDT.

    Composition:
        REBINFF2
          RIFVERIN
          REBSHAPE x N + RBOBJECT x N   (each RBOBJECT carries MODULEDT + SHPVTINT)
          OBJCHIER (opt.)
          REBENVDT
            ENDTHEAD + RIFFNAME + BMPNAMES + BMNAMEXT + BMNAMVER
            SPECLOBJ > AVPSTART (opt.)
            LIGHTSET (LTSETHDR + [LITSCALE] + AMBIENCE + STDLIGHT x N)
            AVPENVIR (opt.)
            ENVSDSCL (opt.)

    include_module_data : when True (default), every RBOBJECT receives a
        MODULEDT chunk with a full VMDARRAY of peer indices and an empty
        ADJMDLEP. This is what makes the geometry visible to the physics
        engine. Set False only for debugging.
    """
    objects = list(objects)
    if not objects:
        raise ValueError("build_rif_level requires at least one object")

    n_objects = len(objects)
    all_indices = list(range(n_objects))

    shapes = []
    objs = []
    for i, obj in enumerate(objects):
        if obj.type != "MESH":
            raise ValueError(f"Object {obj.name!r} is not a mesh")
        file_id = i + 1
        slot_bitmaps = _resolve_bitmap_indices(obj.data)
        shapes.append(_build_shape_chunk(
            obj.data, obj.name, file_id, slot_bitmaps,
        ))

        loc, rot_q, _scale = obj.matrix_local.decompose()
        bx, by, bz = blender_to_rif_axis(loc.x, loc.y, loc.z)
        loc_rif = (meters_to_rif(bx), meters_to_rif(by), meters_to_rif(bz))
        orient_rif = _convert_orientation(rot_q)

        module_data = None
        if include_module_data:
            # Every object links to every other object (self included).
            # For our test levels this forms a fully-connected module
            # graph. Large levels would prune this with spatial reasoning.
            module_data = {
                "vmdarray": [
                    (peer_idx, 0, 0, 0) for peer_idx in all_indices
                ],
                "adjmdlep": [],   # no inter-module portals in a single blob
                "modacous": (-1, -1.0, 0),
            }

        objs.append(_build_object_chunk(
            name=obj.name,
            location_rif=loc_rif,
            orientation_rif=orient_rif,
            index_num=i,
            shape_id_no=file_id,
            num_vertices=len(obj.data.vertices),
            module_data=module_data,
        ))

    # ---- environment skeleton (REBENVDT) ----
    env_children = [
        EnvironmentHeaderChunk(),
        RIFNameChunk(level_name),
        _build_bmpnames(texture_paths),
        BMPNamesExtraDataChunk(),
        BMPNamesVersionChunk(1),
    ]
    speclobj_children = []
    if player_start is not None:
        speclobj_children.append(
            _build_avpstart(player_start, player_orientation)
        )
    env_children.append(SpecialObjectsChunk(speclobj_children))
    env_children.append(
        _build_lightset(light_set_name, lights, ambient, include_litscale)
    )
    if include_avpenvir:
        env_children.append(AVPEnvironmentSettingsChunk(
            sky_r=sky_colour[0],
            sky_g=sky_colour[1],
            sky_b=sky_colour[2],
        ))
    if include_scale:
        env_children.append(EnvironmentScaleChunk(1.0))

    env = EnvironmentDataChunk(env_children)

    children = [RIFVersionChunk(0)] + shapes + objs
    if hierarchy:
        children.append(_build_hierarchy_chunk(hierarchy))
    children.append(env)

    return GodFatherChunk(children).serialize()


# ------------------------------------------------------------------ helpers

def _convert_orientation(quat):
    """Rotate a Blender quaternion into RIF space; returns (x, y, z, w)."""
    from mathutils import Matrix
    M = Matrix((
        (1.0, 0.0, 0.0),
        (0.0, 0.0, -1.0),
        (0.0, 1.0, 0.0),
    ))
    m_rif = M @ quat.to_matrix() @ M.inverted()
    q_rif = m_rif.to_quaternion()
    return (q_rif.x, q_rif.y, q_rif.z, q_rif.w)


def _convert_orientation_matrix(quat):
    """Convert a Blender quaternion to a RIF 3x3 fixed-point matrix (16.16)."""
    from mathutils import Matrix
    M = Matrix((
        (1.0, 0.0, 0.0),
        (0.0, 0.0, -1.0),
        (0.0, 1.0, 0.0),
    ))
    m_rif = M @ quat.to_matrix() @ M.inverted()
    return tuple(
        int(round(m_rif[row][col] * MATRIX_ONE))
        for row in range(3) for col in range(3)
    )


def _build_hierarchy_chunk(hierarchy):
    """Build a recursive OBJCHIER tree from {child_name: parent_name}."""
    children_of = {}
    all_children = set()
    all_parents = set()
    for child, parent in hierarchy.items():
        children_of.setdefault(parent, []).append(child)
        all_children.add(child)
        all_parents.add(parent)

    roots = sorted(all_parents - all_children)
    if not roots:
        roots = [sorted(all_parents)[0]] if all_parents else []

    wrapper_children = []
    for root in roots:
        wrapper_children.append(_build_hierarchy_node(root, children_of))
    wrapper_children.append(ObjectHierarchyNameChunk(roots[0] if roots else ""))
    return ObjectHierarchyChunk(wrapper_children)


def _build_hierarchy_node(name, children_of):
    """Recursively build one OBJCHIER node: OBJHIERD + child OBJCHIER nodes."""
    node_children = [ObjectHierarchyDataChunk(name)]
    for child in sorted(children_of.get(name, [])):
        node_children.append(_build_hierarchy_node(child, children_of))
    return ObjectHierarchyChunk(node_children)


def _build_shape_chunk(mesh, object_name, file_id_num, slot_bitmaps):
    """Return a fully assembled REBSHAPE chunk for one mesh."""
    verts_int, vert_normals, polys, poly_normals, uv_lists = \
        _extract_geometry(mesh, slot_bitmaps)
    bounds = _compute_bounds(verts_int)
    centre = _compute_centre(bounds)
    radius = _compute_radius(verts_int, centre)
    uv_pixels = _uv_to_pixels(uv_lists)

    return ShapeChunk([
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
    ])


def _build_object_chunk(name, location_rif, orientation_rif,
                        index_num, shape_id_no, num_vertices=0,
                        module_data=None):
    """Return a fully assembled RBOBJECT chunk.

    Children order matches battle.rif:
        OBJHEAD1 → OBINTDT → MODULEDT (opt.) → SHPVTINT (opt.)

    module_data : dict or None. When provided, a MODULEDT child is
        emitted. Keys:
            vmdarray : list of (object_index, branch_no, flags, spare)
            adjmdlep : list of (object_index, flags, (x, y, z))
            modacous : tuple (env_index, reverb, spare), optional
    """
    obj_header = ObjectHeaderChunk(
        flags=OBJ_FLAGS,
        lock_user=DEFAULT_LOCK_USER,
        location=location_rif,
        orientation=orientation_rif,
        index_num=index_num,
        version_no=OBJ_VERSION_NO,
        shape_id_no=shape_id_no,
        o_name=name,
    )
    children = [
        obj_header,
        ObjectInterfaceDataChunk([ObjectNotesChunk(DEFAULT_NOTES)]),
    ]
    if module_data is not None:
        children.append(_build_module_data(module_data))
    # SHPVTINT intentionally disabled: shipping files include it, but the
    # engine treats missing SHPVTINT as "no prelight" and falls back to
    # ambient lighting. Emitting all-zero SHPVTINT was tested in S6 F2
    # and made no visible difference; leaving it off reduces file size
    # and removes one variable while we chase the render bug.
    # if num_vertices > 0:
    #     children.append(ShapeVertexIntensitiesChunk(
    #         light_set_name=DEFAULT_LIGHT_SET,
    #         num_vertices=num_vertices,
    #     ))
    return ObjectChunk(children)


def _build_module_data(spec):
    """Build a MODULEDT chunk from a spec dict."""
    children = [
        AdjacentModuleEntryPointsChunk(spec.get("adjmdlep", [])),
        ModuleAcousticsChunk(*spec.get("modacous", (-1, -1.0, 0))),
        VModuleArrayChunk(spec.get("vmdarray", [])),
    ]
    return ObjectModuleDataChunk(children)


def _build_bmpnames(texture_paths):
    """Wrap texture paths into a BMPNAMES chunk (never empty)."""
    if not texture_paths:
        texture_paths = ["dummy.RIM"]
    entries = [{"index": i, "name": str(p)} for i, p in enumerate(texture_paths)]
    return BMPNamesChunk(entries)


def _build_avpstart(player_start, player_orientation):
    """Build an AVPSTART chunk (SPECLOBJ child) at the requested pose."""
    bx, by, bz = blender_to_rif_axis(*player_start)
    loc_rif = (meters_to_rif(bx), meters_to_rif(by), meters_to_rif(bz))
    orient_ints = (_convert_orientation_matrix(player_orientation)
                   if player_orientation is not None else None)
    return PlayerStartChunk(location=loc_rif, orientation=orient_ints)


def _build_lightset(name, lights, ambient, include_litscale):
    """Assemble a LIGHTSET chunk; guarantees at least one STDLIGHT."""
    children = [LightSetHeaderChunk(name)]
    if include_litscale:
        children.append(LightScaleChunk())
    children.append(AmbienceChunk(ambient))
    if not lights:
        lights = DEFAULT_LIGHT_SPECS
    for i, spec in enumerate(lights):
        children.append(_make_stdlight(i, spec))
    return LightSetChunk(children)


def _make_stdlight(index, spec):
    """Build one STDLIGHT record from a light spec dict."""
    loc = spec.get("location", (0.0, 0.0, 0.0))
    bx, by, bz = blender_to_rif_axis(*loc)
    loc_rif = (meters_to_rif(bx), meters_to_rif(by), meters_to_rif(bz))
    return StandardLightChunk(
        light_number=index,
        location=loc_rif,
        orientation=spec.get("orientation", IDENTITY_MATRIX_16_16),
        brightness=spec.get("brightness", 255),
        spread=spec.get("spread", 360),
        range=spec.get("range", 2000),
        colour=spec.get("colour", 0x00FFFFFF),
        engine_light_flags=spec.get("engine_flags", 0),
        local_light_flags=spec.get("local_flags", 0),
    )


def _resolve_bitmap_indices(mesh):
    """Return per-material-slot RIF bitmap indices (default = slot idx)."""
    result = []
    for slot_idx, mat in enumerate(mesh.materials):
        idx = slot_idx
        if mat is not None and BITMAP_IDX_PROP in mat:
            try:
                idx = int(mat[BITMAP_IDX_PROP])
            except (TypeError, ValueError):
                pass
        result.append(idx & POLY_BITMAP_MASK)
    return result


def _extract_geometry(mesh, slot_bitmaps):
    """Return (verts_int, vert_normals, polys, poly_normals, uv_lists)."""
    verts_int = []
    vert_normals = []
    for vert in mesh.vertices:
        co = vert.co
        bx, by, bz = blender_to_rif_axis(co.x, co.y, co.z)
        verts_int.append((
            meters_to_rif(bx),
            meters_to_rif(by),
            meters_to_rif(bz),
        ))
        n = vert.normal
        vert_normals.append(blender_to_rif_axis(n.x, n.y, n.z))

    uv_layer = mesh.uv_layers.active
    polys = []
    poly_normals = []
    uv_lists = []

    poly_index = 0
    for poly in mesh.polygons:
        indices = list(poly.vertices)
        n = poly.normal
        face_normal = blender_to_rif_axis(n.x, n.y, n.z)

        if uv_layer is not None:
            loop_uvs = [tuple(uv_layer.data[li].uv)
                        for li in poly.loop_indices]
        else:
            loop_uvs = [(0.0, 0.0)] * len(indices)

        slot = poly.material_index
        bitmap_idx = slot_bitmaps[slot] if slot < len(slot_bitmaps) else 0

        if len(indices) > 4:
            for i in range(1, len(indices) - 1):
                tri = [indices[0], indices[i], indices[i + 1]]
                tri_uvs = [loop_uvs[0], loop_uvs[i], loop_uvs[i + 1]]
                polys.append(_make_poly_record(tri, poly_index, bitmap_idx))
                poly_normals.append(face_normal)
                uv_lists.append(tri_uvs)
                poly_index += 1
        else:
            polys.append(_make_poly_record(indices, poly_index, bitmap_idx))
            poly_normals.append(face_normal)
            uv_lists.append(loop_uvs)
            poly_index += 1

    return verts_int, vert_normals, polys, poly_normals, uv_lists


def _make_poly_record(vert_indices, poly_index, bitmap_idx=0):
    """Build a polygon record dict, padding vertex list with -1."""
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