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
from .chunks.environment_settings import AVPEnvironmentChunk
from .chunks.trigger import DeathVolume
from .chunks.path import PathChunk, PathPoint
from .chunks.strategy import (GeneratorStrategyChunk, STRAT_ENEMY,
                              compose_mission_data)
from .chunks.sound import SoundDirectoryChunk, SoundObjectChunk
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
    ModuleFlagChunk, ObjectModuleDataChunk, module_flags, AdjacentModuleEntryPointsChunk,
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
SHAPE_FLAG_RECENTRED = 0x00002000   # SHPCHUNK.HPP:16, set when the
                                    # vertices have been moved to the
                                    # origin and location carries the
                                    # shift. 4554 of retail's 16653
                                    # shapes carry it.
SHAPE_FLAGS_DEFAULT = SHAPE_FLAG_RECENTRED

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

# `shape.h:625` -- the polygon is drawn as a light flare, and its centre
# becomes the flare's position (`bh_light.c:193`).
POLY_FLAG_LIGHT_CORONA = 0x20000000


def _slot_flags(mesh):
    """Return the polygon flags each material slot asks for."""
    flags = []
    for material in mesh.materials:
        value = POLY_FLAGS
        if material is not None and getattr(
                material, "rif_material_light_corona", False):
            value |= POLY_FLAG_LIGHT_CORONA
        flags.append(value)
    return flags


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
            matrix=_rotation_scale_matrix(obj),
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
                    placed_objects=None,
                    sounds=None,
                    sound_directory=None,
                    paths=None,
                    envir=None,
                    death_volumes=None,
                    mission_objective=None,
                    area_switches=None,
                    multi_switches=None,
                    self_destructs=None,
                    messages=None,
                    particles=None) -> bytes:
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

    sounds : optional list of dicts, one per sound emitter. Keys:
            position     : (x, y, z) in RIF millimetres
            wav_name     : the file, resolved against `sound_directory`
            snd_name     : this level's name for the emitter
            inner_range  : full volume inside this radius, millimetres
            outer_range  : silence outside it
            max_volume   : 0-127
            pitch, flags, probability

    paths : optional list of dicts, one per patrol route. Keys:

            name         : the route's name, for people and the editor; the
                           engine addresses paths by index, not by name
            path_id      : index into the engine's PathArray. Must be dense from
                           zero -- `Projload.cpp:178` sizes that array from the
                           highest id it sees, so a gap wastes memory and
                           suggests the ids are meant to be consecutive
            flags        : PATH_FLAG_*; retail uses BACK_AND_FORTH on 80% and
                           LOOP on none
            points       : list of (module_index, (x, y, z) millimetres), in
                           order. Each module must exist, and the module a
                           generator starts on must be one of them or the engine
                           logs "Error setting up path" and asserts
                           (`Objsetup.cpp:2480`)

    sound_directory : optional string such as "env\\Temple". Written as a
        SOUNDDIR chunk beside SPECLOBJ. Retail's values are the level's own
        folder, and every retail level that places a sound has one, so a level
        with sounds and no directory is a level whose sounds will not resolve.

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

    # A door leaf is a child mesh of a module marked as a door. Its geometry
    # goes into SHPMORPH > SUBSHAPE, not into a shape and module of its own, so
    # it leaves the module list here -- before anything is indexed. file ids,
    # object indices, module origins and the hierarchy all derive from this
    # list, and dropping a mesh later would shift every one of them.
    door_leaves = {}
    for candidate in objects:
        if not getattr(candidate, "rif_module_door", False):
            continue
        for child in candidate.children:
            if child.type == "MESH":
                door_leaves[candidate.name] = child
                break
    if door_leaves:
        leaves = {leaf.name for leaf in door_leaves.values()}
        objects = [obj for obj in objects if obj.name not in leaves]
        if not objects:
            raise ValueError(
                "the scene has only door leaves; a leaf needs its module")

    # Indices of the modules that are doors. Their neighbours list them with a
    # non-zero `branch_no`, and that entry is the only thing that makes the
    # engine treat a module as a door at all (`Projload.cpp:2344`).
    door_peers = {index for index, candidate in enumerate(objects)
                  if getattr(candidate, "rif_module_door", False)}

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
        fragments = _fragments_for(obj)
        shape = _build_shape_chunk(
            obj.data, obj.name, file_id, slot_bitmaps,
            shape_flags=shape_flags,
            # A fragmenting object keeps its vertices where the artist put them:
            # its pieces are offset from the object's origin, and recentring
            # would shift the geometry while the offsets stayed put.
            recentre=fragments is None,
            matrix=_rotation_scale_matrix(obj),
        )
        if fragments is not None:
            shape.children.append(fragments)
        leaf = door_leaves.get(obj.name)
        if leaf is not None:
            _attach_door_morph(shape, obj, leaf, texture_paths)
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
        # A platform lift and a fan are placed objects, not part of the level's
        # shell. It is not a matter of taste: `Projload.cpp:2078` sends an
        # object down `deal_with_placed_object` or `deal_with_module_object`
        # based on this flag, and BOTH `case StratFan` and `case StratPlatLift`
        # live in the former (`Objsetup.cpp:2273`, `:2307`). Exported as a
        # module, the strategy would be written and never dispatched.
        # The object type is the one switch that decides the path, and it is what
        # the panel shows. The switch checks stay as a fallback for scenes built
        # before the enum existed -- without them an old scene would silently
        # export as a module again.
        object_type = getattr(obj, "rif_object_type", "MODULE")
        as_placed = bool(object_type != "MODULE"
                         or getattr(obj, "rif_module_plat_lift", False)
                         or getattr(obj, "rif_module_fan", False)
                         or getattr(obj, "rif_module_track", False)
                         # A fragmenting object is placed as well: 6575 of the
                         # 6582 in the shipped levels are, and only seven are
                         # modules. Its pieces hang off its shape, not the level.
                         or getattr(obj, "rif_fragments", False)
                         # A lamp is a placed object too, and it must carry its
                         # strategy: the loader takes the hit points from it.
                         or getattr(obj, "rif_placed_light", False))
        if include_module_data and not as_placed:
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
                    i, _linked_peers(i, parents, door_peers), door_peers),
                "adjmdlep": modules.build_adjmdlep(
                    module_neighbours.get(i, []), module_origins),
                "modacous": (-1, -1.0, 0),
                "waypoints": (waypoints or {}).get(i),
                # MODFLAGS, read off the object. Zero means no chunk: 12335 of
                # retail's 12813 modules carry none.
                # A door is a module with a strategy, not a kind of its
                # own: `Objsetup.cpp` reads the strategy from OBJPRJDT and
                # moves the shape the module already has.
                "door": (None if not getattr(obj, "rif_module_door", False)
                         else _build_door_strategy(obj)),
                # A track is the other module strategy that moves geometry.
                # Both at once would leave the engine with two behaviours on
                # one module, which no retail level does.
                "track": (None if not getattr(obj, "rif_module_track", False)
                          else _build_track_strategy_chunk(obj)),
                "module_flags": module_flags(
                    airduct=bool(getattr(obj, "rif_module_airduct", False)),
                    stairs=bool(getattr(obj, "rif_module_stairs", False)),
                    sky=bool(getattr(obj, "rif_module_sky", False)),
                    fog=bool(getattr(obj, "rif_module_fog", False)),
                ),
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
            # Placed even without a strategy: a fragmenting object has none.
            placed=as_placed,
            # A lamp, a lift, a fan and a track have no module data, so their
            # strategy comes straight from the object. Written as an if-chain
            # rather than an `or` chain of conditionals: `A or B if C else D`
            # parses as `(A or B) if C else D`, which silently skipped the lamp.
            strategy=_placed_object_strategy(obj)
                     or (module_data or {}).get("door"),
            track=_track_for(obj),
            light=_placed_light_for(obj),
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
    for entry in (sounds or []):
        speclobj_children.append(SoundObjectChunk(
            position=entry["position"],
            wav_name=entry.get("wav_name", ""),
            snd_name=entry.get("snd_name", ""),
            inner_range=entry.get("inner_range", 5000),
            outer_range=entry.get("outer_range", 20000),
            max_volume=entry.get("max_volume", 127),
            pitch=entry.get("pitch", 0),
            flags=entry.get("flags", 0),
            probability=entry.get("probability", 0),
        ))
    # One AVPPATH2 per route, as siblings of the generators inside SPECLOBJ.
    for entry in (paths or []):
        speclobj_children.append(PathChunk(
            entry["name"],
            entry["path_id"],
            [PathPoint(index, location, entry.get("point_flags", 0))
             for index, location in entry["points"]],
            entry.get("flags", 0x2),
        ))

    # One VIOBJECT per kill volume, as siblings of the generators inside
    # SPECLOBJ -- `Objsetup.cpp:3635` looks for them there.
    for volume in (death_volumes or []):
        speclobj_children.append(
            volume.chunk() if hasattr(volume, "chunk") else volume)

    # The level's ending, written the same way: a VIOBJECT in SPECLOBJ.
    if mission_objective is not None:
        speclobj_children.append(mission_objective.chunk())

    # Emitters, which a switch turns on and off.
    for emitter in (particles or []):
        speclobj_children.append(
            emitter.chunk() if hasattr(emitter, "chunk") else emitter)

    # Messages, which a switch shows with code 2.
    for message in (messages or []):
        speclobj_children.append(message)

    # The countdown, which a switch arms like a volume.
    for target in (self_destructs or []):
        speclobj_children.append(
            target.chunk() if hasattr(target, "chunk") else target)

    # And the triggers, which are what finish an objective or arm a volume.
    for switch in (multi_switches or []):
        speclobj_children.append(
            switch.chunk() if hasattr(switch, "chunk") else switch)

    for switch in (area_switches or []):
        speclobj_children.append(
            switch.chunk() if hasattr(switch, "chunk") else switch)

    env_children.append(SpecialObjectsChunk(speclobj_children))

    # AVPENVIR: the sky and the level's AVP weapon settings. Written from the
    # World, and always present -- retail has one in every level that has an
    # environment chunk at all.

    if sound_directory:
        env_children.append(SoundDirectoryChunk(sound_directory))
    data_chunk = _build_global_generator(global_generator, spawns)
    if data_chunk is not None:
        env_children.append(data_chunk)
    env_children.append(
        _build_lightset(light_set_name, lights, ambient, include_litscale,
                        runtime_flags=emit_runtime_lights)
    )
    if include_avpenvir:
        settings = envir or {}
        env_children.append(AVPEnvironmentSettingsChunk(
            sky_r=settings.get("sky_colour", sky_colour)[0],
            sky_g=settings.get("sky_colour", sky_colour)[1],
            sky_b=settings.get("sky_colour", sky_colour)[2],
            bits=settings.get("bits", 0),
            predator_num_spears=settings.get("predator_num_spears", 30),
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


def _attach_door_morph(shape, module, leaf, texture_paths):
    """Append SHPMORPH for a door leaf to its module's REBSHAPE.

    The leaf's geometry has to end up in the same frame as the module's, or the
    engine draws the sub-shape at the module's origin instead of where the door
    is. The module's shape is recentred, so its local vertices are the world
    position minus `shape.origin_offset`; the leaf gets the same treatment plus
    the distance between the two objects.

    Only the translation is taken from the leaf's transform. A leaf that is also
    rotated would need its normals turned as well, and the panel says so.

    `SUBSHAPE` carries the same geometry chunks as `REBSHAPE`: a sub-shape is a
    shape. Only the header differs, and it holds the counts, the radius and the
    bounds that the shape header also has.
    """
    from .chunks.morph import build_morph_chunk

    slots = _resolve_bitmap_indices(leaf.data, texture_paths)
    verts, vert_normals, polys, poly_normals, uv_lists = \
        _extract_geometry(leaf.data, slots, _rotation_scale_matrix(leaf))
    if not verts or not polys:
        raise ValueError(f"door leaf {leaf.name!r} has no geometry")

    # The engine blends the two shapes VERTEX BY VERTEX: across all 35 morphed
    # modules in retail, module_verts == sub_verts in every one. A leaf with a
    # different count would be blended against unrelated vertices and collapse
    # into a shape nobody authored, which reads in game as a door that is simply
    # too small.
    module_verts = shape.children[0].num_verts
    if len(verts) != module_verts:
        raise ValueError(
            f"door leaf {leaf.name!r} has {len(verts)} vertices but its module "
            f"{module.name!r} has {module_verts}; the engine blends them one "
            f"for one, so the leaf must be a duplicate of the module")

    # Where the leaf sits relative to its module, in RIF millimetres.
    delta = leaf.matrix_world.translation - module.matrix_world.translation
    dx, dy, dz = blender_to_rif_axis(delta.x, delta.y, delta.z)
    shift = (meters_to_rif(dx) - shape.origin_offset[0],
             meters_to_rif(dy) - shape.origin_offset[1],
             meters_to_rif(dz) - shape.origin_offset[2])
    verts = [(x + shift[0], y + shift[1], z + shift[2]) for x, y, z in verts]
    bounds = _compute_bounds(verts)
    centre = _compute_centre(bounds)
    radius = _compute_radius(verts, centre)
    uv_pixels = _uv_to_pixels(uv_lists)

    geometry = [
        ShapeCentreChunk(centre, radius),
        ShapeVertexChunk(verts),
        ShapeVertexNormalChunk(vert_normals),
        ShapePolygonNormalChunk(poly_normals),
        ShapePolygonChunk(polys),
        ShapeUVCoordChunk(uv_pixels),
    ]
    shape.children.append(build_morph_chunk(
        geometry,
        num_verts=len(verts),
        num_polys=len(polys),
        radius=radius,
        max_xyz=bounds["max"],
        min_xyz=bounds["min"],
    ))


def _rotation_scale_matrix(obj):
    """Return the object's rotation and scale, with translation removed.

    The shape needs the transform so a scaled or rotated object draws what the
    module bounds describe. The translation is left out on purpose: the
    object's `location` already carries it, and applying it twice would
    place the geometry at twice the distance from the origin.
    """
    matrix = obj.matrix_world.copy()
    matrix.translation = (0.0, 0.0, 0.0)
    return matrix


def _build_shape_chunk(mesh, object_name, file_id_num, slot_bitmaps,
                       shape_flags=None, recentre=False, matrix=None,
                       slot_flags=None):
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
        _extract_geometry(mesh, slot_bitmaps, matrix,
                          slot_flags if slot_flags is not None
                          else _slot_flags(mesh))
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


def _placed_light_strategy(obj):
    """Return the NewSimpleObject strategy a lamp needs, or None."""
    if not getattr(obj, "rif_placed_light", False):
        return None

    from .chunks.strategy import AVPStrategyChunk

    # `integrity` is the lamp's hit points, one byte in the loader.
    return AVPStrategyChunk(
        type_id=0,
        integrity=int(getattr(obj, "rif_light_integrity", 1)) & 0xFF,
        mass=int(getattr(obj, "rif_light_mass", 10)),
    )


def _placed_object_strategy(obj):
    """Return the strategy for a placed object, or None.

    One object carries at most one: a lamp, a lift, a fan and a track are
    different things, and the panel's type keeps them from overlapping.
    """
    strategy = _placed_light_strategy(obj)
    if strategy is not None:
        return strategy
    if getattr(obj, "rif_module_plat_lift", False):
        return _build_plat_lift_strategy_chunk(obj)
    if getattr(obj, "rif_module_fan", False):
        return _build_fan_strategy_chunk(obj)
    if getattr(obj, "rif_module_track", False):
        return _build_track_strategy_chunk(obj)
    return None


def _placed_light_for(obj):
    """Return the PLOBJLIT chunk for an object, or None.

    The light comes out of the object's origin (`Objsetup.cpp:1734`), so no
    position is stored here -- the lamp is the object, and moving the object
    moves the light.
    """
    if not getattr(obj, "rif_placed_light", False):
        return None

    from .chunks.light import PlacedLightChunk, pack_colour

    up = getattr(obj, "rif_light_colour", (1.0, 1.0, 1.0))
    down = getattr(obj, "rif_light_off_colour", (0.0, 0.0, 0.0))
    flags = 0
    if getattr(obj, "rif_light_on_at_start", True):
        flags |= 0x1
    return PlacedLightChunk(
        brightness=getattr(obj, "rif_light_brightness", 1.0),
        range_mm=int(round(getattr(obj, "rif_light_range", 10.0) * 1000.0)),
        up_colour=pack_colour(*(int(round(c * 255)) for c in up)),
        down_colour=pack_colour(*(int(round(c * 255)) for c in down)),
        fade_up_ms=getattr(obj, "rif_light_fade_up_ms", 0),
        fade_down_ms=getattr(obj, "rif_light_fade_down_ms", 0),
        up_ms=getattr(obj, "rif_light_on_ms", 0),
        down_ms=getattr(obj, "rif_light_off_ms", 0),
        start_ms=getattr(obj, "rif_light_start_ms", 0),
        light_type=int(getattr(obj, "rif_light_type", "0")),
        on_off_type=int(getattr(obj, "rif_light_on_off_type", "0")),
        flags=flags,
    )


def _fragments_for(obj):
    """Return the SHPFRAGS chunk for an object, or None.

    Each mesh child becomes a piece. Its position relative to the parent is
    the offset the engine uses as the scatter direction (`Bh_debri.c:800`),
    so a piece modelled to the left flies left. A child that is not a mesh is
    skipped: an empty has no geometry to break into.
    """
    if not getattr(obj, "rif_fragments", False):
        return None

    from .chunks.fragments import build_fragments_chunk, build_piece

    pieces = []
    for child in obj.children:
        mesh = getattr(child, "data", None)
        if mesh is None or getattr(mesh, "vertices", None) is None:
            continue

        verts, _normals, polys, _pn, _uv = _extract_geometry(
            mesh, _resolve_bitmap_indices(mesh),
            _rotation_scale_matrix(child))
        if not verts:
            continue
        bounds = _compute_bounds(verts)
        centre = _compute_centre(bounds)
        radius = _compute_radius(verts, centre)

        # Where the piece sits relative to the parent, in RIF millimetres.
        delta = (child.matrix_world.translation
                 - obj.matrix_world.translation)
        dx, dy, dz = blender_to_rif_axis(delta.x, delta.y, delta.z)
        location = (meters_to_rif(dx), meters_to_rif(dy), meters_to_rif(dz))

        pieces.append(build_piece(
            name=child.name,
            file_id_num=len(pieces) + 1,
            num_verts=len(verts),
            num_polys=len(polys),
            radius=radius,
            maximum=bounds["max"],
            minimum=bounds["min"],
            location=location,
            copies=getattr(child, "rif_fragment_copies", 1),
        ))

    if not pieces:
        return None
    return build_fragments_chunk(pieces)


def _track_for(obj):
    """Return an OBJTRAK2 for this object, or None when it has no track.

    The object marks the start of the track and a child empty marks the end,
    which is the one-section case that 415 of retail's 663 tracks use. With no
    empty the run is zero, so the track stands where it is -- what 481 of the
    1312 sections do, and what a fan needs: its direction comes from the
    rotation, not from the distance.
    """
    if not getattr(obj, "rif_track_enabled", False):
        return None

    from .chunks.track import (
        TRACK_FLAG_LOOP, TRACK_FLAG_LOOP_BACK_AND_FORTH,
        TRACK_FLAG_PLAYING_AT_START, TRACK_FLAG_USE_TRACK_SMOOTHING,
        TrackSection, build_track_chunk, quaternion_payload,
    )

    start = obj.matrix_world.translation
    end = None
    marker = getattr(obj, "rif_track_end", None)
    if marker is not None:
        # A deleted empty leaves a pointer that still resolves for a moment, so
        # the name is checked against the data rather than trusted.
        end = marker.matrix_world.translation

    def to_rif(vector):
        """Convert a Blender position to RIF millimetres."""
        rx, ry, rz = blender_to_rif_axis(vector.x, vector.y, vector.z)
        return (meters_to_rif(rx), meters_to_rif(ry), meters_to_rif(rz))

    pivot_start = to_rif(start)
    pivot_end = to_rif(end) if end is not None else pivot_start

    offset = getattr(obj, "rif_track_object_offset", (0, 0, 0))
    flags = 0
    if getattr(obj, "rif_track_loop", False):
        flags |= TRACK_FLAG_LOOP
    if getattr(obj, "rif_track_playing_at_start", False):
        flags |= TRACK_FLAG_PLAYING_AT_START
    if getattr(obj, "rif_track_loop_back_forth", False):
        flags |= TRACK_FLAG_LOOP_BACK_AND_FORTH
    if getattr(obj, "rif_track_smoothing", False):
        flags |= TRACK_FLAG_USE_TRACK_SMOOTHING

    section = TrackSection(
        pivot_start=pivot_start,
        pivot_end=pivot_end,
        object_offset=(int(offset[0]), int(offset[1]), int(offset[2])),
        seconds=getattr(obj, "rif_track_seconds", 1.0),
        quat_start=quaternion_payload(obj.matrix_world),
    )
    return build_track_chunk([section], flags=flags)


def _build_object_chunk(name, location_rif, orientation_rif,
                        index_num, shape_id_no, num_vertices=0,
                        module_data=None, prelight_intensities=None,
                        strategy=None, track=None, placed=None,
                        light=None):
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

    track : ObjectTrackChunk or None. When provided, an OBJTRAK2 child is
        emitted. Four strategies read it -- Track (72), TrackDestruct (80),
        Fan (75) and PlatLift (54) -- and it belongs to the object rather than
        to the strategy, so it is emitted for modules and placed objects alike.
    """
    # PLACED_OBJECT_FLAGS is what makes the engine treat an object as a placed
    # object instead of a module (Projload.cpp: the header flag decides which
    # branch runs). A door is a MODULE that happens to carry a strategy, so the
    # flag must follow module_data, not strategy: keying it on the strategy
    # exported every door as a placed object, and the engine then never
    # registered it as a module -- no geometry, no collision, no navigation.
    obj_header = ObjectHeaderChunk(
        flags=(PLACED_OBJECT_FLAGS if placed
               else OBJ_FLAGS if module_data is not None
               else PLACED_OBJECT_FLAGS if strategy is not None
               else OBJ_FLAGS),
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
        # A dict is the SimpleObject record a placed object uses; a
        # finished chunk is a strategy of its own, like a door.
        if hasattr(strategy, "serialize"):
            children.append(ObjectProjectDataChunk([strategy]))
        else:
            children.append(ObjectProjectDataChunk(
                [AVPStrategyChunk(**strategy)]))
    if light is not None:
        # A placed light belongs to the object: it lives and dies with it, which
        # is why shooting the lamp makes the light go out.
        children.append(light)
    if track is not None:
        # Read by the strategy's behaviour, not by the strategy itself, so it
        # sits on the object next to MODULEDT rather than inside AVPSTRAT.
        children.append(track)
    if prelight_intensities:
        children.append(ShapeVertexIntensitiesChunk(
            light_set_name=DEFAULT_LIGHT_SET,
            num_vertices=len(prelight_intensities),
            intensities=prelight_intensities,
        ))
    return ObjectChunk(children)


def _linked_peers(index, parents, door_peers):
    """Return the modules visible from `index`, with doors joined to siblings.

    Parenting declares two spaces connected, and that is enough for rooms.
    A door is different: it sits between spaces and has a single parent, so
    the room on its other side is a sibling and would never list it -- the
    door then exists from one side only. A door is therefore linked to its
    parent AND its siblings, and a sibling lists the door back.
    """
    from . import modules

    peers = modules.linked_modules(index, parents)
    parent = parents[index]

    # Siblings: modules sharing this one's parent.
    siblings = [other for other, other_parent in enumerate(parents)
                if other != index and other_parent is not None
                and other_parent == parent]

    if index in door_peers:
        # A door joins the spaces around it.
        peers = peers + siblings
    else:
        # And a room lists the doorways it can reach through its parent.
        peers = peers + [other for other in siblings if other in door_peers]

    # The parent of a door is a room the door joins; keep it on both sides.
    return list(dict.fromkeys(peers))


def _build_fan_strategy_chunk(obj):
    """Return the AVPSTRAT for a `Fan` strategy on this object."""
    from .chunks.fan import FanStrategyChunk

    return FanStrategyChunk(
        speed_up_seconds=getattr(obj, "rif_fan_speed_seconds", 10.0),
        slow_down_seconds=getattr(obj, "rif_fan_slow_seconds", 10.0),
        # The property is a fraction of full; the file holds per mille.
        strength=int(round(
            getattr(obj, "rif_fan_strength", 1.0) * 100.0)),
    )


def _build_plat_lift_strategy_chunk(obj):
    """Return the AVPSTRAT for a `PlatLift` strategy on this object."""
    from .chunks.plat_lift import (
        PLAT_LIFT_FLAG_DISABLED, PLAT_LIFT_FLAG_ONE_USE,
        PlatLiftStrategyChunk,
    )

    flags = 0
    if getattr(obj, "rif_plat_lift_disabled", False):
        flags |= PLAT_LIFT_FLAG_DISABLED
    if getattr(obj, "rif_plat_lift_one_use", False):
        flags |= PLAT_LIFT_FLAG_ONE_USE
    # All 74 retail lifts leave the five spare words at zero.
    return PlatLiftStrategyChunk(flags=flags)


def _build_track_strategy_chunk(obj):
    """Return the AVPSTRAT for a `Track` strategy on this module."""
    from .chunks.track import build_track_strategy

    return build_track_strategy()


def _build_door_strategy(obj):
    """Return the DoorStrategyChunk for a module marked as a door."""
    from .chunks.door import (
        DoorStrategyChunk, DOOR_FLAG_HORIZONTAL, DOOR_FLAG_LIFT,
        DOOR_FLAG_LOCKED, DOOR_FLAG_OPEN, DOOR_FLAG_PROXIMITY,
    )

    flags = 0
    if getattr(obj, "rif_module_door_locked", True):
        flags |= DOOR_FLAG_LOCKED
    if getattr(obj, "rif_module_door_proximity", True):
        flags |= DOOR_FLAG_PROXIMITY
    if getattr(obj, "rif_module_door_open", False):
        flags |= DOOR_FLAG_OPEN
    if getattr(obj, "rif_module_door_lift", False):
        flags |= DOOR_FLAG_LIFT
    if getattr(obj, "rif_module_door_horizontal", False):
        flags |= DOOR_FLAG_HORIZONTAL
    return DoorStrategyChunk(
        door_flags=flags,
        open_seconds=float(getattr(obj, "rif_module_door_open_seconds", 1.0)),
        close_seconds=float(getattr(obj, "rif_module_door_close_seconds", 0.5)),
    )


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
    # Only when a flag is set: 12335 of retail's 12813 modules carry no
    # MODFLAGS chunk, so writing a zero one would change every level that is
    # currently identical to the shipping data.
    flags = spec.get("module_flags", 0)
    if flags:
        children.append(ModuleFlagChunk(flags))
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
        mission = int(spec.get("mission", 0))
        # A generator needs its extra chunk when it has weights OR a mission.
        # The two travel in the same chunk, so a patrol is written through the
        # same object rather than a second one under the same name.
        if weights is None and not mission:
            continue
        # The generator name must match the spec's exactly, including the
        # counter `_build_generators` may have appended, so it is resolved the
        # same way here rather than assumed.
        children = []
        if weights is not None:
            children.append(GeneratorExSettingsChunk(
                generation_rate=int(spec.get("generation_rate", 0)),
                gen_rate_increase=int(spec.get("gen_rate_increase", 0)),
                gen_limit=int(spec.get("gen_limit", 0)),
                weights=weights))
        if mission:
            children.append(GeneratorStrategyChunk(
                mission=mission,
                mission_data=compose_mission_data(
                    spec.get("path_id", 0), spec.get("start_object_index", 0)),
                strategy_type=int(spec.get("strategy_type", STRAT_ENEMY))))
        extras.append(AVPGeneratorExtraChunk(
            name=spec.get("_resolved_name") or spec.get("name")
            or f"gen{index}",
            settings=children[0] if len(children) == 1 else None,
            children=children if len(children) > 1 else None,
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


def _extract_geometry(mesh, slot_bitmaps, matrix=None, slot_flags=None):
    """Return (verts_int, vert_normals, polys, poly_normals, uv_lists).

    `matrix` is the object's rotation and scale, without translation. It must be
    applied here or the geometry ignores a scaled or rotated object while the
    module bounds apply it -- the collision volume and the drawn shape then
    disagree, and a door is walk-through while still triggering from inside the
    volume nobody can see.
    """
    rotation = None
    if matrix is not None:
        rotation = matrix.to_3x3()

    verts_int = []
    vert_normals = []
    for vert in mesh.vertices:
        co = vert.co
        if rotation is not None:
            co = rotation @ co
        bx, by, bz = blender_to_rif_axis(co.x, co.y, co.z)
        verts_int.append((
            meters_to_rif(bx),
            meters_to_rif(by),
            meters_to_rif(bz),
        ))
        n = vert.normal
        if rotation is not None:
            n = (rotation @ n).normalized()
        vert_normals.append(blender_to_rif_axis(n.x, n.y, n.z))

    uv_layer = mesh.uv_layers.active
    polys = []
    poly_normals = []
    uv_lists = []

    poly_index = 0
    for poly in mesh.polygons:
        indices = list(poly.vertices)
        n = poly.normal
        if rotation is not None:
            n = (rotation @ n).normalized()
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
                polys.append(_make_poly_record(
            tri, poly_index, bitmap_idx,
            slot_flags[slot] if slot_flags and slot < len(slot_flags)
            else None))
                poly_normals.append(face_normal)
                uv_lists.append(tri_uvs)
                poly_index += 1
        else:
            polys.append(_make_poly_record(
                indices, poly_index, bitmap_idx,
                slot_flags[slot] if slot_flags and slot < len(slot_flags)
                else None))
            poly_normals.append(face_normal)
            uv_lists.append(loop_uvs)
            poly_index += 1

    return verts_int, vert_normals, polys, poly_normals, uv_lists


def _make_poly_record(vert_indices, poly_index, bitmap_idx=0, flags=None):
    """Build a polygon record dict, padding vertex list with -1.

    `flags` is `POLY_FLAGS` plus whatever the surface asked for. A lamp's flare
    is one of those bits: `iflag_light_corona` (`shape.h:625`) marks the polygons
    the engine draws as a billboard, and it lives on the polygon rather than the
    shape so one mesh can be part housing and part glow.
    """
    count = len(vert_indices)
    if count < 3 or count > 4:
        raise ValueError(f"Polygon must have 3 or 4 verts, got {count}")
    padded = list(vert_indices) + [-1] * (5 - count)
    colour = ((poly_index & 0xFFFF) << 16) | (bitmap_idx & POLY_BITMAP_MASK)
    return {
        "engine_type": POLY_ENGINE_TYPE,
        "normal_index": poly_index,
        "flags": POLY_FLAGS if flags is None else int(flags),
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