"""Blender roundtrip tests for RIF exporter.

Run inside Blender:
    blender --background --python tests/blender_roundtrip.py
Or from the Text Editor: Run Script.

32 tests covering S1-S6: single mesh, calibration, UV, bitmap indices,
axis conversion, quaternion, multi-object, hierarchy, level skeleton,
LIGHTSET, AVPENVIR.
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