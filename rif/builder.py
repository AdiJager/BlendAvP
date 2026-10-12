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

S6 F3 (OBJ_FLAGS milestone):
    - OBJ_FLAGS corrected to 0x200 (MODULE_OBJECT only), verified against
      battle.rif.

S6 F6 (H45 + H53):
    - Safe Spawn places AVPSTART at AABB centre so AllNewModuleHandler
      creates DISPLAYBLOCKs for our modules.
    - texture_override forces bitmap_idx 0 pointing at a real .RIM.

S6 F7 (prelight path):
    - Prelight via SHPVTINT is the ONLY way to light static geometry in
      AvP without a full placed-light-object system (see bh_light.c).
      VertexIntensity_Standard_Opt in Kshape.c reads SHPVTINT only when the
      runtime shape has ShapeFlag_PreLit — without prelight and without
      runtime lights every vertex colour is 0 and the geometry renders black.
    - build_rif_level emits SHPVTINT (all-white prelight by default).
    - LightScaleChunk fixed: 40-byte payload, not 44.
    - StandardLightChunk defaults fixed: brightness = ONE_FIXED (65536),
      spread = 4096 (full circle).

S6 F8 (re-diagnosis of the S6 F7 regression):
    - The S6 F7 claim that 0x00000001 made the loader treat the shape as a
      .3DS import and swap geometry axes is wrong: `ShapeFlag_3DS_AxisFlip`
      is only defined in shape.h and no compiled source reads it.
    - `build_rif_bytes` no longer writes a zero quaternion in OBJHEAD1.

S6 F9 (ROOT CAUSE — "empty level with Include Prelight on"):
    - shape.h:421 defines ShapeFlag_PreLit = 0x00001000 and
      SHPCHUNK.HPP:15 defines SHAPE_FLAG_EXTERNALFILE = 0x0001000: the SAME
      bit. Writing ShapeFlag_PreLit into SHPHEAD1.flags therefore made
      Projload.cpp:1974 take the "imported shape" branch, which calls
      copy_to_mainshapelist() with a stale object pointer (line 1986) and
      routes every linked object to deal_with_placed_object() instead of
      copy_to_module(). No MODULE is registered, AllNewModuleHandler
      (MODULE.C:123) creates no DISPLAYBLOCK, and the level renders empty.
      The file still validated: it is a loader-side semantic collision.
    - Fix: never write bit 0x1000 into SHPHEAD1.flags. Prelight is activated
      by the SHPVTINT chunk alone — Projload.cpp:1455/2067 OR the bit into
      the runtime shape on its own.
