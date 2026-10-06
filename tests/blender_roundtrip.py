"""Blender roundtrip tests for RIF exporter.

Run inside Blender:
    blender --background --python tests/blender_roundtrip.py
Or from the Text Editor: Run Script.

57 tests covering S1-S6 F13: single mesh, calibration, UV, bitmap indices,
axis conversion, quaternion, multi-object, hierarchy, level skeleton,
LIGHTSET, AVPENVIR, prelight flags/SHPVTINT, point-light and spot baking,
the World-to-ambient mapping, and the absence of a fabricated fallback light.
"""
import os
import struct
import sys
import time
import traceback


def _locate_rif_package():
    """Make `import rif` resolve regardless of how this script is launched.

    Tries, in order:
      1. Plain import — works when cwd or sys.path already has rif_exporter/.
      2. __file__-relative — works with `blender --background --python`.
      3. Installed addon lookup via addon_utils — works from the Text Editor
         when the extension is installed and enabled (the normal workflow).
      4. Blender's user/system addon script paths.

    Returns the directory that was added to sys.path, or None on failure.
    """
    # 1. Already importable?
    try:
        import rif  # noqa: F401
        return "(already on sys.path)"
    except ImportError:
        pass

    # 2. __file__-relative (real filesystem script).
    here = None
    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        here = None
    if here and os.path.basename(here) == "tests":
        root = os.path.dirname(here)
        if os.path.isdir(os.path.join(root, "rif")):
            if root not in sys.path:
                sys.path.insert(0, root)
            return root

    # 3. Installed addon lookup (Blender Text Editor workflow).
    try:
        import addon_utils
        for mod in addon_utils.modules():
            short = mod.__name__.split(".")[-1].lower()
            if short != "rif_exporter":
                continue
            pkg_dir = os.path.dirname(os.path.abspath(mod.__file__))
            if os.path.isdir(os.path.join(pkg_dir, "rif")):
                if pkg_dir not in sys.path:
                    sys.path.insert(0, pkg_dir)
                return pkg_dir
    except Exception:
        pass

    # 4. Fallback: scan Blender's addon script paths.
    try:
        import bpy
        for base in bpy.utils.script_paths(subdir="addons"):
            cand = os.path.join(base, "rif_exporter")
            if os.path.isdir(os.path.join(cand, "rif")):
                if cand not in sys.path:
                    sys.path.insert(0, cand)
                return cand
    except Exception:
        pass

    return None


_RIF_PATH = _locate_rif_package()
if _RIF_PATH is None:
    print("=" * 72)
    print("FATAL: could not locate the 'rif' package.")
    print("Install and enable the RIF Exporter extension first, or run this")
    print("script from the command line with:")
    print("  blender --background --python tests/blender_roundtrip.py")
    print("=" * 72)
    sys.exit(1)

try:
    import bpy
    from mathutils import Quaternion
    HAS_BPY = True
except ImportError:
    bpy = None
    Quaternion = None
    HAS_BPY = False

from rif.builder import (
    build_rif_bytes, build_rif_multi, build_rif_level,
    _convert_orientation,
    POLY_BITMAP_MASK, POLY_UV_HI_MASK,
    BITMAP_IDX_PROP,
)
from rif.types import (
    blender_to_rif_axis, rif_to_blender_axis,
    meters_to_rif, uv_to_rif_pixels,
    RIF_UNITS_PER_METER, DEFAULT_UV_PIXELS,
)
from rif import lighting as rif_lighting


# ------------------------------------------------------------------ framework

class SkipTest(Exception):
    """Raised by tests that cannot run in the current environment."""


_TESTS = []


def test(fn):
    """Register a function as a test case."""
    _TESTS.append(fn)
    return fn


# ------------------------------------------------------------------ helpers

def _clear_scene():
    """Remove all objects and mesh/material data from the current scene."""
    if bpy is None:
        return
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)


def _make_cube(name="Cube", size=1.0, location=(0.0, 0.0, 0.0)):
    """Create a cube mesh object; return the object."""
    _clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def _make_cubes(count, prefix="Cube"):
    """Create N cubes at spaced X locations; return them in order."""
    _clear_scene()
    objs = []
    for i in range(count):
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(i * 2.0, 0, 0))
        obj = bpy.context.active_object
        obj.name = f"{prefix}{i}"
        objs.append(obj)
    return objs


def _make_plate(name="Plate", dims=(2.0, 0.1, 0.3)):
    """Create an asymmetric plate at origin; return the object."""
    _clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = dims
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return obj


def _find_chunk(data, ident):
    """Return (index, total_size) for the first occurrence of ident."""
    marker = ident.encode("ascii").ljust(8, b"\0")
    idx = data.find(marker)
    if idx < 0:
        return -1, 0
    size = struct.unpack_from("<I", data, idx + 8)[0]
    return idx, size


def _find_all_chunks(data, ident):
    """Return list of (index, total_size) for every occurrence of ident."""
    marker = ident.encode("ascii").ljust(8, b"\0")
    results = []
    pos = 0
    while True:
        idx = data.find(marker, pos)
        if idx < 0:
            break
        size = struct.unpack_from("<I", data, idx + 8)[0]
        results.append((idx, size))
        pos = idx + 8
    return results


def _chunk_payload(data, ident):
    """Return payload bytes of the first matching chunk."""
    idx, size = _find_chunk(data, ident)
    if idx < 0:
        return b""
    return data[idx + 12:idx + size]


def _read_i32(data, off):
    return struct.unpack_from("<i", data, off)[0]


def _read_u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def _read_f32(data, off):
    return struct.unpack_from("<f", data, off)[0]


def _read_i32x3(data, off):
    return struct.unpack_from("<3i", data, off)


def _shphead1_bounds(payload):
    """Extract (max_xyz, min_xyz) from SHPHEAD1 payload."""
    max_xyz = (_read_i32(payload, 36), _read_i32(payload, 44),
               _read_i32(payload, 52))
    min_xyz = (_read_i32(payload, 40), _read_i32(payload, 48),
               _read_i32(payload, 56))
    return max_xyz, min_xyz


def _objhead1_location(payload):
    """Extract (x, y, z) location from OBJHEAD1 payload (offset 20)."""
    return _read_i32x3(payload, 20)


def _decode_poly_colour(colour):
    """Reproduce the community importer's uv_idx decoder."""
    if colour & POLY_UV_HI_MASK:
        return ((colour & POLY_UV_HI_MASK) << 4) | (colour >> 16)
    return colour >> 16


def _iter_poly_records(shppolys_payload):
    """Yield (engine, normal_idx, flags, colour, vert_ind) per 36-byte record."""
    count = len(shppolys_payload) // 36
    for i in range(count):
        off = i * 36
        yield (
            _read_i32(shppolys_payload, off),
            _read_i32(shppolys_payload, off + 4),
            _read_i32(shppolys_payload, off + 8),
            _read_u32(shppolys_payload, off + 12),
            struct.unpack_from("<5i", shppolys_payload, off + 16),
        )


def _iter_uv_records(uv_payload):
    """Yield list of (u, v) lists — one per poly."""
    num = _read_u32(uv_payload, 0)
    off = 4
    for _ in range(num):
        count = _read_u32(uv_payload, off)
        off += 4
        uvs = []
        for _ in range(count):
            u = _read_f32(uv_payload, off)
            v = _read_f32(uv_payload, off + 4)
            uvs.append((u, v))
            off += 8
        yield uvs


# ------------------------------------------------------------------ tests S1-S4

@test
def test_buffer_starts_with_rebinff2():
    """Output buffer begins with the REBINFF2 root identifier."""
    obj = _make_cube()
    data = build_rif_bytes(obj.data, object_name="Cube")
    assert data.startswith(b"REBINFF2"), \
        f"expected REBINFF2, got {data[:8]!r}"
    size = _read_u32(data, 8)
    assert size == len(data), f"root size {size} != buffer len {len(data)}"


@test
def test_shphead1_bounds_match_metres_to_rif():
    """Bounds in SHPHEAD1 match meters_to_rif of Blender half-extents."""
    obj = _make_cube(size=1.0)
    data = build_rif_bytes(obj.data, object_name="Cube")
    payload = _chunk_payload(data, "SHPHEAD1")
    assert payload, "SHPHEAD1 not found"
    max_xyz, min_xyz = _shphead1_bounds(payload)
    # Cube size=1 → half extent 0.5 m → 500 mm.
    assert max_xyz == (500, 500, 500), f"max_xyz={max_xyz}"
    assert min_xyz == (-500, -500, -500), f"min_xyz={min_xyz}"
    assert meters_to_rif(0.5) == 500
    assert RIF_UNITS_PER_METER == 1000.0


@test
def test_shphead1_radius_matches_bounds_diagonal():
    """SHPHEAD1 radius equals half-space diagonal of the AABB."""
    obj = _make_cube(size=1.0)
    data = build_rif_bytes(obj.data, object_name="Cube")
    payload = _chunk_payload(data, "SHPHEAD1")
    radius = _read_f32(payload, 32)
    expected = 500.0 * (3 ** 0.5)
    assert abs(radius - expected) < 1.0, \
        f"radius {radius} != expected {expected}"


@test
def test_shphead1_counts_match_mesh():
    """SHPHEAD1 num_verts / num_polys match the Blender mesh."""
    obj = _make_cube()
    data = build_rif_bytes(obj.data, object_name="Cube")
    payload = _chunk_payload(data, "SHPHEAD1")
    num_verts = _read_i32(payload, 24)
    num_polys = _read_i32(payload, 28)
    assert num_verts == len(obj.data.vertices)
    assert num_polys == len(obj.data.polygons)


@test
def test_uv_max_under_pixel_span():
    """All UVs in SHPUVCRD are within [0, DEFAULT_UV_PIXELS]."""
    obj = _make_cube()
    data = build_rif_bytes(obj.data, object_name="Cube")
    uv_payload = _chunk_payload(data, "SHPUVCRD")
    assert uv_payload, "SHPUVCRD not found"
    umax = 0.0
    vmax = 0.0
    for uvs in _iter_uv_records(uv_payload):
        for u, v in uvs:
            umax = max(umax, u)
            vmax = max(vmax, v)
    assert umax <= DEFAULT_UV_PIXELS + 1e-4, f"umax={umax}"
    assert vmax <= DEFAULT_UV_PIXELS + 1e-4, f"vmax={vmax}"


@test
def test_poly_colour_encodes_uv_index():
    """Decoder yields uv_idx == poly_idx for every SHPPOLYS record."""
    obj = _make_cube()
    data = build_rif_bytes(obj.data, object_name="Cube")
    payload = _chunk_payload(data, "SHPPOLYS")
    for i, (_eng, _n, _f, colour, _vi) in enumerate(_iter_poly_records(payload)):
        uv_idx = _decode_poly_colour(colour)
        assert uv_idx == i, f"poly {i}: decoder returned {uv_idx}"


@test
def test_uv_roundtrip_is_lossless_at_edges():
    """uv_to_rif_pixels is invertible at u=0 and u=1."""
    for u in (0.0, 1.0):
        u_pix, _v_pix = uv_to_rif_pixels(u, 0.0)
        u_back = u_pix / DEFAULT_UV_PIXELS
        assert abs(u_back - u) < 1e-6, f"u={u} → {u_back}"


@test
def test_bitmap_idx_defaults_to_slot_index():
    """Two material slots → bitmap_idx 0 and 1 in poly records."""
    obj = _make_cube()
    mat0 = bpy.data.materials.new(name="Mat0")
    mat1 = bpy.data.materials.new(name="Mat1")
    obj.data.materials.append(mat0)
    obj.data.materials.append(mat1)
    for i, poly in enumerate(obj.data.polygons):
        poly.material_index = i % 2

    data = build_rif_bytes(obj.data, object_name="Cube")
    payload = _chunk_payload(data, "SHPPOLYS")
    seen = set()
    for i, (_eng, _n, _f, colour, _vi) in enumerate(_iter_poly_records(payload)):
        seen.add(colour & POLY_BITMAP_MASK)
    assert seen == {0, 1}, f"expected bitmap_idx in {{0,1}}, got {seen}"