"""
from math import sqrt

from . import modules
from .chunks.strategy import AVPStrategyChunk, ObjectProjectDataChunk
from .chunks.waypoint import WaypointChunk
from .types import (
    DEFAULT_LOCK_USER, DEFAULT_NOTES,
    meters_to_rif, uv_to_rif_pixels, blender_to_rif_axis,
)
from .texture_library import rim_name_for_material
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
    AVPGeneratorChunk, generator_flags, GlobalGeneratorDataChunk,
    GEN_MULTIPLAYERSTART, AVPGeneratorExtraChunk, GeneratorExSettingsChunk,
    AVPEnvironmentSettingsChunk,
    MATRIX_ONE,
)
from .chunks.lighting import (
    LightSetChunk, LightSetHeaderChunk, AmbienceChunk, LightScaleChunk,
    StandardLightChunk, ShapeVertexIntensitiesChunk,
    IDENTITY_MATRIX_16_16, ONE_FIXED,
)
from .chunks.module import (
    ObjectModuleDataChunk, AdjacentModuleEntryPointsChunk,
    ModuleAcousticsChunk, VModuleArrayChunk,
    VModuleArrayChunk, ModuleAcousticsChunk,
)


# ------------------------------------------------------------------ constants

# Shape_Header_Chunk.flags. Bit layout verified against shape.h:
#
#     0x00000001  ShapeFlag_3DS_AxisFlip        (defined only, never read)
#     0x00000002  ShapeFlag_RSP
#     0x00000004  ShapeFlag_Detail
#     0x00000010  ShapeFlag_AugZ
#     0x00000020  ShapeFlag_AugZ_Lite
#     0x00000040  ShapeFlag_Free1
#     0x00000080  ShapeFlag_SizeSortItems
#     0x00000100  ShapeFlag_VSC_tx3d
#     0x00000200  ShapeFlag_ZSP
#     0x00000400  ShapeFlag_Sprite
#     0x00000800  ShapeFlag_SpriteR
#     0x00001000  ShapeFlag_PreLit / SHAPE_FLAG_EXTERNALFILE  <<< COLLISION
#     0x00002000  ShapeFlag_Cylinder
#     0x00008000  ShapeFlag_SpriteResizing
#     0x00010000  ShapeFlag_MultiViewSprite
#     0x00020000  ShapeFlag_UnrotatedPoints
#     0x00040000  ShapeFlag_HasTextureAnimation
#
# S6 F9 — ROOT CAUSE of the "empty level with prelight on" bug:
#
#   shape.h:421       #define ShapeFlag_PreLit        0x00001000
#   SHPCHUNK.HPP:15   #define SHAPE_FLAG_EXTERNALFILE 0x0001000
#
# The two constants share bit 0x1000. Writing ShapeFlag_PreLit into
# SHPHEAD1.flags therefore also raises SHAPE_FLAG_EXTERNALFILE, and
# Projload.cpp:1974 sends the shape down the "imported shape" branch:
#
#   * copy_to_mainshapelist() is called with the stale `ob` of the previous
#     object loop (Projload.cpp:1986) instead of the object being processed,
#     so the shape's vertices are transformed by a dangling pointer;
#   * every linked object is pushed to deal_with_placed_object() and never to
#     copy_to_module(), so no MODULE is registered and AllNewModuleHandler
#     (MODULE.C:123) can never create a DISPLAYBLOCK — the level renders as
#     empty even though collisions and loading report no error.
#
# The bit is NOT needed in the file: Projload.cpp:1455/2067 OR it into the
# runtime shape as soon as the RBOBJECT carries a matching SHPVTINT.
# So prelight is driven purely by SHPVTINT, and the file-side flag stays 0.
SHAPE_FLAG_PRELIT = 0x00001000   # ShapeFlag_PreLit — do NOT write to a file

# Alias for the loader-side meaning of the same bit (SHPCHUNK.HPP:15).
SHAPE_FLAG_EXTERNALFILE = 0x00001000   # makes Projload.cpp:1974 misfire

# Flags actually written into SHPHEAD1. Always 0 for our exports: prelight is
# activated by the SHPVTINT chunk, and 0x1000 must stay clear to avoid the
# SHAPE_FLAG_EXTERNALFILE collision above.
SHAPE_FLAGS_DEFAULT = 0

# Prelight path: full white per vertex (packed 0x00RRGGBB).
PRELIGHT_WHITE = 0x00FFFFFF


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
OBJ_FLAGS = 0x00000200

# What retail puts on a placed object. Measured across the 63 shipping levels:
# 4530 objects carry exactly this, and it is the six bits below. `PCLOAD` is not
# decorative -- `Objsetup.cpp:2278` skips an object without it **silently**, and
# one of the AVPGAMEMODE bits is likewise required before the object is created
# at all. All three race bits are set because a pickup is not race-specific in
# the shipping data.
PLACED_OBJECT_FLAGS = 0x0000FC00
OBJ_VERSION_NO = 1

POLY_BITMAP_MASK = 0x00000FFF
POLY_UV_HI_MASK  = 0x0000F000
BITMAP_IDX_PROP = "rif_bitmap_idx"

DEFAULT_LIGHT_SET = "NORMALLT"

# LTCHUNK.HPP:15 — marks a STDLIGHT as one SetUpRunTimeLights should adopt.
LIGHT_LOCAL_FLAG_RUNTIME = 0x00000010


# ------------------------------------------------------------------ public API

def build_rif_bytes(mesh, object_name: str = None,
                    include_prelight: bool = True) -> bytes:
    """Serialise a single Blender mesh to a REBINFF2 byte buffer.

    include_prelight : when True (default), emit an all-white SHPVTINT
        and set ShapeFlag_PreLit on the shape so the engine reads it.
        Without prelight, VertexIntensity_Standard_Opt in Kshape.c
        returns a zero vertex colour and the mesh renders black.
    """
    if object_name is None:
        object_name = mesh.name or "Mesh"

    slot_bitmaps = _resolve_bitmap_indices(mesh)
    shape_flags = SHAPE_FLAGS_DEFAULT

    shape = _build_shape_chunk(
        mesh, object_name, 1, slot_bitmaps, shape_flags=shape_flags,
    )

    num_vertices = len(mesh.vertices)
    prelight = None
    if include_prelight and num_vertices > 0:
        prelight = [PRELIGHT_WHITE] * num_vertices

    obj = _build_object_chunk(
        name=object_name,
        location_rif=(0, 0, 0),
        orientation_rif=(0.0, 0.0, 0.0, 1.0),
        index_num=0,
        shape_id_no=1,
        num_vertices=num_vertices,
        module_data=None,
        prelight_intensities=prelight,
    )
    root = GodFatherChunk([
        RIFVersionChunk(0),
        shape,
        obj,
    ])
    return root.serialize()


def build_rif_multi(objects, hierarchy=None,
                    include_prelight: bool = True) -> bytes:
    """Serialise a list of Blender mesh objects into one REBINFF2 buffer.

    No environment envelope, no module data — this is the export path used
    when assembling pieces before handing them to a level build.
    """
    objects = list(objects)
    if not objects:
        raise ValueError("build_rif_multi requires at least one object")

    # See the SHAPE_FLAG_EXTERNALFILE note above: the file-side flags stay 0
    # and prelight travels in the SHPVTINT chunk only.
    shape_flags = SHAPE_FLAGS_DEFAULT

    shapes = []
    objs = []
    for i, obj in enumerate(objects):
        if obj.type != "MESH":
            raise ValueError(f"Object {obj.name!r} is not a mesh")
        file_id = i + 1
        slot_bitmaps = _resolve_bitmap_indices(obj.data)
        shape = _build_shape_chunk(
            obj.data, obj.name, file_id, slot_bitmaps,
            shape_flags=shape_flags, recentre=True,
        )
        shapes.append(shape)

        loc, rot_q, _scale = obj.matrix_world.decompose()
        bx, by, bz = blender_to_rif_axis(loc.x, loc.y, loc.z)
        shift = shape.origin_offset
        loc_rif = (meters_to_rif(bx) + shift[0], meters_to_rif(by) + shift[1],
                   meters_to_rif(bz) + shift[2])
        orient_rif = _convert_orientation(rot_q)

        num_vertices = len(obj.data.vertices)
        prelight = None
        if include_prelight and num_vertices > 0:
            prelight = [PRELIGHT_WHITE] * num_vertices

        objs.append(_build_object_chunk(
            name=obj.name,
            location_rif=loc_rif,
            orientation_rif=orient_rif,
            index_num=i,
            shape_id_no=file_id,
            num_vertices=num_vertices,
            module_data=None,
            prelight_intensities=prelight,
        ))

    children = [RIFVersionChunk(0)] + shapes + objs
    # NOTE: the OBJCHIER / OBJHIERD / OBHIERNM chunks are deliberately NOT
    # written. They appear in ZERO of the 63 retail levels, and emitting them
    # crashes the game on level load as soon as anything is parented. The
    # outliner hierarchy is still read for the module visibility graph; it just
    # does not belong in the file.

    return GodFatherChunk(children).serialize()


def build_rif_level(objects, level_name, texture_paths=None,
                    player_start=None, player_orientation=None,
                    hierarchy=None, global_scale=None,
                    lights=None, ambient=255,
                    light_set_name=DEFAULT_LIGHT_SET,
                    include_litscale=True,
                    emit_runtime_lights=False,
                    sky_colour=(128, 128, 128),
                    include_avpenvir=True,
                    include_module_data=True,
                    include_prelight=True,
                    prelight_colour=PRELIGHT_WHITE,
                    object_intensities=None,
                    shape_flags_override=None,
                    spawns=None,
                    global_generator=None,
                    waypoints=None,
                    placed_objects=None) -> bytes:
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

    include_prelight : when True (default), every RBOBJECT gets a SHPVTINT
        with the given per-vertex intensity, which is the ONLY path to lit
        static geometry without a full placed-light-object system. The
        loader then sets ShapeFlag_PreLit on the runtime shape by itself
        (Projload.cpp:1455/2067); the flag is deliberately NOT written into
        SHPHEAD1 (see the SHAPE_FLAG_EXTERNALFILE collision note at the top
        of this module). Set False to reproduce the pre-S6-F7 unlit export.

    lights : optional list of light spec dicts written as STDLIGHT chunks.
        `location` is in RIF millimetres, ALREADY axis-converted (x, -z, y) --
        the operator's `_collect_scene_lights` does that once, so the STDLIGHT
        record and the prelight bake compare positions in the same space as
        SHPRAWVT. Brightness is 16.16 fixed point, range is RIF millimetres and
        colour is packed 0x00BBGGRR. There is no fallback light: pass an empty
        list for an empty LIGHTSET. A fabricated light would light the player
        and NPCs, which read exactly these chunks.

    emit_runtime_lights : when True, every written STDLIGHT also gets
        LOFlag_Runtime so the engine's SetUpRunTimeLights adopts it as a real
        light for whichever module contains it. That is what lights dynamic
        objects — players, NPCs, dropped items — because only module objects
        carry ObFlag3_PreLit (comp_map.c:457) and are skipped by Avpview.c:170.
        Leave True for playable levels.

    prelight_colour : packed 0x00RRGGBB per-vertex intensity used when
        `object_intensities` has no entry for an object. Use PRELIGHT_WHITE
        for a fully-lit flat surface.

    object_intensities : optional list aligned with `objects`, each entry
        either None or a list of packed 0x00RRGGBB values, one per vertex of
        that mesh. This is where baked point-light results go: compute them
        with `rif.lighting.bake_for_object` so the values match what the
        engine would have produced itself.

    placed_objects : optional list of dicts, each describing one placed
        object such as a pickup. Keys:
            mesh      : the mesh datablock to export as its REBSHAPE
            name      : object name written to OBJHEAD1
            location  : (x, y, z) in RIF millimetres
            orientation : quaternion, already in RIF form
            strategy  : keyword arguments for `AVPStrategyChunk`

        A placed object gets a shape and an RBOBJECT but **no MODULEDT**: the
        engine treats it as a thing in the world rather than as space the player
        walks through, and it is the presence of the group flag that decides
        that (Projload.cpp:2078). Its shape index continues the module
        numbering, because the loader resolves shapes through one list.

    waypoints : optional {module index: [record, ...]} mapping. Each record
        is `(min, max, centre, flags, links)` in absolute RIF millimetres. A
        module with no entry gets no WAYPOINT chunk at all.

    shape_flags_override : force a specific SHAPE_FLAGS value instead
        of the computed one. Kept for diagnostics only — never set bit
        0x1000 on a module shape (see the SHAPE_FLAG_EXTERNALFILE note at
        the top of this module).
    """
    objects = list(objects)
    if not objects:
        raise ValueError("build_rif_level requires at least one object")

    # Prelight is carried by SHPVTINT and activated by the loader itself
    # (Projload.cpp:1455/2067). SHPHEAD1.flags must stay 0: bit 0x1000 is
    # ALSO SHAPE_FLAG_EXTERNALFILE (SHPCHUNK.HPP:15) and triggers the
    # imported-shape branch at Projload.cpp:1974, which never registers a
    # module — the level then renders empty.
    if shape_flags_override is not None:
        shape_flags = shape_flags_override
    else:
        shape_flags = SHAPE_FLAGS_DEFAULT

    n_objects = len(objects)
    all_indices = list(range(n_objects))

    # Module bounding boxes in RIF millimetres, in the same frame the object
    # locations use. Computed up front because the linking step needs every
    # module's box before it can write any module's chunk.
    module_bounds = []
    module_origins = []
    for obj in objects:
        if obj.type != "MESH":
            raise ValueError(f"Object {obj.name!r} is not a mesh")
        loc, _rot, _scale = obj.matrix_world.decompose()
        bx, by, bz = blender_to_rif_axis(loc.x, loc.y, loc.z)
        world_origin = (meters_to_rif(bx), meters_to_rif(by),
                        meters_to_rif(bz))
        # The shape is recentred below, so the module's position has to absorb
        # the same shift. world_origin is the true world position and is what
        # the module box and the AI entry points are measured against; the two
        # only differ in which frame they are expressed in, never in where the
        # geometry ends up.
        module_origins.append(world_origin)
        points = []
        matrix = obj.matrix_world
        for vertex in obj.data.vertices:
            world = matrix @ vertex.co
            vx, vy, vz = blender_to_rif_axis(world.x, world.y, world.z)
            points.append((meters_to_rif(vx), meters_to_rif(vy),
                           meters_to_rif(vz)))
        module_bounds.append(modules.box_of(points))

    module_neighbours = modules.neighbours(module_bounds, module_origins)

    # The outliner hierarchy doubles as the visibility graph: a module sees its
    # parent and its children (see `modules.linked_modules`). `hierarchy` maps
    # object NAME to parent NAME because that is what the writer needs for
    # OBJHIERD; indices are what the module graph needs.
    name_to_index = {obj.name: i for i, obj in enumerate(objects)}
    parents = []
    for obj in objects:
        parent_name = (hierarchy or {}).get(obj.name)
        parents.append(name_to_index.get(parent_name))

    shapes = []
    objs = []
    for i, obj in enumerate(objects):
        if obj.type != "MESH":
            raise ValueError(f"Object {obj.name!r} is not a mesh")
        file_id = i + 1
        slot_bitmaps = _resolve_bitmap_indices(obj.data, texture_paths)
        shape = _build_shape_chunk(
            obj.data, obj.name, file_id, slot_bitmaps,
            shape_flags=shape_flags, recentre=True,
        )
        shapes.append(shape)

        # `module_origins[i]` is the true world position, which the module box
        # and the AI entry points are measured against. The location written to
        # the file also absorbs the shape's recentring offset, so that
        # `location + local vertices` still lands on exactly the same world
        # point. This is the retail convention: local vertices, position in
        # `object_data.location`.
        shift = shape.origin_offset
        loc_rif = tuple(module_origins[i][a] + shift[a] for a in range(3))

        loc, rot_q, _scale = obj.matrix_world.decompose()
        orient_rif = _convert_orientation(rot_q)
        orient_rif = _convert_orientation(rot_q)

        module_data = None
        if include_module_data:
            # VMDARRAY is a VISIBILITY SCRIPT, not a graph (MODULE.C:190): the
            # engine walks it and marks every module it reaches as visible.
            # It comes from the outliner hierarchy — parent and children — and
            # not from the geometry, because a box test cannot tell a doorway
            # from a wall and treats a hollow room as a solid block.
            #
            # ADJMDLEP is the separate AI graph, built from which module boxes
            # actually meet. Without it NPCs have no path anywhere.
            module_data = {
                "vmdarray": modules.build_vmdarray(
                    i, modules.linked_modules(i, parents)),
                "adjmdlep": modules.build_adjmdlep(
                    module_neighbours.get(i, []), module_origins),
                "modacous": (-1, -1.0, 0),
                "waypoints": (waypoints or {}).get(i),
            }

        num_vertices = len(obj.data.vertices)
        prelight = None
        if include_prelight and num_vertices > 0:
            baked = (object_intensities[i]
                     if object_intensities and i < len(object_intensities)
                     else None)
            if baked is not None:
                prelight = list(baked)
            else:
                prelight = [prelight_colour] * num_vertices

        objs.append(_build_object_chunk(
            name=obj.name,
            location_rif=loc_rif,
            orientation_rif=orient_rif,
            index_num=i,
            shape_id_no=file_id,
            num_vertices=num_vertices,
            module_data=module_data,
            prelight_intensities=prelight,
        ))

    # ---- placed objects -------------------------------------------------
    # A pickup is not a module: it carries PLACED_OBJECT_FLAGS and an
    # OBJPRJDT > AVPSTRAT child, and no MODULEDT at all. Its shape is built
    # exactly like a module's so the item can be seen in the world.
    for entry in (placed_objects or []):
        file_id = len(shapes) + 1
        bitmap_slots = _resolve_bitmap_indices(entry["mesh"], texture_paths)
        # Local vertices with the position in location is the retail
        # convention, so no recentring is wanted here: a pickup is small and
        # sits where the artist put it.
        shapes.append(_build_shape_chunk(
            entry["mesh"], entry["name"], file_id, bitmap_slots,
        ))
        objs.append(_build_object_chunk(
            name=entry["name"],
            location_rif=entry["location"],
            orientation_rif=entry["orientation"],
            index_num=len(objs) + 1,
            shape_id_no=file_id,
            strategy=entry["strategy"],
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
    speclobj_children.extend(_build_generators(spawns))
    speclobj_children.extend(_build_generator_extras(spawns))
    env_children.append(SpecialObjectsChunk(speclobj_children))
    data_chunk = _build_global_generator(global_generator, spawns)
    if data_chunk is not None:
        env_children.append(data_chunk)
    env_children.append(
        _build_lightset(light_set_name, lights, ambient, include_litscale,
                        runtime_flags=emit_runtime_lights)
    )
    if include_avpenvir:
        env_children.append(AVPEnvironmentSettingsChunk(
            sky_r=sky_colour[0],
            sky_g=sky_colour[1],
            sky_b=sky_colour[2],
        ))
    if global_scale is not None:
        # A level-wide multiplier applied to every position on load
        # (`Projload.cpp:336`). Omitted rather than written as 1.0: 58 of the 63
        # retail levels have no ENVSDSCL at all, and multiplying by one is what
        # its absence already means.
        env_children.append(EnvironmentScaleChunk(global_scale))

    env = EnvironmentDataChunk(env_children)

    children = [RIFVersionChunk(0)] + shapes + objs
    # NOTE: the OBJCHIER / OBJHIERD / OBHIERNM chunks are deliberately NOT
    # written. They appear in ZERO of the 63 retail levels, and emitting them
    # crashes the game on level load as soon as anything is parented. The
    # outliner hierarchy is still read for the module visibility graph; it just
    # does not belong in the file.
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


def _build_shape_chunk(mesh, object_name, file_id_num, slot_bitmaps,
                       shape_flags=None, recentre=False):
    """Return a fully assembled REBSHAPE chunk for one mesh.

    shape_flags : SHPHEAD1.flags value; defaults to SHAPE_FLAGS_DEFAULT.
        Pass SHAPE_FLAG_PRELIT when the shape should read SHPVTINT.

    recentre : shift the vertices so the shape sits around the origin, and
        publish the shift as chunk.origin_offset. The caller adds that offset
        to the object's location, which leaves every vertex exactly where it
        was while putting the shape into the frame the engine expects: retail
        stores LOCAL vertices and carries the module's position in
        object_data.location, never world-space vertices with a zero
        location. With world-space vertices, m_world reads as (0, 0, 0) for
        every module, so the engine believes each one sits at the origin while
        its geometry is metres away — which is what makes a module fail to
        draw.
    """
    if shape_flags is None:
        shape_flags = SHAPE_FLAGS_DEFAULT

    verts_int, vert_normals, polys, poly_normals, uv_lists = \
        _extract_geometry(mesh, slot_bitmaps)
    offset = (0, 0, 0)
    if recentre:
        offset = _compute_centre(_compute_bounds(verts_int))
        verts_int = [(v[0] - offset[0], v[1] - offset[1], v[2] - offset[2])
                     for v in verts_int]
    bounds = _compute_bounds(verts_int)
    centre = _compute_centre(bounds)
    radius = _compute_radius(verts_int, centre)
    uv_pixels = _uv_to_pixels(uv_lists)

    chunk = ShapeChunk([
        ShapeHeaderChunk(
            flags=shape_flags,
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
    chunk.origin_offset = offset
    return chunk


def _build_object_chunk(name, location_rif, orientation_rif,
                        index_num, shape_id_no, num_vertices=0,
                        module_data=None, prelight_intensities=None,
                        strategy=None):
    """Return a fully assembled RBOBJECT chunk.

    Children order matches battle.rif:
        OBJHEAD1 → OBINTDT → MODULEDT (opt.) → SHPVTINT (opt.)

    strategy : dict or None. When provided the object is a **placed
        object** rather than a module: `OBJHEAD1` gets `PLACED_OBJECT_FLAGS`
        and an `OBJPRJDT > AVPSTRAT` child is emitted instead of `MODULEDT`.
        Keys are the keyword arguments of `AVPStrategyChunk`.

    module_data : dict or None. When provided, a MODULEDT child is
        emitted. Keys:
            vmdarray : list of (object_index, branch_no, flags, spare)
            adjmdlep : list of (object_index, flags, (x, y, z))
            modacous : tuple (env_index, reverb, spare), optional

    prelight_intensities : list[int] or None. When provided (and
        non-empty), a SHPVTINT child is emitted. Length must match
        num_vertices. The linked REBSHAPE must have ShapeFlag_PreLit
        set or the engine ignores this chunk (see Kshape.c).
    """
    obj_header = ObjectHeaderChunk(
        flags=(PLACED_OBJECT_FLAGS if strategy is not None else OBJ_FLAGS),
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
    if strategy is not None:
        # The engine reaches the strategy as
        # `ob->lookup_single_child("OBJPRJDT")->lookup_single_child("AVPSTRAT")`,
        # so both levels are needed (Objsetup.cpp:2235).
        children.append(ObjectProjectDataChunk([AVPStrategyChunk(**strategy)]))
    if prelight_intensities:
        children.append(ShapeVertexIntensitiesChunk(
            light_set_name=DEFAULT_LIGHT_SET,
            num_vertices=len(prelight_intensities),
            intensities=prelight_intensities,
        ))
    return ObjectChunk(children)


def _build_module_data(spec):
    """Build a MODULEDT chunk from a spec dict.

    Children order follows retail: the adjacency list first, then acoustics and
    the visibility array, with the waypoint volumes last when the module has
    any. A module with no waypoints gets no WAYPOINT child at all, rather than
    an empty one — retail ships both shapes, and omitting it keeps a level
    without navigation identical to before.
    """
    children = [
        AdjacentModuleEntryPointsChunk(spec.get("adjmdlep", [])),
        ModuleAcousticsChunk(*spec.get("modacous", (-1, -1.0, 0))),
        VModuleArrayChunk(spec.get("vmdarray", [])),
    ]
    waypoints = spec.get("waypoints")
    if waypoints:
        children.append(WaypointChunk(waypoints))
    return ObjectModuleDataChunk(children)


def _build_bmpnames(texture_paths):
    """Wrap texture paths into a BMPNAMES chunk (never empty).

    Entry ``index`` is the position in ``texture_paths``, and callers must pass
    a list whose positions match the `rif_bitmap_idx` they wrote onto the
    materials. Those two used to be derived independently -- BMPNAMES from a
    global list, the polygon index from each object's own slot numbering -- so
    with more than one object the second one pointed at the first one's texture.
    """
    if not texture_paths:
        # Never emit an empty BMPNAMES: the loader would then resolve no
        # texture at all and silently drop every polygon to iflag_nolight.
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


def _needs_global_generator(spawns):
    """Return True when the level has any generator that is not a player spawn.

    A multiplayer spawn needs no spawning budget: the engine places players
    itself. Anything else — a fixed NPC, a weighted generator, or a
    default-enemy generator — is fed by GLOGENDC, and with no budget the level
    stays empty however correct the generator is.
    """
    for spec in spawns or ():
        flags = spec.get("flags")
        if flags is None or not int(flags) & GEN_MULTIPLAYERSTART:
            return True
    return False


def _build_global_generator(global_generator, spawns):
    """Return the GLOGENDC chunk for this level, or None to omit it.

    `global_generator` decides:
        None   auto - write one when the level has any non-player generator
        False  never write one
        dict   write one, using the given fields over the retail defaults
    """
    if global_generator is False:
        return None
    if global_generator is None:
        if not _needs_global_generator(spawns):
            return None
        return GlobalGeneratorDataChunk.from_defaults()
    if isinstance(global_generator, GlobalGeneratorDataChunk):
        return global_generator
    return GlobalGeneratorDataChunk.from_defaults(**global_generator)


def _build_generator_extras(specs):
    """Return one AVPGeneratorExtraChunk per generator that carries settings.

    `AVPGENEX` is a SIBLING of `AVPGENER` inside `SPECLOBJ`, not its child, and
    the engine links the two by comparing names. Writing it anywhere else — or
    under a name that does not match — leaves the generator without settings.

    The settings chunk is written even when every weight is zero. That is not a
    degenerate case: with `ADVANCEDGENERATOR` set and `WeightingTotal == 0` the
    engine falls back to the level's default creature (`Objsetup.cpp:3105`),
    and it can only find the extended settings if the chunk exists. Omitting it
    is what made the mixed generator inert in game.
    """
    extras = []
    for index, spec in enumerate(specs or (), start=1):
        weights = spec.get("weights")
        if weights is None:
            continue
        # The generator name must match the spec's exactly, including the
        # counter `_build_generators` may have appended, so it is resolved the
        # same way here rather than assumed.
        extras.append(AVPGeneratorExtraChunk(
            name=spec.get("_resolved_name") or spec.get("name")
            or f"gen{index}",
            settings=GeneratorExSettingsChunk(
                generation_rate=int(spec.get("generation_rate", 0)),
                gen_rate_increase=int(spec.get("gen_rate_increase", 0)),
                gen_limit=int(spec.get("gen_limit", 0)),
                weights=weights),
        ))
    return extras


def _build_generators(spawns):
    """Return one AVPGeneratorChunk per spawn spec, names made unique.

    A spec is a dict with:
        location      (x, y, z) in RIF millimetres, already axis-converted
        orientation   rotation about Y, 4096 units per turn (optional)
        type          0 for a player spawn, 1..9 for a fixed NPC (optional)
        flags         the AVPGENER flag word (optional; defaults to a spawn)
        name          generator name (optional; generated when absent)
        texture_id, sub_type, extra1, extra2   (optional)

    Names must be unique within a level: the engine matches an `AVPGENEX`
    extra-data chunk to its generator by comparing this string, so a duplicate
    would attach the wrong data — or none. Missing names get `gen1`, `gen2`…
    and any collision is resolved by appending a counter, because silently
    writing a duplicate would be worse than a slightly different name.
    """
    if not spawns:
        return []

    chunks = []
    used = set()
    for index, spec in enumerate(spawns, start=1):
        # Distinguish "no name given" from "explicitly empty": one generator in
        # Compound.RIF has an empty name, and rewriting it would change data the
        # engine is happy with. Only a missing name is generated.
        raw_name = spec.get("name")
        name = f"gen{index}" if raw_name is None else str(raw_name)
        if name in used:
            suffix = 2
            while f"{name}_{suffix}" in used:
                suffix += 1
            name = f"{name}_{suffix}"
        used.add(name)
        # Record what was actually written: `_build_generator_extras` links the
        # extra data to this exact string, so a deduplicated name has to travel
        # with the spec or the two halves would disagree.
        spec["_resolved_name"] = name

        flags = spec.get("flags")
        if flags is None:
            flags = generator_flags(multiplayer_start=True)
        chunks.append(AVPGeneratorChunk(
            name=name,
            location=tuple(spec.get("location", (0, 0, 0))),
            orientation=int(spec.get("orientation", 0)),
            npc_type=int(spec.get("type", 0)),
            flags=int(flags),
            texture_id=int(spec.get("texture_id", 0)),
            sub_type=int(spec.get("sub_type", 0)),
            extra1=int(spec.get("extra1", 0)),
            extra2=int(spec.get("extra2", 0)),
        ))
    return chunks


def _build_lightset(name, lights, ambient, include_litscale,
                    runtime_flags=False):
    """Assemble a LIGHTSET chunk.

    Emits only the lights actually passed in. There is deliberately no
    fallback light: an unwanted white light at the origin would light
    characters (they read exactly these chunks) and silently invalidate any
    lighting test. An empty LIGHTSET is still written, because AMBIENCE has
    to reach GlobalAmbience for dynamic objects.

    Note: STDLIGHT entries are not inert. Objsetup.cpp:3425
    SetUpRunTimeLights() turns every STDLIGHT whose local_light_flags has
    LOFlag_Runtime (0x10) and whose position falls inside a module into a real
    LIGHTBLOCK for that module. That is what lights the player and NPCs, since
    only module objects carry ObFlag3_PreLit (comp_map.c:457) and are skipped
    by Avpview.c:170.
    """
    children = [LightSetHeaderChunk(name)]
    if include_litscale:
        children.append(LightScaleChunk())
    children.append(AmbienceChunk(ambient))
    for i, spec in enumerate(lights or ()):
        children.append(_make_stdlight(i, spec, runtime_flags=runtime_flags))
    return LightSetChunk(children)


def _make_stdlight(index, spec, runtime_flags=False):
    """Build one STDLIGHT record from a light spec dict.

    The spec values are all in engine units, produced once by the operator's
    `_collect_scene_lights` so that the STDLIGHT record and the prelight bake
    cannot disagree:
        brightness : 16.16 fixed (ONE_FIXED = 65536 = 1.0)
        spread     : 4096-per-circle (4096 = full 360°)
        range      : RIF millimetres
        colour     : packed 0x00BBGGRR
        location   : RIF millimetres, already axis-converted

    runtime_flags : when True, OR LOFlag_Runtime into local_light_flags so
        SetUpRunTimeLights (Objsetup.cpp:3502) adopts the light.
    """
    local_flags = spec.get("local_flags", 0)
    if runtime_flags:
        local_flags |= LIGHT_LOCAL_FLAG_RUNTIME
    # The record stores i32, so round rather than trusting a caller that passed
    # floats; a float would otherwise raise deep inside struct.pack.
    location = tuple(int(round(v)) for v in spec.get("location", (0, 0, 0)))
    return StandardLightChunk(
        light_number=index,
        location=location,
        orientation=spec.get("orientation", IDENTITY_MATRIX_16_16),
        brightness=spec.get("brightness", ONE_FIXED),
        spread=spec.get("spread", 4096),
        range=spec.get("range", 20000),
        colour=spec.get("colour", 0x00FFFFFF),
        engine_light_flags=spec.get("engine_flags", 0),
        local_light_flags=local_flags,
    )


def _resolve_bitmap_indices(mesh, texture_paths=None):
    """Return per-material-slot RIF bitmap indices.

    Two ways to decide a slot's texture index, and only one may win:

    * `texture_paths` given — it IS the BMPNAMES list, so a slot's index is the
      position of its material's RIM name inside that list. Nothing else may
      influence it. Reading a leftover `rif_bitmap_idx` here would let unrelated
      scene state change the exported bytes: a material that once carried 7
      made polygons reference index 7 while BMPNAMES declared 0..2, which the
      engine cannot resolve and therefore stops drawing.
    * `texture_paths` absent — fall back to the per-material property, then to
      the slot number, which is what `build_rif_bytes`/`build_rif_multi`
      callers rely on.
    """
    if texture_paths:
        index_of = {}
        for position, path in enumerate(texture_paths):
            index_of.setdefault(str(path), position)
        result = []
        for slot_idx, mat in enumerate(mesh.materials):
            name = rim_name_for_material(mat)
            result.append(index_of.get(name, slot_idx) & POLY_BITMAP_MASK)
        return result

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