@test
def test_bitmap_idx_custom_property_override():
    """material['rif_bitmap_idx'] overrides the default slot index."""
    obj = _make_cube()
    mat0 = bpy.data.materials.new(name="Mat0")
    mat0[BITMAP_IDX_PROP] = 7
    obj.data.materials.append(mat0)
    for poly in obj.data.polygons:
        poly.material_index = 0

    data = build_rif_bytes(obj.data, object_name="Cube")
    payload = _chunk_payload(data, "SHPPOLYS")
    for _eng, _n, _f, colour, _vi in _iter_poly_records(payload):
        assert (colour & POLY_BITMAP_MASK) == 7, \
            f"expected bitmap_idx 7, got {colour & POLY_BITMAP_MASK}"


@test
def test_axis_conversion_is_not_identity():
    """Plate dims (2.0, 0.1, 0.3) survive the Blender→RIF rotation.

    Blender (X, Y, Z) = (2.0, 0.1, 0.3) should map to
    RIF   (X, Y, Z) = (2.0, 0.3, 0.1) — X stays, Y and Z swap.
    """
    obj = _make_plate(dims=(2.0, 0.1, 0.3))
    data = build_rif_bytes(obj.data, object_name="Plate")
    payload = _chunk_payload(data, "SHPHEAD1")
    max_xyz, min_xyz = _shphead1_bounds(payload)
    x_span = max_xyz[0] - min_xyz[0]
    y_span = max_xyz[1] - min_xyz[1]
    z_span = max_xyz[2] - min_xyz[2]
    assert x_span == 2000, f"x_span={x_span}"
    assert y_span == 300, f"y_span={y_span}"
    assert z_span == 100, f"z_span={z_span}"


@test
def test_axis_roundtrip_via_importer_is_identity():
    """blender_to_rif_axis is inverted exactly by rif_to_blender_axis."""
    for v in ((1.0, 2.0, 3.0), (-4.5, 0.0, 7.25), (0.1, -0.2, 0.3)):
        rif = blender_to_rif_axis(*v)
        back = rif_to_blender_axis(*rif)
        for a, b in zip(v, back):
            assert abs(a - b) < 1e-9, f"{v} → {rif} → {back}"


@test
def test_orientation_identity_preserved():
    """Identity Blender quaternion → identity RIF quaternion (0,0,0,1)."""
    q = Quaternion((1.0, 0.0, 0.0, 0.0))
    x, y, z, w = _convert_orientation(q)
    assert abs(x) < 1e-6 and abs(y) < 1e-6 and abs(z) < 1e-6
    assert abs(w - 1.0) < 1e-6, f"w={w}"


@test
def test_orientation_is_unit_quaternion():
    """Non-trivial Blender quaternion stays unit-length after rotation."""
    q = Quaternion((0.5, 0.5, 0.5, 0.5))
    q.normalize()
    x, y, z, w = _convert_orientation(q)
    mag_sq = x * x + y * y + z * z + w * w
    assert abs(mag_sq - 1.0) < 1e-5, f"|q|^2 = {mag_sq}"


@test
def test_multi_two_cubes_shape_and_object_counts():
    """build_rif_multi emits one REBSHAPE + one RBOBJECT per object."""
    objs = _make_cubes(2)
    data = build_rif_multi(objs)
    assert data.count(b"REBSHAPE") == 2, "expected 2 REBSHAPE chunks"
    assert data.count(b"RBOBJECT") == 2, "expected 2 RBOBJECT chunks"


@test
def test_multi_object_locations_match_transforms():
    """OBJHEAD1.location matches each object's world position."""
    objs = _make_cubes(2)
    objs[1].location = (1.0, 2.0, 3.0)
    bpy.context.view_layer.update()

    data = build_rif_multi(objs)
    objs_chunks = _find_all_chunks(data, "OBJHEAD1")
    assert len(objs_chunks) == 2
    locs = [_objhead1_location(data[i + 12:i + s])
            for i, s in objs_chunks]
    assert locs[0] == (0, 0, 0), f"obj0 loc {locs[0]}"
    assert locs[1] == (1000, -3000, 2000), f"obj1 loc {locs[1]}"


@test
def test_multi_shape_file_ids_are_distinct():
    """SHPHEAD1.file_id_num is 1 and 2 for a two-object export."""
    objs = _make_cubes(2)
    data = build_rif_multi(objs)
    heads = _find_all_chunks(data, "SHPHEAD1")
    assert len(heads) == 2
    ids = [_read_i32(data, i + 12 + 20) for i, _s in heads]
    assert ids == [1, 2], f"file_id_num list = {ids}"


@test
def test_hierarchy_flat_tree_structure():
    """Parent with one child: 3 OBJCHIER, 2 OBJHIERD, 1 OBHIERNM.

    Note the identifier is OBHIERNM (8 chars, no J) — the format only
    allows eight ASCII bytes per chunk id. Verified against sentry.txt.
    """
    objs = _make_cubes(2)
    hierarchy = {objs[1].name: objs[0].name}
    data = build_rif_multi(objs, hierarchy=hierarchy)
    assert data.count(b"OBJCHIER") == 3, \
        f"OBJCHIER count = {data.count(b'OBJCHIER')}"
    assert data.count(b"OBJHIERD") == 2, \
        f"OBJHIERD count = {data.count(b'OBJHIERD')}"
    assert data.count(b"OBHIERNM") == 1, \
        f"OBHIERNM count = {data.count(b'OBHIERNM')}"
    assert b"OBJHIERNM" not in data


@test
def test_hierarchy_deep_chain():
    """Chain A→B→C: 4 OBJCHIER, 3 OBJHIERD."""
    objs = _make_cubes(3)
    hierarchy = {
        objs[1].name: objs[0].name,
        objs[2].name: objs[1].name,
    }
    data = build_rif_multi(objs, hierarchy=hierarchy)
    assert data.count(b"OBJCHIER") == 4
    assert data.count(b"OBJHIERD") == 3


@test
def test_hierarchy_absent_when_none():
    """When hierarchy is None, no OBJCHIER chunk is written."""
    objs = _make_cubes(2)
    data = build_rif_multi(objs, hierarchy=None)
    assert b"OBJCHIER" not in data


@test
def test_roundtrip_via_community_importer():
    """Smoke check: community RIF importer is installed and importable."""
    try:
        import addon_utils
    except ImportError:
        raise SkipTest("addon_utils unavailable")

    importer_name = None
    for mod in addon_utils.modules():
        short = mod.__name__.split(".")[-1].lower()
        if short in ("io_scene_rif", "io_import_rif", "rif_importer"):
            importer_name = mod.__name__
            break

    if importer_name is None:
        raise SkipTest("community RIF importer not installed under a "
                       "recognised module name")

    import importlib
    importer = importlib.import_module(importer_name)
    assert importer is not None


# ------------------------------------------------------------------ tests S5

@test
def test_envdt_present_in_level_mode():
    """build_rif_level output contains REBENVDT on the root."""
    obj = _make_cube()
    data = build_rif_level([obj], "testlevel")
    assert b"REBENVDT" in data, "REBENVDT missing from level RIF"


@test
def test_envdt_riffname_matches():
    """RIFFNAME chunk payload equals the level name passed in."""
    from rif.types import pack_string
    obj = _make_cube()
    data = build_rif_level([obj], "mylevel")
    idx, size = _find_chunk(data, "RIFFNAME")
    assert idx > 0, "RIFFNAME chunk missing"
    payload = data[idx + 12:idx + size]
    assert payload == pack_string("mylevel"), \
        f"payload={payload!r} expected={pack_string('mylevel')!r}"


@test
def test_envdt_bmpnames_count():
    """BMPNAMES header count matches the number of texture paths."""
    obj = _make_cube()
    tex = ["a.RIM", "b.RIM", "c.RIM"]
    data = build_rif_level([obj], "lvl", texture_paths=tex)
    idx, _size = _find_chunk(data, "BMPNAMES")
    assert idx > 0
    header = _read_u32(data, idx + 12)
    count = header & 0xFFFF
    assert count == 3, f"expected 3 BMPNAMES entries, got {count}"


@test
def test_envdt_bmnamver_present():
    """BMNAMVER chunk is present with version = 1 and size = 16."""
    obj = _make_cube()
    data = build_rif_level([obj], "lvl")
    idx, size = _find_chunk(data, "BMNAMVER")
    assert idx > 0, "BMNAMVER missing"
    assert size == 16, f"BMNAMVER size should be 16, got {size}"
    version = _read_u32(data, idx + 12)
    assert version == 1, f"BMNAMVER version {version}"


@test
def test_envdt_bmnamext_size():
    """BMNAMEXT chunk is exactly 64 bytes total (12 header + 52 payload)."""
    obj = _make_cube()
    data = build_rif_level([obj], "lvl")
    idx, size = _find_chunk(data, "BMNAMEXT")
    assert idx > 0
    assert size == 64, f"BMNAMEXT size should be 64, got {size}"


@test
def test_avpstart_written():
    """SPECLOBJ > AVPSTART is emitted with the requested location."""
    obj = _make_cube()
    data = build_rif_level(
        [obj], "lvl",
        player_start=(1.0, 2.0, 3.0),
    )
    assert b"SPECLOBJ" in data, "SPECLOBJ missing when player_start given"
    idx, size = _find_chunk(data, "AVPSTART")
    assert idx > 0, "AVPSTART missing inside SPECLOBJ"
    assert size == 68, f"AVPSTART size should be 68, got {size}"
    # Location 3× i32 at payload offset 0.
    x = _read_i32(data, idx + 12)
    y = _read_i32(data, idx + 16)
    z = _read_i32(data, idx + 20)
    # blender_to_rif_axis(1, 2, 3) = (1, -3, 2); ×1000 → (1000, -3000, 2000).
    assert (x, y, z) == (1000, -3000, 2000), f"got {(x, y, z)}"


# ------------------------------------------------------------------ tests S6

@test
def test_lightset_present_in_level_mode():
    """build_rif_level output contains LIGHTSET on the env tree."""
    obj = _make_cube()
    data = build_rif_level([obj], "lvl")
    assert b"LIGHTSET" in data, "LIGHTSET missing from level RIF"


@test
def test_ltsethdr_name_padded_to_8():
    """LTSETHDR payload is exactly 12 bytes (8-char name + 4-byte pad)."""
    obj = _make_cube()
    data = build_rif_level([obj], "lvl", light_set_name="MYLIGHTS")
    idx, size = _find_chunk(data, "LTSETHDR")
    assert idx > 0, "LTSETHDR missing"
    assert size == 24, f"LTSETHDR size should be 24, got {size}"
    name = data[idx + 12:idx + 20]
    assert name == b"MYLIGHTS", f"name = {name!r}"


@test
def test_ambience_value_roundtrip():
    """AMBIENCE payload reflects the ambient argument."""
    obj = _make_cube()
    data = build_rif_level([obj], "lvl", ambient=200)
    idx, size = _find_chunk(data, "AMBIENCE")
    assert idx > 0, "AMBIENCE missing"
    assert size == 16, f"AMBIENCE size should be 16, got {size}"
    ambience = _read_i32(data, idx + 12)
    assert ambience == 200, f"ambience = {ambience}"


@test
def test_litscale_payload_size():
    """LITSCALE chunk is 52 bytes total (12 header + 40 payload)."""
    obj = _make_cube()
    data = build_rif_level([obj], "lvl", include_litscale=True)
    idx, size = _find_chunk(data, "LITSCALE")
    assert idx > 0, "LITSCALE missing"
    assert size == 52, f"LITSCALE size should be 52, got {size}"


@test
def test_stdlight_payload_layout():
    """One STDLIGHT per light spec, 96 bytes total (12 + 84)."""
    obj = _make_cube()
    lights = [{"location": (1.0, 2.0, 3.0), "brightness": 200}]
    data = build_rif_level([obj], "lvl", lights=lights)
    idx, size = _find_chunk(data, "STDLIGHT")
    assert idx > 0, "STDLIGHT missing"
    assert size == 96, f"STDLIGHT size should be 96, got {size}"
    # location at payload offset +4 (after i32 light_number)
    x = _read_i32(data, idx + 12 + 4)
    y = _read_i32(data, idx + 12 + 8)
    z = _read_i32(data, idx + 12 + 12)
    # blender_to_rif_axis(1, 2, 3) = (1, -3, 2); ×1000 → (1000, -3000, 2000).
    assert (x, y, z) == (1000, -3000, 2000), f"got {(x, y, z)}"
    # brightness at payload offset +52 (4 + 12 + 36)
    brightness = _read_i32(data, idx + 12 + 52)
    assert brightness == 200, f"brightness = {brightness}"


@test
def test_avpenvir_present():
    """AVPENVIR chunk is emitted with the requested sky colour."""
    obj = _make_cube()
    data = build_rif_level(
        [obj], "lvl",
        sky_colour=(64, 96, 128),
    )
    idx, size = _find_chunk(data, "AVPENVIR")
    assert idx > 0, "AVPENVIR missing"
    assert size == 36, f"AVPENVIR size should be 36, got {size}"
    data_size = _read_i32(data, idx + 12)
    assert data_size == 24, f"data_size = {data_size}"
    r = _read_i32(data, idx + 16)
    g = _read_i32(data, idx + 20)
    b = _read_i32(data, idx + 24)
    assert (r, g, b) == (64, 96, 128), f"RGB = {(r, g, b)}"


# ------------------------------------------------------ tests S6 F8 (prelight)

def _shphead1_of(data, shape_index=0):
    """Return the (index, size) of the Nth SHPHEAD1 in the buffer."""
    hits = _find_all_chunks(data, "SHPHEAD1")
    assert len(hits) > shape_index, "SHPHEAD1 not found"
    return hits[shape_index]


@test
def test_prelight_on_keeps_shape_flags_clear():
    """SHPHEAD1.flags must be 0 when prelight is on.

    ShapeFlag_PreLit (shape.h:421) and SHAPE_FLAG_EXTERNALFILE
    (SHPCHUNK.HPP:15) are both 0x00001000. Writing the bit made
    Projload.cpp:1974 treat our level shapes as imported shapes, so no
    module was registered and the level rendered empty. Prelight is
    activated by the SHPVTINT chunk instead (Projload.cpp:1455).
    """
    obj = _make_cube()
    data = build_rif_level([obj], "lvl", include_prelight=True)
    idx, _size = _shphead1_of(data)
    flags = _read_i32(data, idx + 12)
    assert not (flags & 0x00001000), \
        f"SHAPE_FLAG_EXTERNALFILE / ShapeFlag_PreLit must stay clear, " \
        f"flags = 0x{flags:08X}"
    assert not (flags & 0x01), "ShapeFlag_3DS_AxisFlip must not be set"
    assert _find_chunk(data, "SHPVTINT")[0] > 0, \
        "prelight must still travel in a SHPVTINT chunk"


@test
def test_prelight_off_clears_shape_flags():
    """With prelight disabled the shape flags must be exactly 0 (retail)."""
    obj = _make_cube()
    data = build_rif_level([obj], "lvl", include_prelight=False)
    idx, _size = _shphead1_of(data)
    flags = _read_i32(data, idx + 12)
    assert flags == 0, f"flags = 0x{flags:08X}"
    assert _find_chunk(data, "SHPVTINT")[0] < 0, "SHPVTINT must be absent"


@test
def test_exported_flags_never_set_the_external_file_bit():
    """No export path may raise SHAPE_FLAG_EXTERNALFILE (0x1000).

    This is the regression test for the S6 F9 root cause: bit 0x1000 sends
    level shapes down Projload.cpp:1974 (imported shape), where the loader
    uses a stale object pointer and never calls copy_to_module().
    """
    obj = _make_cube()
    for kwargs in ({"include_prelight": True}, {"include_prelight": False}):
        data = build_rif_level([obj], "lvl", **kwargs)
        idx, _size = _shphead1_of(data)
        flags = _read_i32(data, idx + 12)
        assert flags & 0x00001000 == 0, \
            f"flags 0x{flags:08X} raises SHAPE_FLAG_EXTERNALFILE ({kwargs})"
    data = build_rif_bytes(obj.data, object_name=obj.name)
    idx, _size = _find_chunk(data, "SHPHEAD1")
    flags = _read_i32(data, idx + 12)
    assert flags & 0x00001000 == 0, f"single-mesh flags 0x{flags:08X}"


@test
def test_shape_flags_override_zero_is_honoured():
    """shape_flags_override=0 must win over include_prelight.

    This is diagnostic step D1 from HANDOFF_S6_F7. The operator used to
    collapse a 0 override to None (`value or None`), so D1 was untestable.
    """
    obj = _make_cube()
    data = build_rif_level([obj], "lvl", include_prelight=True,
                           shape_flags_override=0)
    idx, _size = _shphead1_of(data)
    flags = _read_i32(data, idx + 12)
    assert flags == 0, f"flags = 0x{flags:08X}"


@test
def test_shpvtint_emitted_by_default():
    """Level mode emits exactly one SHPVTINT per RBOBJECT."""
    objs = _make_cubes(2)
    data = build_rif_level(objs, "lvl", include_prelight=True)
    svic = _find_all_chunks(data, "SHPVTINT")
    robj = _find_all_chunks(data, "RBOBJECT")
    assert len(robj) == 2, f"expected 2 RBOBJECT, got {len(robj)}"
    assert len(svic) == 2, f"expected 2 SHPVTINT, got {len(svic)}"


@test
def test_shpvtint_absent_when_prelight_off():
    """No SHPVTINT is written when prelight is disabled."""
    obj = _make_cube()
    data = build_rif_level([obj], "lvl", include_prelight=False)
    assert _find_chunk(data, "SHPVTINT")[0] < 0, "SHPVTINT must be absent"


@test
def test_shpvtint_num_vertices_matches_shphead1():
    """SHPVTINT.num_vertices equals the linked SHPHEAD1.num_verts.

    VertexIntensity_Standard_Opt indexes Global_EID_IPtr[] by vertex number
    without a bounds check, so a mismatch reads past the array.
    """
    obj = _make_cube()
    data = build_rif_level([obj], "lvl", include_prelight=True)
    hdr_idx, hdr_size = _shphead1_of(data)
    num_verts = _read_i32(data, hdr_idx + 12 + 24)
    svic_idx, svic_size = _find_chunk(data, "SHPVTINT")
    assert svic_idx > 0, "SHPVTINT missing"
    light_set = data[svic_idx + 12:svic_idx + 20]
    assert light_set == b"NORMALLT", f"light set = {light_set!r}"
    count = _read_i32(data, svic_idx + 12 + 12)
    assert count == num_verts, f"count {count} != num_verts {num_verts}"
    body = (svic_size - 12) - 16
    assert body == 4 * num_verts, f"intensity body = {body}"


@test
def test_prelight_colour_packs_rgb_correctly():
    """prelight_colour is written as packed 0x00RRGGBB per vertex."""
    obj = _make_cube()
    data = build_rif_level([obj], "lvl", include_prelight=True,
                           prelight_colour=0x00804020)
    svic_idx, _size = _find_chunk(data, "SHPVTINT")
    first = _read_u32(data, svic_idx + 12 + 16)
    assert first == 0x00804020, f"intensity[0] = 0x{first:08X}"


@test
def test_lightscale_payload_is_40_bytes():
    """LITSCALE payload is 40 bytes — the 44-byte form shifted the subtree."""
    obj = _make_cube()
    data = build_rif_level([obj], "lvl", include_litscale=True)
    idx, size = _find_chunk(data, "LITSCALE")
    assert idx > 0, "LITSCALE missing"
    assert size - 12 == 40, f"payload = {size - 12}"
    assert _read_i32(data, idx + 12) == 0, "LightMode should be 0"
    assert abs(_read_f32(data, idx + 16) - 1.0) < 1e-6, "prelight_multiply"


@test
def test_build_rif_bytes_orientation_is_identity():
    """The single-mesh path writes an identity quaternion, not (0,0,0,0)."""
    obj = _make_cube()
    data = build_rif_bytes(obj.data, object_name=obj.name)
    idx, _size = _find_chunk(data, "OBJHEAD1")
    assert idx > 0, "OBJHEAD1 missing"
    quat = struct.unpack_from("<4f", data, idx + 12 + 32)
    assert quat == (0.0, 0.0, 0.0, 1.0), f"quat = {quat}"


# ------------------------------------------------- tests S6 F11 (point lights)

def _reference_vertex_intensity(vertex, normal, lights, base_colour,
                                light_scale=rif_lighting.ONE_FIXED):
    """Independent port of Kshape.c::VertexIntensity_Standard_Opt.

    Written straight from the C so a typo in rif/lighting.py cannot hide
    behind the same typo here. ``shrd`` is ``(a*b) // 65536`` — floor
    division — and ``DIV_FIXED`` keeps the full 32-bit ``a << 16``.
    """
    def shrd(a, b):
        return (a * b) // 65536

    def trunc_div(a, b):
        q = abs(a) // abs(b)
        return q if (a < 0) == (b < 0) else -q

    def wrap32(value):
        value &= 0xFFFFFFFF
        return value - 0x100000000 if value >= 0x80000000 else value

    blue_i = (base_colour & 255) * 257
    green_i = ((base_colour >> 8) & 255) * 257
    red_i = ((base_colour >> 16) & 255) * 257

    vx, vy, vz = vertex
    nx, ny, nz = normal
    for light in lights:
        lx, ly, lz = light["location"]
        rng = light.get("range", 20000)
        bright = light.get("brightness", rif_lighting.ONE_FIXED)
        colour = light.get("colour", 0xFFFFFF)

        tlx, tly, tlz = lx - vx, ly - vy, lz - vz
        dx, dy, dz = abs(tlx), abs(tly), abs(tlz)
        if dx > dy:
            dist = dx + ((dy + dz) >> 2) if dx > dz else dz + ((dy + dx) >> 2)
        else:
            dist = dy + ((dx + dz) >> 2) if dy > dz else dz + ((dx + dy) >> 2)
        if dist >= rng:
            continue

        bor = trunc_div(bright << 16, rng)
        idot = shrd(rng - dist, bor)
        if dist > 0:
            dot = (shrd(int(round(nx * rif_lighting.ONE_FIXED)), tlx)
                   + shrd(int(round(ny * rif_lighting.ONE_FIXED)), tly)
                   + shrd(int(round(nz * rif_lighting.ONE_FIXED)), tlz))
            if dot > 0:
                idot = (trunc_div(wrap32(idot * dot), dist) + idot // 4) // 2
            else:
                idot //= 8

        red_i += shrd(idot, ((colour >> 16) & 255) * 257)
        green_i += shrd(idot, ((colour >> 8) & 255) * 257)
        blue_i += shrd(idot, (colour & 255) * 257)

    return ((min(red_i >> 8, 255) << 16)
            | (min(green_i >> 8, 255) << 8)
            | min(blue_i >> 8, 255))


@test
def test_bake_matches_engine_formula():
    """Baked values equal an independent port of the engine's C code."""
    lights = [{"location": (1200, -3400, 500), "colour": 0xFFB060,
               "brightness": rif_lighting.ONE_FIXED * 2, "range": 12000}]
    cases = [
        ((0, 0, 0), (0.0, 0.0, 1.0), 0x00202020),
        ((4000, 0, 0), (-1.0, 0.0, 0.0), 0x00000000),
        ((-2500, 1500, 800), (0.577, 0.577, 0.577), 0x00404040),
        ((9000, 0, 0), (1.0, 0.0, 0.0), 0x00101010),
    ]
    for vertex, normal, base in cases:
        got = rif_lighting.bake_vertex_intensities(
            [vertex], [normal], lights, base_colour=base)[0]
        want = _reference_vertex_intensity(vertex, normal, lights, base)
        assert got == want, f"{vertex}: {hex(got)} != {hex(want)}"


@test
def test_bake_no_lights_passes_base_through():
    """With no lights the base colour is returned unchanged."""
    for base in (0x00000000, 0x00FFFFFF, 0x00204060):
        out = rif_lighting.bake_vertex_intensities(
            [(100, -200, 300)], [(0.0, 0.0, 1.0)], [], base_colour=base)
        assert out == [base], hex(out[0])


@test
def test_bake_falloff_is_monotonic():
    """A vertex further from the light is never brighter."""
    lights = [{"location": (0, 0, 0), "colour": 0xFFFFFF,
               "brightness": rif_lighting.ONE_FIXED, "range": 20000}]
    values = [
        rif_lighting.bake_vertex_intensities(
            [(d, 0, 0)], [(-1.0, 0.0, 0.0)], lights, base_colour=0)[0]
        for d in (500, 2000, 5000, 10000, 19000)
    ]
    assert all(values[i] >= values[i + 1] for i in range(len(values) - 1)), \
        [hex(v) for v in values]


@test
def test_bake_out_of_range_contributes_nothing():
    """A light beyond its own range leaves only the base colour."""
    lights = [{"location": (100000, 0, 0), "range": 1000,
               "brightness": rif_lighting.ONE_FIXED, "colour": 0xFFFFFF}]
    out = rif_lighting.bake_vertex_intensities(
        [(0, 0, 0)], [(1.0, 0.0, 0.0)], lights, base_colour=0x00000000)
    assert out == [0], hex(out[0])


@test
def test_bake_facing_vertex_beats_facing_away():
    """Normals pointing at the light receive more light than those away."""
    lights = [{"location": (0, 0, 0), "range": 20000,
               "brightness": rif_lighting.ONE_FIXED, "colour": 0xFFFFFF}]
    facing = rif_lighting.bake_vertex_intensities(
        [(5000, 0, 0)], [(-1.0, 0.0, 0.0)], lights, base_colour=0)[0]
    away = rif_lighting.bake_vertex_intensities(
        [(5000, 0, 0)], [(1.0, 0.0, 0.0)], lights, base_colour=0)[0]
    assert facing > away, (hex(facing), hex(away))


@test
def test_bake_for_object_translates_lights():
    """bake_for_object must express lights relative to the object origin."""
    light = [{"location": (10000, 0, 0), "range": 20000,
              "brightness": rif_lighting.ONE_FIXED, "colour": 0xFFFFFF}]
    local = rif_lighting.bake_for_object(
        (5000, 0, 0), [(5000, 0, 0)], [(1.0, 0.0, 0.0)], light,
        base_colour=0)[0]
    direct = rif_lighting.bake_vertex_intensities(
        [(5000, 0, 0)], [(1.0, 0.0, 0.0)], [{"location": (5000, 0, 0),
                                            "range": 20000,
                                            "brightness":
                                            rif_lighting.ONE_FIXED,
                                            "colour": 0xFFFFFF}],
        base_colour=0)[0]
    assert local == direct, (hex(local), hex(direct))


@test
def test_level_writes_baked_intensities_verbatim():
    """object_intensities reach SHPVTINT byte-for-byte."""
    obj = _make_cube()
    num_verts = len(obj.data.vertices)
    baked = [0x00112233 + i for i in range(num_verts)]
    data = build_rif_level([obj], "lvl", include_prelight=True,
                           object_intensities=[baked])
    idx, size = _find_chunk(data, "SHPVTINT")
    assert idx > 0, "SHPVTINT missing"
    count = _read_i32(data, idx + 12 + 12)
    assert count == num_verts, f"count {count} != {num_verts}"
    written = [_read_u32(data, idx + 12 + 16 + 4 * i) for i in range(count)]
    assert written == baked, (written[:4], baked[:4])


@test
def test_stdlight_runtime_flag_is_opt_in():
    """LOFlag_Runtime (0x10) is only set when emit_runtime_lights is on."""
    obj = _make_cube()
    lights = [{"location": (0.0, 0.0, 1.0), "colour": 0x00FFFFFF,
               "brightness": rif_lighting.ONE_FIXED, "range": 15000}]

    plain = build_rif_level([obj], "lvl", lights=lights,
                            emit_runtime_lights=False)
    idx, _size = _find_chunk(plain, "STDLIGHT")
    assert idx > 0, "STDLIGHT missing"
    local_flags = _read_i32(plain, idx + 12 + 72)
    assert not (local_flags & 0x10), f"flags 0x{local_flags:08X}"

    runtime = build_rif_level([obj], "lvl", lights=lights,
                              emit_runtime_lights=True)
    idx, _size = _find_chunk(runtime, "STDLIGHT")
    local_flags = _read_i32(runtime, idx + 12 + 72)
    assert local_flags & 0x10, f"flags 0x{local_flags:08X}"


@test
def test_stdlight_written_fields_match_spec():
    """STDLIGHT carries the light's converted position, range and colour."""
    obj = _make_cube()
    lights = [{"location": (1.0, 2.0, 3.0), "colour": 0x00123456,
               "brightness": rif_lighting.ONE_FIXED, "range": 12345}]
    data = build_rif_level([obj], "lvl", lights=lights)
    idx, size = _find_chunk(data, "STDLIGHT")
    assert idx > 0, "STDLIGHT missing"
    assert size == 96, f"STDLIGHT size {size}"
    # blender_to_rif_axis(1, 2, 3) = (1, -3, 2); x1000 -> (1000, -3000, 2000).
    x, y, z = _read_i32x3(data, idx + 12 + 4)
    assert (x, y, z) == (1000, -3000, 2000), f"location {(x, y, z)}"
    # brightness/spread/range at payload offsets 52/56/60.
    assert _read_i32(data, idx + 12 + 52) == rif_lighting.ONE_FIXED
    assert _read_i32(data, idx + 12 + 60) == 12345
    assert _read_u32(data, idx + 12 + 64) == 0x00123456


@test
def test_bake_spot_cone_limits_the_light():
    """A spot lights only inside its cone; the engine has no cone code."""
    half = 0.5236 / 2                      # 30 degrees full angle
    cos_inner, cos_outer = rif_lighting.spot_cosines(half, 0.0)
    spot = {
        "location": (0, 0, 0), "colour": 0xFFFFFF,
        "brightness": rif_lighting.power_to_brightness(1000.0),
        "range": 25000, "direction": (0.0, 0.0, 1.0),
        "spot_cos_inner": cos_inner, "spot_cos_outer": cos_outer,
    }
    # Directly ahead (inside the cone) versus straight behind (outside it).
    inside = rif_lighting.bake_vertex_intensities(
        [(0, 0, 1000)], [(0.0, 0.0, -1.0)], [spot], base_colour=0)[0]
    outside = rif_lighting.bake_vertex_intensities(
        [(0, 0, -1000)], [(0.0, 0.0, 1.0)], [spot], base_colour=0)[0]
    assert inside > 0, "the cone centre must be lit"
    assert outside == 0, f"outside the cone got {hex(outside)}"

    # An omni light at the same spot does reach the point behind it.
    omni = {k: v for k, v in spot.items()
            if k not in ("direction", "spot_cos_inner", "spot_cos_outer")}
    behind_omni = rif_lighting.bake_vertex_intensities(
        [(0, 0, -1000)], [(0.0, 0.0, 1.0)], [omni], base_colour=0)[0]
    assert behind_omni > 0, "an omni light should still reach behind itself"


@test
def test_spot_cosines_shrink_with_blend():
    """A larger spot_blend widens the soft edge of the cone."""
    hard = rif_lighting.spot_cosines(0.5, 0.0)
    soft = rif_lighting.spot_cosines(0.5, 0.5)
    assert hard[0] == hard[1], "no blend means no soft edge"
    assert soft[0] < soft[1], "blend must shrink the inner cone"
    assert soft[1] == hard[1], "the outer angle must not move"


@test
def test_power_to_brightness_scales_linearly():
    """Doubling Blender's Power doubles the engine brightness."""
    one = rif_lighting.power_to_brightness(1000.0)
    two = rif_lighting.power_to_brightness(2000.0)
    assert two == 2 * one, (one, two)
    assert rif_lighting.power_to_brightness(0.0) == 0
    assert rif_lighting.power_to_brightness(1000.0, 2.0) == two


@test
def test_world_ambient_uses_colour_and_strength():
    """World colour is scaled by World strength and packed as 0x00RRGGBB."""
    assert rif_lighting.world_ambient_colour((1.0, 1.0, 1.0), 1.0) == 0xFFFFFF
    assert rif_lighting.world_ambient_colour((1.0, 1.0, 1.0), 0.0) == 0x000000
    packed = rif_lighting.world_ambient_colour((0.0, 0.0, 1.0), 1.0)
    assert packed == 0x0000FF, hex(packed)
    grey = rif_lighting.world_ambient_colour((0.5, 0.5, 0.5), 1.0)
    assert (grey >> 16) == ((grey >> 8) & 255) == (grey & 255)
    assert rif_lighting.world_ambient_level((1.0, 1.0, 1.0), 0.5) == 128


@test
def test_level_without_lights_writes_no_stdlight():
    """No lights passed means no STDLIGHT at all.

    A fabricated fallback light used to be written here. It lit the player and
    NPCs by itself (they read exactly these chunks) and silently invalidated
    lighting tests, so the LIGHTSET must stay empty instead.
    """
    obj = _make_cube()
    data = build_rif_level([obj], "lvl", include_prelight=True)
    assert b"LIGHTSET" in data, "LIGHTSET itself is still required"
    assert _find_chunk(data, "STDLIGHT")[0] < 0, \
        "a level with no lights must not gain one"
    assert b"AMBIENCE" in data, "AMBIENCE still has to reach the engine"


@test
def test_direction_to_rif_preserves_length():
    """A direction is rotated like a position but stays unit length."""
    for direction in ((0.0, 0.0, -1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
        out = rif_lighting.direction_to_rif(direction)
        length = sum(c * c for c in out) ** 0.5
        assert abs(length - 1.0) < 1e-9, (direction, out, length)


# ------------------------------------------------------------------ runner

def run_all():
    """Run all registered tests, printing PASS/FAIL/SKIP per test.

    Deliberately does NOT call _clear_scene() in a finally block: deleting
    Blender objects that may still be referenced by a running operator can
    trigger an access violation in MSVCP140.dll. Each test helper does its
    own scene cleanup at the start instead.
    """
    if not HAS_BPY:
        print("bpy not importable — run this script inside Blender.")
        return False

    passed = 0
    failed = 0
    skipped = 0
    start = time.time()

    for test_fn in _TESTS:
        name = test_fn.__name__
        try:
            test_fn()
            print(f"  PASS  {name}")
            passed += 1
        except SkipTest as exc:
            print(f"  SKIP  {name}: {exc}")
            skipped += 1
        except AssertionError as exc:
            print(f"  FAIL  {name}: {exc}")
            failed += 1
        except Exception as exc:
            print(f"  ERROR {name}: {exc}")
            traceback.print_exc()
            failed += 1

    duration = time.time() - start
    total = len(_TESTS)
    print(f"\n{passed}/{total} passed, {failed} failed, "
          f"{skipped} skipped ({duration:.2f}s)")
    return failed == 0


if __name__ == "__main__":
    ok = run_all()
    # sys.exit() crashes Blender when called from the Text Editor (operator
    # context receives SystemExit and tears down the interpreter mid-frame).
    # Only exit when we are truly running headless from the CLI.
    try:
        import bpy
        _headless = bpy.app.background
    except Exception:
        _headless = True
    if _headless:
        sys.exit(0 if ok else 1)