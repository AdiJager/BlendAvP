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
from pathlib import Path


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
    from mathutils import Matrix, Quaternion, Vector
    HAS_BPY = True
except ImportError:
    bpy = None
    Quaternion = None
    HAS_BPY = False

# Directory name of the add-on, used to import it as a package. Kept as a
# literal so this file works whether or not it is executed as part of it.
PACKAGE_NAME = "avp_rif_exporter"

# This file lives at <parent>/<PACKAGE_NAME>/tests/blender_roundtrip.py, so the
# parent is the directory that must be on `sys.path` for `PACKAGE_NAME` to be
# importable as a package. Derived from this file rather than hard-coded, so the
# suite also works when run from a copy inside Blender's addons folder.
_TESTS_FILE = os.path.abspath(__file__)
_PACKAGE_DIR = os.path.dirname(os.path.dirname(_TESTS_FILE))
_PARENT_DIR = os.path.dirname(_PACKAGE_DIR)


class SkipTest(Exception):
    """Raised by tests that cannot run in the current environment."""


def load_package_module(relative_name):
    """Import one of the add-on's modules fresh from disk.

    A long-lived Blender session keeps `rif.builder` and friends in
    `sys.modules`, so editing them on disk has no effect on the running
    interpreter — the tests keep exercising the OLD code and report failures
    that were already fixed.

    The module is loaded as a **top-level** module with the add-on directory on
    `sys.path`, not as `avp_rif_exporter.rif.builder`. Going through the package
    does not work: `import a.b.c` consults the parent package's attribute before
    `sys.modules`, so a stale parent hands back the OLD module, and dropping the
    parents would re-execute `avp_rif_exporter/__init__.py`, which imports
    `operators` and registers the add-on a second time. Importing `rif.builder`
    directly avoids both problems, and `rif` is importable as a top-level
    package anyway — that is how `_locate_rif_package` sets things up.

    Every fresh `rif.*` module shares one `sys.modules` entry, so the copies the
    tests hold stay mutually consistent.
    """
    import importlib

    for directory in (_PARENT_DIR, _PACKAGE_DIR):
        if directory not in sys.path:
            sys.path.insert(0, directory)

    # Drop the whole top-level package so nothing stale survives; importing the
    # requested module then pulls its dependencies from disk again.
    for cached in list(sys.modules):
        if cached == "rif" or cached.startswith("rif."):
            sys.modules.pop(cached, None)
    importlib.invalidate_caches()
    return importlib.import_module(relative_name)


def load_operators():
    """Return this add-on's `operators` module.

    `operators` uses relative imports (`from .rif.types import ...`), so it only
    imports as a submodule of the add-on package.

    Inside Blender the add-on may already be imported, and then its live module
    must be reused: importing a second copy would make the tests compare results
    produced by different module objects. The match has to be on the package
    name, not just the final component — Blender itself ships `cycles.operators`,
    and matching on `.operators` alone handed the tests that module, which then
    failed with "module 'cycles.operators' has no attribute ...".
    """
    import importlib

    wanted = f"{PACKAGE_NAME}.operators"
    if wanted in sys.modules:
        return sys.modules[wanted]
    for name, module in list(sys.modules.items()):
        if name == "operators" and getattr(module, "__package__", None) == PACKAGE_NAME:
            return module
        if name.endswith(".operators") and \
                getattr(module, "__package__", None) == PACKAGE_NAME:
            return module

    for directory in (_PARENT_DIR, _PACKAGE_DIR):
        if directory not in sys.path:
            sys.path.insert(0, directory)
    for candidate in (wanted, "operators"):
        try:
            return importlib.import_module(candidate)
        except ImportError:
            continue
    raise SkipTest("cannot import the add-on's operators module")


# Modules are imported fresh from disk so the tests never measure stale code
# left in `sys.modules` by a long-lived Blender session (see
# `load_package_module`). This must come after that helper is defined.
_builder = load_package_module("rif.builder")
_types = load_package_module("rif.types")
rif_lighting = load_package_module("rif.lighting")

build_rif_bytes = _builder.build_rif_bytes
build_rif_multi = _builder.build_rif_multi
build_rif_level = _builder.build_rif_level
_convert_orientation = _builder._convert_orientation
POLY_BITMAP_MASK = _builder.POLY_BITMAP_MASK
POLY_UV_HI_MASK = _builder.POLY_UV_HI_MASK
BITMAP_IDX_PROP = _builder.BITMAP_IDX_PROP

blender_to_rif_axis = _types.blender_to_rif_axis
rif_to_blender_axis = _types.rif_to_blender_axis
meters_to_rif = _types.meters_to_rif
uv_to_rif_pixels = _types.uv_to_rif_pixels
RIF_UNITS_PER_METER = _types.RIF_UNITS_PER_METER
DEFAULT_UV_PIXELS = _types.DEFAULT_UV_PIXELS


# ------------------------------------------------------------------ framework

_TESTS = []


def test(fn):
    """Register a function as a test case."""
    _TESTS.append(fn)
    return fn


# ------------------------------------------------------------------ helpers

def _clear_scene():
    """Remove all objects and mesh/material data from the current scene.

    Order matters. Objects go first while the depsgraph is coherent: removing a
    mesh that an object still references, then the object, is what makes Blender
    tear down render batches for a datablock that is mid-draw, and the symptom is
    a crash inside `DRW_cache_free_old_batches` / `DEG_iterator_objects_next`
    with no Python frames — which is a Blender bug, not a test failure.
    """
    if bpy is None:
        return
    for obj in list(bpy.data.objects):
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except (ReferenceError, RuntimeError):
            pass
    for mesh in list(bpy.data.meshes):
        try:
            bpy.data.meshes.remove(mesh)
        except (ReferenceError, RuntimeError):
            pass
    for mat in list(bpy.data.materials):
        try:
            bpy.data.materials.remove(mat)
        except (ReferenceError, RuntimeError):
            pass

    # Flush the dependency graph once, after the batch of removals, instead of
    # once per datablock.
    try:
        bpy.context.view_layer.update()
    except (AttributeError, RuntimeError):
        pass


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


def _new_cube_mesh(name="CubeMesh"):
    """Return a new cube mesh datablock without clearing the scene.

    `_make_cube` clears everything first, so it cannot be used to build several
    objects in one test. This creates just the mesh, leaving the caller free to
    make as many objects as it needs.
    """
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    created = bpy.context.active_object
    mesh = created.data
    mesh.name = name
    bpy.data.objects.remove(created, do_unlink=True)
    return mesh


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
    return data[idx + CHUNK_HEADER_BYTES:idx + size]


# Identifier (8 B) + total size (4 B); the payload starts after this.
CHUNK_HEADER_BYTES = 12


def _all_chunk_payloads(data, ident):
    """Return the payload bytes of every matching chunk.

    `_find_all_chunks` reports the chunk HEADER offset and its total size, so
    the payload starts 12 bytes in. Slicing `data[start:start + span]` instead
    includes the identifier and the size field, which shifts every record by
    four bytes and turns the following chunk's header into record data — the
    reader then reports nonsense indices and vertex numbers rather than a
    structural error.
    """
    return [data[start + CHUNK_HEADER_BYTES:start + span]
            for start, span in _find_all_chunks(data, ident)]


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
    """The writer and the reader invert each other, V included.

    Both directions carry the half-texel offset, so this asserts the pair rather
    than the division alone: a test of one half is what let the missing V flip
    look correct for as long as it did.
    """
    for u in (0.0, 0.25, 1.0):
        for v in (0.0, 0.25, 1.0):
            u_pix, v_pix = uv_to_rif_pixels(u, v)
            u_back = (u_pix + 0.5) / DEFAULT_UV_PIXELS
            v_back = 1.0 - (v_pix + 0.5) / DEFAULT_UV_PIXELS
            assert abs(u_back - u) < 1e-6, f"u={u} v={v} → {u_back}"
            assert abs(v_back - v) < 1e-6, f"u={u} v={v} → {v_back}"


@test
def test_uv_flips_v_for_the_engine():
    """Blender's bottom row (v=0) becomes the engine's last row, not its first.

    The engine counts rows from the top of the image (d3d_render.cpp:942), so a
    file written without the flip is mirrored in game. This is the assertion
    that would have caught it.
    """
    _u, bottom = uv_to_rif_pixels(0.5, 0.0)
    _u, top = uv_to_rif_pixels(0.5, 1.0)
    assert bottom > top, f"v=0 stored {bottom}, v=1 stored {top}"
    # The engine samples (V + 0.5) / height from the top row.
    assert abs((bottom + 0.5) / DEFAULT_UV_PIXELS - 1.0) < 1e-6
    assert abs((top + 0.5) / DEFAULT_UV_PIXELS) < 1e-6


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
def test_bitmap_idx_matches_bmpnames_index():
    """Every polygon index must exist in BMPNAMES, across several objects.

    Regression test for a real bug: the polygon texture index defaulted to the
    material's slot number on its own object, while BMPNAMES is numbered
    globally. With two objects the second one pointed at the first one's
    texture. The operator now derives both from one list; this checks the
    builder writes them consistently.
    """
    # Start from an empty scene. Objects left behind by an earlier test carry
    # their own `rif_bitmap_idx` (test_bitmap_idx_custom_property_override sets
    # 7), and the builder reads that property in preference to the slot index,
    # so a leak here shows up as "bitmap_idx 7 is not in BMPNAMES {0,1,2}".
    _clear_scene()

    # Build both objects BEFORE touching `_make_cube`, because that helper
    # clears the whole scene: anything created earlier would be destroyed, and
    # the failure surfaces later as "StructRNA of type Object has been removed".
    first_mesh = _new_cube_mesh("Mapped1")
    second_mesh = _new_cube_mesh("Mapped2")
    first = bpy.data.objects.new("MappedA", first_mesh)
    second = bpy.data.objects.new("MappedB", second_mesh)
    for obj in (first, second):
        bpy.context.collection.objects.link(obj)
    second.location = (5.0, 0.0, 0.0)

    mat_a = bpy.data.materials.new(name="alpha.RIM")
    mat_b = bpy.data.materials.new(name="beta.RIM")
    mat_c = bpy.data.materials.new(name="gamma.RIM")

    # Object one: alpha, beta. Object two: gamma only. Under the old slot-index
    # default, gamma would have been written as index 0 == alpha.
    first.data.materials.append(mat_a)
    first.data.materials.append(mat_b)
    second.data.materials.append(mat_c)
    for poly in first.data.polygons:
        poly.material_index = poly.index % 2
    for poly in second.data.polygons:
        poly.material_index = 0

    paths = ["alpha.RIM", "beta.RIM", "gamma.RIM"]
    data = build_rif_level([first, second], "Mapped", texture_paths=paths)

    # If the index below is wrong, this says WHY in one line: which builder file
    # is running, what each material resolves to, and whether the builder even
    # accepted the `texture_paths` argument. Without it the only symptom is
    # "bitmap_idx 7", which is equally consistent with stale code and with a
    # name mismatch.
    print(f"        [diag] build_rif_level z {getattr(build_rif_level, '__module__', '?')}"
          f" ({getattr(sys.modules.get(getattr(build_rif_level, '__module__', ''), None), '__file__', '?')})")
    for mark, obj in (("A", first), ("B", second)):
        names = [_builder.rim_name_for_material(m) for m in obj.data.materials]
        props = [m.get(BITMAP_IDX_PROP) for m in obj.data.materials]
        slots = sorted({p.material_index for p in obj.data.polygons})
        print(f"        [diag] {mark}: nazwy={names} rif_bitmap_idx={props} "
              f"sloty_w_poligonach={slots}")
    print(f"        [diag] indeksy z buildera: "
          f"{[_builder._resolve_bitmap_indices(o.data, paths) for o in (first, second)]}")

    # BMPNAMES must declare exactly those paths, indexed 0..2.
    idx, size = _find_chunk(data, "BMPNAMES")
    assert idx > 0, "no BMPNAMES chunk in the exported level"
    header, = struct.unpack_from("<I", data, idx + 12)
    count = header & 0xFFFF
    assert count == 3, f"expected 3 BMPNAMES entries, got {count}"

    declared = set()
    pos = idx + 16
    for _ in range(count):
        _flags, index = struct.unpack_from("<2I", data, pos)
        declared.add(index)
        nul = data.index(b"\x00", pos + 20)
        pos = (nul + 1 + 3) & ~3
    assert declared == {0, 1, 2}, f"BMPNAMES indices {declared}"

    # Every polygon index in every shape must be one of those.
    payloads = _all_chunk_payloads(data, "SHPPOLYS")
    assert len(payloads) == 2, f"expected 2 shapes, got {len(payloads)}"

    # Each payload must be a whole number of 36-byte records; if it is not, the
    # slice is wrong and every field read below would be shifted by four bytes.
    for shape_no, payload in enumerate(payloads):
        assert len(payload) % 36 == 0, (
            f"shape {shape_no} payload {len(payload)} B is not a multiple of "
            "36 - the chunk header was probably included in the slice")

    # Report the whole distribution BEFORE asserting, so a failure names the
    # shape and the record number instead of only "bitmap_idx N".
    for shape_no, payload in enumerate(payloads):
        records = list(_iter_poly_records(payload))
        seen = {}
        for _eng, _n, _f, colour, _vi in records:
            seen[colour & POLY_BITMAP_MASK] = \
                seen.get(colour & POLY_BITMAP_MASK, 0) + 1
        print(f"        [diag] shape {shape_no}: {len(records)} poligonow, "
              f"payload={len(payload)} B, rozklad bitmap_idx={seen}")

    for shape_no, payload in enumerate(payloads):
        for rec_no, (_eng, _n, _f, colour, _vi) in \
                enumerate(_iter_poly_records(payload)):
            index = colour & POLY_BITMAP_MASK
            assert index in declared, (
                f"shape {shape_no} record {rec_no}: polygon bitmap_idx {index} "
                f"is not in BMPNAMES {declared} (colour=0x{colour:08X})")


@test
def test_gamma_object_gets_its_own_index():
    """The second object must point at its own texture, not the first's.

    The concrete failure before the fix: gamma landed on index 0, which is
    alpha's texture, so object two rendered with object one's art.
    """
    # Scene must be empty: a stray `rif_bitmap_idx` on a leftover material
    # would override the index this test is checking, exactly as it did before.
    _clear_scene()

    # Meshes first, then objects, then materials: `_new_cube_mesh` appends and
    # removes a temporary object, so building meshes before objects avoids any
    # chance of an operator invalidating a reference held here.
    mesh_one = _new_cube_mesh("Gamma1")
    mesh_two = _new_cube_mesh("Gamma2")
    obj_one = bpy.data.objects.new("GammaA", mesh_one)
    obj_two = bpy.data.objects.new("GammaB", mesh_two)
    for obj in (obj_one, obj_two):
        bpy.context.collection.objects.link(obj)
    obj_two.location = (5.0, 0.0, 0.0)

    mat_a = bpy.data.materials.new(name="alpha.RIM")
    mat_b = bpy.data.materials.new(name="beta.RIM")
    mat_c = bpy.data.materials.new(name="gamma.RIM")

    obj_one.data.materials.append(mat_a)
    obj_one.data.materials.append(mat_b)
    obj_two.data.materials.append(mat_c)

    for poly in obj_one.data.polygons:
        poly.material_index = poly.index % 2
    for poly in obj_two.data.polygons:
        poly.material_index = 0

    # The operator's mapping is what feeds this; replicate its result here and
    # assert the builder honours it.
    paths = ["alpha.RIM", "beta.RIM", "gamma.RIM"]
    mat_a[BITMAP_IDX_PROP] = 0
    mat_b[BITMAP_IDX_PROP] = 1
    mat_c[BITMAP_IDX_PROP] = 2

    data = build_rif_level([obj_one, obj_two], "Mapped", texture_paths=paths)
    payloads = _all_chunk_payloads(data, "SHPPOLYS")
    assert len(payloads) == 2, f"expected 2 shapes, got {len(payloads)}"

    second_indices = {colour & POLY_BITMAP_MASK
                      for _e, _n, _f, colour, _v in _iter_poly_records(payloads[1])}
    assert second_indices == {2}, (
        f"second object uses {second_indices}, expected {{2}} (gamma)")
    first_indices = {colour & POLY_BITMAP_MASK
                     for _e, _n, _f, colour, _v in _iter_poly_records(payloads[0])}
    assert first_indices == {0, 1}, first_indices


@test
def test_bmpnames_name_has_no_graphics_prefix():
    """A BMPNAMES name must not repeat the textures root.

    The loader treats the name as a path relative to the textures root, which
    is `FirstTex_Directory = "Graphics"` (Projload.cpp:97), so a name beginning
    with `graphics\\` makes it look for `Graphics\\graphics\\...` and resolve
    nothing. An unresolved texture rewrites the polygon to I_Polygon with
    iflag_nolight (chnkload.cpp:3390), so the level renders empty while its
    collisions still work.
    """
    from rif.texture_library import RIM_PATH_PROP

    expected = r"Envrnmts\Battle\Cliff04.RIM"
    mat = bpy.data.materials.new(name="Prefixed.RIM")
    mat[RIM_PATH_PROP] = r"graphics\Envrnmts\Battle\Cliff04.RIM"
    operators = load_operators()
    got = operators._material_texture_path(mat)
    assert got == expected, (
        f"_material_texture_path returned {got!r}; the 'graphics\\' prefix must "
        "be stripped because the loader supplies it")

    mat[RIM_PATH_PROP] = expected
    assert operators._material_texture_path(mat) == expected, \
        "an already-correct name must pass through unchanged"


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
    # Light specs carry RIF millimetres, already axis-converted. The operator
    # does that conversion in `_collect_scene_lights`, so the builder never
    # rescales and both the record and the prelight bake agree on one space.
    lights = [{"location": (1000, -3000, 2000), "brightness": 200}]
    data = build_rif_level([obj], "lvl", lights=lights)
    idx, size = _find_chunk(data, "STDLIGHT")
    assert idx > 0, "STDLIGHT missing"
    assert size == 96, f"STDLIGHT size should be 96, got {size}"
    # location at payload offset +4 (after i32 light_number)
    x = _read_i32(data, idx + 12 + 4)
    y = _read_i32(data, idx + 12 + 8)
    z = _read_i32(data, idx + 12 + 12)
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
    """Without prelight the only flag left is RECENTRED.

    The shape is still recentred -- every module the exporter writes is -- so
    SHAPE_FLAG_RECENTRED (0x2000) belongs on it whether or not prelight data is
    written. What must never appear is the prelight/external-file bit 0x1000.
    """
    obj = _make_cube()
    data = build_rif_level([obj], "lvl", include_prelight=False)
    idx, _size = _shphead1_of(data)
    flags = _read_i32(data, idx + 12)
    assert flags == 0x00002000, f"flags = 0x{flags:08X}"
    assert flags & 0x00001000 == 0, f"flags = 0x{flags:08X}"
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
def test_bake_uses_world_space_not_local():
    """Geometry away from the origin must be lit where the light actually is.

    Regression test: the baker was handed object-LOCAL vertices while the lights
    were in world space, so an object standing away from the origin only lit up
    where it crossed point 0,0,0. In game that read as "the point light only
    bakes into point 0,0,0", while runtime lighting of the player looked correct.
    """
    operators = load_operators()

    obj = _make_cube()
    # Move the object 10 m along X, then light it where it now stands.
    obj.location = (10.0, 0.0, 0.0)
    bpy.context.view_layer.update()

    verts, normals = operators._object_world_geometry(obj)
    xs = sorted(v[0] for v in verts)
    assert xs[0] >= 9 * 1000, f"vertices are not in world space: {xs[:3]}"

    # A light beside the object must produce a non-zero intensity.
    light = {"location": (10_500, 0, 0), "range": 20_000,
             "brightness": rif_lighting.ONE_FIXED, "colour": 0xFFFFFF}
    baked = operators._bake_object_lights([obj], [light], 0)
    assert baked[0], "the baker returned nothing"
    assert any(v != 0 for v in baked[0]), \
        "a light standing beside the object lit nothing"


@test
def test_collected_light_positions_are_rif_millimetres():
    """A collected light spec must be in RIF millimetres, like the vertices.

    Regression test: the spec carried Blender metres, so a lamp 10.5 m away
    looked 10.5 units from the origin while SHPRAWVT vertices are in
    millimetres. The baker compares the two directly, so only geometry within a
    few millimetres of the RIF origin was lit — "the light bakes only into point
    0,0,0" even after the object transform was handled correctly.
    """
    operators = load_operators()

    light_data = bpy.data.lights.new(name="LampData", type="POINT")
    light_data.energy = 1000.0
    light_data.color = (1.0, 1.0, 1.0)
    lamp = bpy.data.objects.new("Lamp", light_data)
    bpy.context.collection.objects.link(lamp)
    lamp.location = (2.0, 3.0, 4.0)
    bpy.context.view_layer.update()

    lights, _skipped, _dark = operators._collect_scene_lights(bpy.context, 25.0)
    assert lights, "no light collected"
    location = lights[0]["location"]
    # blender_to_rif_axis maps (x, y, z) -> (x, -z, y), then metres -> mm.
    assert location == (2000, -4000, 3000), location
    assert all(isinstance(v, int) for v in location), \
        f"locations must be ints for the i32 STDLIGHT record: {location}"


@test
def test_stdlight_runtime_flag_is_opt_in():
    """LOFlag_Runtime (0x10) is only set when emit_runtime_lights is on."""
    obj = _make_cube()
    lights = [{"location": (0, 0, 1000), "colour": 0x00FFFFFF,
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
    """STDLIGHT carries the light's position, range and colour verbatim."""
    obj = _make_cube()
    # Specs are in RIF millimetres already; see test_stdlight_payload_layout.
    lights = [{"location": (1000, -3000, 2000), "colour": 0x00123456,
               "brightness": rif_lighting.ONE_FIXED, "range": 12345}]
    data = build_rif_level([obj], "lvl", lights=lights)
    idx, size = _find_chunk(data, "STDLIGHT")
    assert idx > 0, "STDLIGHT missing"
    assert size == 96, f"STDLIGHT size {size}"
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
    """A larger spot_blend widens the soft edge of the cone.

    Widening the soft band means the *inner* cone narrows. A narrower cone has
    a *larger* cosine, so the inner cosine must rise above the outer one, not
    fall below it. The original assertion had this backwards, which is why it
    failed even though spot_cosines was correct.
    """
    hard = rif_lighting.spot_cosines(0.5, 0.0)
    soft = rif_lighting.spot_cosines(0.5, 0.5)
    assert hard[0] == hard[1], "no blend means no soft edge"
    assert soft[0] > soft[1], "blend must shrink the inner cone"
    assert soft[0] > hard[0], "the inner angle must narrow as blend grows"
    assert soft[1] == hard[1], "the outer angle must not move"


@test
def test_power_to_brightness_scales_linearly_below_the_ceiling():
    """Below the ceiling, brightness is proportional to Blender's Power.

    `LightBright` is documented as "0->1 Fixed Point" (prototyp.h:772), so the
    engine cannot represent more than ONE_FIXED; the exporter clamps there.
    """
    one = rif_lighting.power_to_brightness(125.0)
    two = rif_lighting.power_to_brightness(250.0)
    assert two == 2 * one, (one, two)
    assert rif_lighting.power_to_brightness(0.0) == 0
    assert one < rif_lighting.BRIGHTNESS_CEILING


@test
def test_power_to_brightness_clamps_at_one_fixed():
    """A very bright lamp saturates at ONE_FIXED rather than overflowing."""
    assert rif_lighting.power_to_brightness(1e9) == rif_lighting.ONE_FIXED
    assert rif_lighting.BRIGHTNESS_CEILING == rif_lighting.ONE_FIXED
    raw = rif_lighting.power_to_brightness(100000.0)
    assert raw == rif_lighting.BRIGHTNESS_CEILING


@test
def test_brightness_anchor_is_mid_grey():
    """The documented anchor must actually produce mid-grey in a bake.

    This is the regression test for "the room is black in-game": the anchor
    states that BLENDER_REFERENCE_POWER_W at BLENDER_REFERENCE_DISTANCE_M
    reads as roughly half scale, and that has to hold through the real bake,
    not just in the arithmetic.
    """
    watts = rif_lighting.BLENDER_REFERENCE_POWER_W
    metres = rif_lighting.BLENDER_REFERENCE_DISTANCE_M
    brightness = rif_lighting.effective_brightness(
        watts, rif_lighting.DEFAULT_LIGHT_RANGE_MM)
    assert brightness < rif_lighting.BRIGHTNESS_CEILING, \
        "the anchor must not already be clipped"
    light = {"location": (0, 0, 0), "colour": 0xFFFFFF,
             "brightness": brightness,
             "range": rif_lighting.DEFAULT_LIGHT_RANGE_MM}
    value = rif_lighting.bake_vertex_intensities(
        [(int(metres * 1000), 0, 0)], [(-1.0, 0.0, 0.0)], [light],
        base_colour=0)[0]
    level = (value >> 8) & 255
    assert 80 <= level <= 160, f"anchor gave {level}/255, expected mid-grey"


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


# ------------------------------------------- multiplayer spawns and generators
#
# These cover the Beta work. They run here, not only in the offline harness,
# because that harness uses a `bpy` stub which repeatedly turned out to be both
# poorer and more tolerant than Blender — a test that passes against the stub is
# not evidence that the add-on works.

MP_SPAWN_FLAGS = 0x48002801        # MULTIPLAYERSTART | ADVANCED | marine | predator
NPC_FLAGS = 0x00003801             # the commonest fixed-NPC flag word in retail


@test
def test_player_spawn_reaches_the_file():
    """A spawn spec becomes an AVPGENER inside REBENVDT > SPECLOBJ."""
    obj = _make_cube()
    spawn = {"location": (1000, 2000, 3000), "name": "gen1",
             "orientation": 0, "type": 0, "flags": MP_SPAWN_FLAGS}
    data = build_rif_level([obj], "lvl", spawns=[spawn])
    idx, size = _find_chunk(data, "AVPGENER")
    assert idx > 0, "AVPGENER missing when a spawn is given"
    # Location is already in RIF millimetres and must be written verbatim:
    # converting it again here is the bug that once put every light at 0,0,0.
    assert _read_i32(data, idx + 12) == 1000, "x not written verbatim"
    assert _read_i32(data, idx + 16) == 2000, "y not written verbatim"
    assert _read_i32(data, idx + 20) == 3000, "z not written verbatim"
    # Payload layout: 0 location, 12 orientation, 16 type, 20 flags, 24 the four
    # bytes, 28 name. The chunk header adds 12, so flags sit at idx + 32.
    assert _read_i32(data, idx + 28) == 0, "type should be 0 for a spawn"
    assert _read_i32(data, idx + 32) == MP_SPAWN_FLAGS, "flags changed"
    # Payload is round_up_to_4(29 + len(name)): 36 for a four-character name,
    # plus the 12-byte header. The name is padded, not truncated.
    assert size == 48, f"chunk size should be 48, got {size}"


@test
def test_spawn_orientation_is_masked_to_twelve_bits():
    """Only 12 bits of orientation survive, as the engine loads them."""
    obj = _make_cube()
    data = build_rif_level(
        [obj], "lvl",
        spawns=[{"location": (0, 0, 0), "name": "g", "orientation": 4096 + 17,
                 "type": 0, "flags": MP_SPAWN_FLAGS}])
    idx, _size = _find_chunk(data, "AVPGENER")
    assert _read_i32(data, idx + 24) == 17, "orientation was not masked"


@test
def test_generator_names_are_made_unique():
    """Duplicate names would cross-link AVPGENEX, so they must be fixed.

    The engine matches a generator's extra data by comparing this string, so two
    identical names would attach the wrong settings — or none.
    """
    obj = _make_cube()
    spawns = [{"location": (0, 0, 0), "name": "same", "orientation": 0,
               "type": 1, "flags": NPC_FLAGS},
              {"location": (0, 0, 0), "name": "same", "orientation": 0,
               "type": 1, "flags": NPC_FLAGS}]
    data = build_rif_level([obj], "lvl", spawns=spawns)
    names = []
    for payload in _all_chunk_payloads(data, "AVPGENER"):
        names.append(payload[28:].split(b"\x00")[0])
    assert len(names) == 2, names
    assert len(set(names)) == 2, f"names are not unique: {names}"


@test
def test_global_generator_written_for_npc_generators():
    """GLOGENDC appears, directly under REBENVDT, whenever NPCs can spawn.

    It is not decoration: every retail level with non-zero alien weights also
    carries this chunk, and without a budget the engine has nothing to spawn.
    """
    obj = _make_cube()
    npc = {"location": (0, 0, 0), "name": "gen1", "orientation": 0,
           "type": 1, "flags": NPC_FLAGS}
    data = build_rif_level([obj], "lvl", spawns=[npc])
    idx, size = _find_chunk(data, "GLOGENDC")
    assert idx > 0, "GLOGENDC missing for a level with an NPC generator"
    assert size == 40, f"GLOGENDC size should be 40 (12+28), got {size}"


@test
def test_player_spawns_alone_need_no_global_generator():
    """A level of nothing but player spawns must not gain a GLOGENDC.

    The engine places players itself, so a budget would be dead weight — and
    adding one silently would make the exporter's own test files differ from
    what the user placed.
    """
    obj = _make_cube()
    spawn = {"location": (0, 0, 0), "name": "gen1", "orientation": 0,
             "type": 0, "flags": MP_SPAWN_FLAGS}
    data = build_rif_level([obj], "lvl", spawns=[spawn])
    assert _find_chunk(data, "AVPGENER")[0] > 0, "the spawn itself is missing"
    assert _find_chunk(data, "GLOGENDC")[0] < 0, \
        "player spawns must not force a GLOGENDC"


@test
def test_global_generator_defaults_match_retail():
    """The default budget is the commonest retail combination."""
    obj = _make_cube()
    npc = {"location": (0, 0, 0), "name": "gen1", "orientation": 0,
           "type": 1, "flags": NPC_FLAGS}
    data = build_rif_level([obj], "lvl", spawns=[npc])
    idx, _size = _find_chunk(data, "GLOGENDC")
    fields = [_read_i32(data, idx + 12 + 4 * i) for i in range(7)]
    enemy, max_npcs, per_minute, accel, hive, spare1, spare2 = fields
    assert enemy == 0, f"default should be aliens, got {enemy}"
    assert max_npcs == 25, f"MaxNPCSOnLevel default is {max_npcs}"
    assert per_minute == 4, f"NPCSPerMinute default is {per_minute}"
    assert accel == 2, f"NPCAcceleration default is {accel}"
    assert hive == 32000, f"HiveStateChangeTime default is {hive}"
    assert (spare1, spare2) == (0, 0), "spares must stay zero"


@test
def test_global_generator_can_be_suppressed_and_overridden():
    """`False` omits the chunk; a dict overrides individual fields."""
    obj = _make_cube()
    npc = {"location": (0, 0, 0), "name": "gen1", "orientation": 0,
           "type": 1, "flags": NPC_FLAGS}

    data = build_rif_level([obj], "lvl", spawns=[npc],
                           global_generator=False)
    assert _find_chunk(data, "GLOGENDC")[0] < 0, \
        "an explicit False must suppress the chunk"

    data = build_rif_level([obj], "lvl", spawns=[npc],
                           global_generator={"max_npcs": 40,
                                             "enemy_generated": 1})
    idx, _size = _find_chunk(data, "GLOGENDC")
    assert _read_i32(data, idx + 12) == 1, "enemy_generated override ignored"
    assert _read_i32(data, idx + 16) == 40, "max_npcs override ignored"


@test
def test_placeables_module_builds_the_menu():
    """Every placeable entry has its own operator description.

    Blender takes a menu item's tooltip from its operator class, so a shared
    operator would show one text everywhere. The table exists to avoid that, and
    this checks it in Blender rather than only against the stub.
    """
    placeables = load_package_module("placeables")
    assert len(placeables.PLACEABLES) == len(placeables.PLACEABLE_OPERATORS), \
        "one operator per menu entry expected"
    descriptions = {}
    for spec in placeables.PLACEABLES:
        cls = placeables.PLACEABLE_OPERATORS[spec.key]
        assert cls.bl_description == spec.description, spec.key
        assert len(spec.description) > 40, \
            f"{spec.label}: tooltip too short to be descriptive"
        assert spec.description not in descriptions.values(), \
            f"{spec.label} shares its tooltip with another entry"
        descriptions[spec.key] = spec.description
    # Categories must come first in the menu, and each must hold something.
    for category, label in placeables.CATEGORY_LABELS:
        entries = [s for s in placeables.PLACEABLES if s.category == category]
        assert entries, f"category {label} is empty"


@test
def test_placeable_properties_register_in_blender():
    """The property group really exposes its fields once registered.

    This is the check that a `from __future__ import annotations` mistake would
    break: that makes the annotations strings, Blender then registers nothing,
    and the panel silently comes up empty.

    A PropertyGroup cannot be constructed directly — Blender hands out one
    instance per ID through the pointer property — so the group is registered
    and read back off an object, which is also how the panel reaches it.
    """
    placeables = load_package_module("placeables")
    group = placeables.RIF_PlaceableSettings
    annotations = getattr(group, "__annotations__", {})
    assert annotations, "the property group declares no fields"
    for field in ("race_marine", "race_alien", "race_predator", "npc_type",
                  "sub_type", "mix_drone"):
        assert field in annotations, f"missing declared field {field}"
        # A string annotation means Blender cannot build the property from it.
        assert not isinstance(annotations[field], str), (
            f"{field} is declared as a string annotation, so Blender would "
            "skip it — remove `from __future__ import annotations`")

    # The group carries a collection of switch targets, and Blender will not
    # build it unless the target group is registered first.
    bpy.utils.register_class(placeables.RIF_SwitchTarget)
    bpy.utils.register_class(group)
    bpy.types.Object.rif_placeable = bpy.props.PointerProperty(type=group)
    holder = None
    try:
        holder = bpy.data.objects.new("PropTest", None)
        bpy.context.collection.objects.link(holder)
        settings = holder.rif_placeable
        assert settings.race_marine is True, "race_marine default is wrong"
        assert settings.race_alien is True, "race_alien default is wrong"
        assert settings.race_predator is True, "race_predator default is wrong"
        assert settings.npc_type == 1, "npc_type default is wrong"
        assert settings.mix_drone == 1, "mix_drone default is wrong"
        # The fields must be writable, not read-only placeholders.
        settings.npc_type = 8
        assert holder.rif_placeable.npc_type == 8, "npc_type is not writable"
    finally:
        if holder is not None:
            bpy.data.objects.remove(holder, do_unlink=True)
        del bpy.types.Object.rif_placeable
        bpy.utils.unregister_class(group)
        bpy.utils.unregister_class(placeables.RIF_SwitchTarget)


@test
def test_every_icon_name_exists_in_blender():
    """No `icon=` string may name an icon Blender does not have.

    An invented icon does not fail at import: it raises inside `draw()` on every
    redraw, so the panel never appears and the console fills with the same
    traceback. `icon="LOGIC"` shipped exactly that way.

    The scan walks the AST rather than matching text, because a regular
    expression also matches the `icon="LOGIC"` quoted inside this very
    docstring — which is how a guard ends up reporting itself.
    """
    import ast
    function = bpy.types.UILayout.bl_rna.functions["label"]
    valid = {item.identifier
             for item in function.parameters["icon"].enum_items}
    assert valid, "could not read Blender's icon enum"

    bad = []
    for path in _sources_under_package():
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            bad.append(f"{path.name} does not parse: {exc}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "icon":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and \
                        isinstance(value.value, str):
                    if value.value not in valid:
                        bad.append(f"{path.name}:{value.lineno} uses "
                                   f"{value.value!r}")
    assert not bad, "invalid icon names: " + "; ".join(bad)

    # The scan above only sees literal `icon="..."` keywords. A menu drawn as
    # `layout.operator(..., icon=spec.icon)` takes its icon from the table, and
    # that is where `EMPTY_CIRCLE` hid: valid to the AST scan, fatal at draw
    # time. The values have to be read from the table too.
    placeables = load_package_module("placeables")
    table_bad = [f"PLACEABLES[{s.key}].icon = {s.icon!r}"
                 for s in placeables.PLACEABLES if s.icon not in valid]
    assert not table_bad, \
        "invalid icons in the placeables table: " + "; ".join(table_bad)


@test
def test_category_menus_draw():
    """Every category menu must draw, exercising each entry's icon.

    A menu is drawn far less often than a panel, so a bad icon here can sit
    unnoticed until the user happens to open that one category. The recording
    layout checks the icon passed to every operator call.
    """
    placeables = load_package_module("placeables")
    layout = _RecordingLayout(record_icons=True)
    for category, label in placeables.CATEGORY_LABELS:
        menu = placeables.CATEGORY_MENUS[category]
        layout.reset()
        menu.draw(_PanelHost(layout), bpy.context)
        assert layout.calls, f"menu {label} drew nothing"
        operators = [c for c in layout.calls if c[0] == "operator"]
        assert operators, f"menu {label} listed no entries"
    # The top-level AvP menu must also draw, including its separators.
    layout.reset()
    placeables.menu_draw(_PanelHost(layout), bpy.context)
    assert any(c[0] == "menu" for c in layout.calls), \
        "the AvP menu listed no categories"


def _sources_under_package():
    """Return every .py file in the add-on, for the icon scan."""
    root = Path(_PACKAGE_DIR)
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


@test
def test_placeable_panel_draws_for_every_kind():
    """The properties panel must draw for each placeable kind.

    Blender swallows exceptions raised while drawing a panel: the panel simply
    comes up empty and the console repeats the same traceback on every redraw.
    That is how the invalid `icon="LOGIC"` shipped unnoticed — a syntax-clean
    module that fails only at draw time.

    A recording layout stands in for the real one. It validates every icon
    against Blender's enum and resolves every `prop()` target, which is what
    catches a missing settings field.
    """
    placeables = load_package_module("placeables")
    panel = placeables.RIF_PT_placeable
    group = placeables.RIF_PlaceableSettings

    # The group carries a collection of switch targets, and Blender will not
    # build it unless the target group is registered first.
    bpy.utils.register_class(placeables.RIF_SwitchTarget)
    bpy.utils.register_class(group)
    bpy.types.Object.rif_placeable = bpy.props.PointerProperty(type=group)
    holder = None
    try:
        holder = bpy.data.objects.new("PanelTest", None)
        bpy.context.collection.objects.link(holder)
        # Blender's panel draw reads the active object off the context.
        bpy.context.view_layer.objects.active = holder
        holder.select_set(True)

        layout = _RecordingLayout(record_icons=True)
        for kind in sorted({
                placeables.KIND_PLAYER_START_SP,
                placeables.KIND_PLAYER_START_MP,
                placeables.KIND_NPC,
                placeables.KIND_DEFAULT_ENEMY,
                placeables.KIND_WEIGHTED,
                placeables.KIND_WAYPOINT,
                placeables.KIND_PATROL_PATH,
                placeables.KIND_GLOBAL_GENERATOR}):
            holder.rif_placeable.kind = kind
            layout.reset()
            # Unbound call: the panel only needs a `self` carrying `.layout`.
            panel.draw(_PanelHost(layout), bpy.context)
            assert layout.calls, f"draw() did nothing for kind {kind}"
    finally:
        if holder is not None:
            bpy.data.objects.remove(holder, do_unlink=True)
        del bpy.types.Object.rif_placeable
        bpy.utils.unregister_class(group)
        bpy.utils.unregister_class(placeables.RIF_SwitchTarget)


class _PanelHost:
    """Minimal `self` for calling a panel's draw() outside Blender's UI."""

    def __init__(self, layout):
        self.layout = layout


class _RecordingLayout:
    """Stand-in for a UILayout that records calls and checks icon names."""

    def __init__(self, record_icons=False):
        self.calls = []
        self.record_icons = record_icons
        self._valid_icons = None
        if record_icons:
            function = bpy.types.UILayout.bl_rna.functions["label"]
            self._valid_icons = {item.identifier
                                 for item in function.parameters["icon"]
                                 .enum_items}

    def reset(self):
        """Forget the recorded calls, keeping the icon set."""
        self.calls = []

    def _check_icon(self, icon):
        if icon is None or not self._valid_icons:
            return
        assert icon in self._valid_icons, \
            f"draw() used an icon Blender does not have: {icon!r}"

    def label(self, text="", icon=None, **_kwargs):
        """Record a label, checking its icon."""
        self._check_icon(icon)
        self.calls.append(("label", text))

    def prop(self, data, name, **_kwargs):
        """Record a property, forcing the field to resolve."""
        if isinstance(name, str):
            getattr(data, name)   # raises when the field does not exist
        self.calls.append(("prop", name))

    def box(self):
        """Return a nested layout that records into the same list."""
        return self

    def row(self, **_kwargs):
        """Return a nested layout that records into the same list."""
        return self

    def column(self, **_kwargs):
        """Return a nested layout that records into the same list."""
        return self

    def separator(self, **_kwargs):
        """Record a separator."""
        self.calls.append(("separator", None))

    def operator(self, idname="", text="", icon=None, **_kwargs):
        """Record an operator entry, checking its icon."""
        self._check_icon(icon)
        self.calls.append(("operator", idname))

    def menu(self, idname="", text="", icon=None, **_kwargs):
        """Record a submenu entry, checking its icon."""
        self._check_icon(icon)
        self.calls.append(("menu", idname))


@test
def test_generator_weights_reach_the_file():
    """A weighted generator gains AVPGENEX > AVPGENNM + GENEXSET.

    The mixed generator was inert in game because ADVANCEDGENERATOR was set but
    the extended settings were never written. This checks the whole chain: the
    extra chunk exists, it sits beside its generator in SPECLOBJ, its name
    matches, and the weights land in the right slots.
    """
    obj = _make_cube()
    spec = {"location": (0, 0, 0), "name": "mixed", "orientation": 0,
            "type": 0, "flags": 0x08003801,
            "weights": {"Alien_Wt": 3, "PredAlien_Wt": 1,
                        "Praetorian_Wt": 0}}
    data = build_rif_level([obj], "lvl", spawns=[spec])

    idx, size = _find_chunk(data, "AVPGENEX")
    assert idx > 0, "AVPGENEX missing for a generator with weights"
    # 12 header + [12 + 8 name] + [12 + 84 settings]. `_find_chunk` reports the
    # whole chunk, so the AVPGENEX header is included; the payload alone is 116.
    assert size == 128, f"AVPGENEX should be 128 bytes for 'mixed', got {size}"

    payload = _chunk_payload(data, "GENEXSET")
    assert payload is not None, "GENEXSET missing inside AVPGENEX"
    # Header: 2x i32, 4x u8, 2x i32 = 20 bytes, then data_size and 15 weights.
    assert len(payload) == 84, f"GENEXSET must be 84 bytes, got {len(payload)}"
    rate, increase = struct.unpack_from("<2i", payload, 0)
    assert (rate, increase) == (0, 0), (rate, increase)
    limit = payload[8]
    assert limit == 0, limit
    data_size = struct.unpack_from("<i", payload, 20)[0]
    assert data_size == 64, f"weights.data_size must be 64, got {data_size}"
    weights = struct.unpack_from("<15i", payload, 24)
    assert weights[11] == 3, f"Alien_Wt should be 3, got {weights[11]}"
    assert weights[12] == 1, f"PredAlien_Wt should be 1, got {weights[12]}"
    assert weights[13] == 0, f"Praetorian_Wt should be 0, got {weights[13]}"
    assert sum(weights) == 4, "the weights must sum to the roll total"

    names = _chunk_payload(data, "AVPGENNM")
    assert names is not None, "AVPGENNM missing"
    assert names.split(b"\x00")[0] == b"mixed", \
        "the name chunk must match the generator, or the engine links nothing"


@test
def test_extra_chunk_follows_a_deduplicated_name():
    """AVPGENEX must carry the name actually written, not the requested one.

    The engine links extra data by string comparison, so a generator renamed to
    avoid a collision needs its settings renamed with it. Otherwise the two
    halves disagree and the weights are silently dropped.
    """
    obj = _make_cube()
    spawns = [{"location": (0, 0, 0), "name": "same", "orientation": 0,
               "type": 0, "flags": 0x08003801,
               "weights": {"Alien_Wt": 1}},
              {"location": (0, 0, 0), "name": "same", "orientation": 0,
               "type": 0, "flags": 0x08003801,
               "weights": {"Alien_Wt": 2}}]
    data = build_rif_level([obj], "lvl", spawns=spawns)

    generator_names = []
    for payload in _all_chunk_payloads(data, "AVPGENER"):
        generator_names.append(payload[28:].split(b"\x00")[0])
    extra_names = []
    for payload in _all_chunk_payloads(data, "AVPGENNM"):
        extra_names.append(payload.split(b"\x00")[0])
    assert len(generator_names) == 2, generator_names
    assert len(extra_names) == 2, extra_names
    assert sorted(generator_names) == sorted(extra_names), (
        f"generators {generator_names} and their extra data {extra_names} "
        "do not line up")


@test
def test_plain_generator_gets_no_extra_chunk():
    """A fixed NPC generator must not gain an AVPGENEX.

    Retail writes 614 of its 1064 fixed-NPC generators as a bare AVPGENER, and
    the drone that worked in game was exactly that. Adding settings would set
    ADVANCEDGENERATOR and change which code path the engine takes.
    """
    obj = _make_cube()
    npc = {"location": (0, 0, 0), "name": "drone", "orientation": 0,
           "type": 1, "flags": 0x00003801}
    data = build_rif_level([obj], "lvl", spawns=[npc])
    assert _find_chunk(data, "AVPGENER")[0] > 0, "the generator is missing"
    assert _find_chunk(data, "AVPGENEX")[0] < 0, \
        "a fixed-NPC generator must stay a bare AVPGENER"
    assert _find_chunk(data, "GENEXSET")[0] < 0, \
        "a fixed-NPC generator must carry no weights"


@test
def test_zero_weight_generator_still_writes_settings():
    """All-zero weights are a real setup, not an omission.

    They mean "use the level's default creature", and the engine can only reach
    that fallback if the extended settings chunk exists. Dropping it when the
    total is zero would break the Default Enemy Generator.
    """
    obj = _make_cube()
    spec = {"location": (0, 0, 0), "name": "default", "orientation": 0,
            "type": 0, "flags": 0x08003801,
            "weights": {"Alien_Wt": 0, "PredAlien_Wt": 0,
                        "Praetorian_Wt": 0}}
    data = build_rif_level([obj], "lvl", spawns=[spec])
    assert _find_chunk(data, "AVPGENEX")[0] > 0, \
        "the extra chunk is required even with no weights"
    payload = _chunk_payload(data, "GENEXSET")
    assert payload is not None, "GENEXSET missing"
    weights = struct.unpack_from("<15i", payload, 24)
    assert sum(weights) == 0, weights


@test
def test_default_enemy_generator_starts_with_no_weights():
    """Placing a Default Enemy Generator must preset every proportion to zero.

    The property group defaults `mix_drone` to 1 so a Mixed generator does
    something useful immediately. That default would be wrong here: it turns the
    fallback into a hard-coded drone generator and ignores GLOGENDC.
    """
    placeables = load_package_module("placeables")
    spec = [s for s in placeables.PLACEABLES
            if s.kind == placeables.KIND_DEFAULT_ENEMY]
    assert spec, "no Default Enemy Generator entry in the menu"
    presets = spec[0].flags
    for field in ("mix_drone", "mix_predalien", "mix_praetorian"):
        assert presets.get(field) == 0, \
            f"{field} must be preset to 0, got {presets.get(field)!r}"


@test
def test_global_generator_panel_fields_reach_the_chunk():
    """The placed marker's budget fields must be what lands in GLOGENDC.

    The panel used to show a static note with no fields at all, so the level's
    NPC cap and rate could not be set from Blender even though the chunk
    supports them.
    """
    placeables = load_package_module("placeables")
    group = placeables.RIF_PlaceableSettings
    # The group carries a collection of switch targets, and Blender will not
    # build it unless the target group is registered first.
    bpy.utils.register_class(placeables.RIF_SwitchTarget)
    bpy.utils.register_class(group)
    bpy.types.Object.rif_placeable = bpy.props.PointerProperty(type=group)
    holder = None
    try:
        # The cube comes first: `_make_cube` clears the scene, which would take
        # the marker with it.
        cube = _make_cube()
        holder = bpy.data.objects.new("GlobalGen", None)
        bpy.context.collection.objects.link(holder)
        # Both must be set: the marker property is what the collector finds the
        # object by, the group field is what it reads. The menu operator sets
        # both together, so a divergence here would be a test artefact.
        holder[placeables.PLACEABLE_PROP] = placeables.KIND_GLOBAL_GENERATOR
        holder.rif_placeable.kind = placeables.KIND_GLOBAL_GENERATOR
        holder.rif_placeable.max_npcs = 7
        holder.rif_placeable.npcs_per_minute = 30
        holder.rif_placeable.npc_acceleration = 5
        holder.rif_placeable.hive_state_change_time = 1000
        holder.rif_placeable.enemy_kind = "marines"

        # The operator reads the marker off the scene, so it must be the active
        # object for `_collect_global_generator` to find the settings.
        bpy.context.view_layer.objects.active = holder
        operators = load_operators()
        overrides = operators._collect_global_generator(bpy.context)
        assert overrides is not None, "the marker was not collected"
        assert overrides["max_npcs"] == 7, overrides
        assert overrides["npcs_per_minute"] == 30, overrides
        assert overrides["enemy_generated"] == 1, \
            "marines must map to EnemyGenerated = 1"

        cube = _make_cube()
        data = build_rif_level([cube], "lvl", spawns=[{
            "location": (0, 0, 0), "name": "gen1", "orientation": 0,
            "type": 1, "flags": NPC_FLAGS}],
            global_generator=overrides)
        idx, _size = _find_chunk(data, "GLOGENDC")
        assert idx > 0, "GLOGENDC missing"
        fields = [_read_i32(data, idx + 12 + 4 * i) for i in range(7)]
        assert fields[0] == 1, f"enemy_generated is {fields[0]}, expected 1"
        assert fields[1] == 7, f"MaxNPCSOnLevel is {fields[1]}, expected 7"
        assert fields[2] == 30, f"NPCSPerMinute is {fields[2]}, expected 30"
        assert fields[4] == 1000, f"HiveStateChangeTime is {fields[4]}"
    finally:
        try:
            if holder is not None:
                bpy.data.objects.remove(holder, do_unlink=True)
        except (ReferenceError, RuntimeError):
            pass
        del bpy.types.Object.rif_placeable
        bpy.utils.unregister_class(group)
        bpy.utils.unregister_class(placeables.RIF_SwitchTarget)


@test
def test_module_count_matches_objects():
    """Each mesh becomes its own RBOBJECT module, and that is not incidental.

    The engine refuses to generate an NPC while the generator's module is
    visible (`BH_GENER.C:227`), so a single-object level can never spawn from a
    weighted generator: the player is inside the only module there is. Knowing
    the count is what makes that diagnosable.
    """
    _clear_scene()
    one = _make_cube("Room")
    data = build_rif_level([one], "one", spawns=[])
    assert _find_all_chunks(data, "RBOBJECT").__len__() == 1, \
        "one object should produce one module"

    _clear_scene()
    two = _make_cubes(2)
    data = build_rif_level(two, "two", spawns=[])
    assert len(_find_all_chunks(data, "RBOBJECT")) == 2, \
        "two objects should produce two modules"


class _ReportRecorder:
    """Minimal operator stand-in that records report() calls."""

    def __init__(self):
        self.reports = []

    def report(self, levels, message):
        """Record one report call."""
        self.reports.append((tuple(levels), message))

    def warnings(self):
        """Return the messages reported at WARNING level."""
        return [m for levels, m in self.reports if "WARNING" in levels]


@test
def test_player_start_comes_only_from_a_placed_object():
    """The spawn must come from a Player Start object, and from nothing else.

    Three settings used to describe this one point, resolved in an order where
    the default shadowed the coordinate fields. The check that matters is that
    removing them left exactly one source.
    """
    operators = load_operators()
    placeables = load_package_module("placeables")

    _clear_scene()
    assert operators._resolve_player_start(bpy.context) is None, \
        "an empty scene must yield no player start"

    marker = bpy.data.objects.new("PlayerStart", None)
    bpy.context.collection.objects.link(marker)
    marker[operators.MARKER_PROP] = operators.PLAYER_START_MARKER
    marker.location = (1.5, 2.5, 3.5)
    # The resolver reads matrix_world, which background mode does not recompute
    # on its own, so without this it sees the position from before the line
    # above. Every other test that reads a world transform does the same.
    bpy.context.view_layer.update()
    found = operators._resolve_player_start(bpy.context)
    assert found == (1.5, 2.5, 3.5), found
    assert placeables.PLAYER_START_MARKER == operators.PLAYER_START_MARKER, \
        "the marker string must agree between the menu and the exporter"


@test
def test_missing_player_start_is_reported():
    """A level with no way in must be reported, not exported in silence.

    The old "Safe Spawn" toggle placed the start automatically and said nothing,
    so a level whose geometry it mis-measured looked fine until it was played.
    """
    operators = load_operators()
    recorder = _ReportRecorder()

    # No start and no multiplayer spawn: this is the case worth warning about.
    operators._report_player_start(recorder, None, [])
    assert recorder.warnings(), "a level with no way in must warn"
    message = recorder.warnings()[0]
    assert "world origin" in message, message

    # A start exists: nothing to say.
    recorder = _ReportRecorder()
    operators._report_player_start(recorder, (0.0, 0.0, 0.0), [])
    assert not recorder.reports, recorder.reports

    # Multiplayer spawns are a legitimate way in, so this is information only.
    recorder = _ReportRecorder()
    operators._report_player_start(recorder, None,
                                   [{"flags": 0x48002801}])
    assert not recorder.warnings(), \
        "multiplayer spawns must not be reported as a problem"
    assert recorder.reports, "it should still be mentioned"


class _FakeExportSettings:
    """Stands in for the export operator, which Blender will not let us create.

    `RIF_OT_export()` raises "bpy_struct.__new__(struct): expected a single
    argument" in real Blender — the offline stub allowed it, so this is another
    place the stub was more permissive than Blender. The layout lives in a
    module-level function precisely so it can be exercised with this stand-in.
    """

    def __init__(self, mode="LEVEL", custom_name=False):
        self.export_mode = mode
        self.link_mode = "OUTLINER"
        self.use_custom_name = custom_name
        self.level_name = "custom"
        self.global_scale = 1.0
        self.override_shape_flags = False
        self.shape_flags_override = 0
        self.export_selected_only = False


def _draw_export(module, mode, custom_name=False):
    """Return the property names the export layout draws for one mode."""
    layout = _RecordingLayout(record_icons=True)
    module._draw_export_settings(
        layout, _FakeExportSettings(mode, custom_name=custom_name))
    return [name for kind, name in layout.calls if kind == "prop"]


def _export_rna():
    """Return the export operator's settings, keyed by identifier.

    Two things have to be right, and both cost a round of debugging:

    * the class must be registered, or `bpy.ops.export_scene.rif` does not
      resolve at all;
    * the settings are NOT in `cls.bl_rna.properties`. That is the RNA of the
      operator *type* and lists only base fields (`bl_idname`, `bl_options`, …)
      even after registration. They live behind the operator itself, via
      `bpy.ops.export_scene.rif.get_rna_type().properties`.
    """
    operators = load_operators()
    cls = operators.RIF_OT_export
    if not cls.is_registered:
        bpy.utils.register_class(cls)
    rna = bpy.ops.export_scene.rif.get_rna_type()
    return {prop.identifier: prop for prop in rna.properties}


@test
def test_export_panel_is_sectioned_and_mode_aware():
    """The export settings must be grouped, and hide what a mode ignores.

    Blender lists an operator's properties flat, in declaration order, unless
    `draw()` says otherwise. `Level Name` is written only in Level mode, so
    showing it in Single Shape or Multi-Object invites the user to fill in a
    field that is silently discarded.
    """
    operators = load_operators()
    for mode in ("SHAPE", "COLLECTION", "LEVEL"):
        drawn = _draw_export(operators, mode)
        assert drawn, f"nothing drawn for mode {mode}"
        assert "export_mode" in drawn, drawn
        if mode == "LEVEL":
            # The name follows the scene by default, so the switch is always
            # there and the field only when it is turned on.
            assert "link_mode" in drawn, (
                f"Level mode must offer the link mode: {drawn}")
            assert "use_custom_name" in drawn, (
                f"Level mode must offer the custom-name switch: {drawn}")
            assert "level_name" not in drawn, (
                f"Level Name must stay hidden until Custom Name is on: {drawn}")
        else:
            assert "use_custom_name" not in drawn, (
                f"{mode} does not write an environment, so Custom Name must "
                f"not be offered: {drawn}")
            assert "level_name" not in drawn, (
                f"{mode} does not write an environment, so Level Name must "
                f"not be offered: {drawn}")

    custom = _draw_export(operators, "LEVEL", custom_name=True)
    assert "level_name" in custom, (
        f"Custom Name must reveal the field: {custom}")

    # Every name the layout uses has to be a real property of the operator, or
    # the panel raises on draw and Blender swallows the exception.
    available = set(_export_rna())
    used = set(_draw_export(operators, "LEVEL"))
    missing = sorted(n for n in used if n not in available)
    assert not missing, f"the layout uses unknown properties: {missing}"


@test
def test_export_defaults_match_the_intended_workflow():
    """A level export must be the default, and the name must be memorable."""
    props = _export_rna()
    assert props["export_mode"].default == "LEVEL", \
        "exporting a level should not need the mode changed first"
    assert props["level_name"].default == "custom", \
        props["level_name"].default
    assert abs(props["global_scale"].default - 1.0) < 1e-9, \
        "unscaled must be the default, so no ENVSDSCL is written"


@test
def test_global_scale_is_written_as_a_double_only_when_set():
    """Global Scale maps to ENVSDSCL, an 8-byte double, and 1.0 writes nothing.

    Retail scales five levels by 0.8 and 0.9 and omits the chunk everywhere
    else, so "unscaled" is the absence of the chunk rather than a stored 1.0.
    """
    obj = _make_cube()
    data = build_rif_level([obj], "lvl", global_scale=None)
    assert _find_chunk(data, "ENVSDSCL")[0] < 0, \
        "no scale given must write no chunk"

    data = build_rif_level([obj], "lvl", global_scale=1.0)
    idx, size = _find_chunk(data, "ENVSDSCL")
    assert idx > 0, "an explicit 1.0 is still written when asked for"
    assert size == 20, f"ENVSDSCL is 12 header + 8 payload, got {size}"

    data = build_rif_level([obj], "lvl", global_scale=0.8)
    idx, _size = _find_chunk(data, "ENVSDSCL")
    payload = _chunk_payload(data, "ENVSDSCL")
    value = struct.unpack("<d", payload)[0]
    assert abs(value - 0.8) < 1e-12, f"expected 0.8, read {value!r}"


def _vmdarray_peers(data):
    """Return the peer indices listed by each VMDARRAY, in file order."""
    result = []
    for payload in _all_chunk_payloads(data, "VMDARRAY"):
        count = struct.unpack_from("<I", payload, 0)[0]
        result.append([struct.unpack_from("<4i", payload, 4 + 16 * k)[0]
                       for k in range(count)])
    return result


def _adjmdlep_entries(data):
    """Return [(peer, flags, (x, y, z)), ...] for each ADJMDLEP."""
    result = []
    for payload in _all_chunk_payloads(data, "ADJMDLEP"):
        count = struct.unpack_from("<I", payload, 0)[0]
        entries = []
        for k in range(count):
            index, flags = struct.unpack_from("<2i", payload, 4 + 20 * k)
            point = struct.unpack_from("<3i", payload, 12 + 20 * k)
            entries.append((index, flags, point))
        result.append(entries)
    return result


def _cubes_at(locations):
    """Create one unit cube per location; return them in order.

    _make_cube clears the scene, so building a multi-object level with it
    destroys each previous cube. Both cubes come from one _make_cubes call,
    then get moved, and the depsgraph is updated so matrix_world reflects the
    new positions.
    """
    objs = _make_cubes(len(locations))
    for obj, location in zip(objs, locations):
        obj.location = location
    bpy.context.view_layer.update()
    return objs


@test
def test_adjmdlep_links_modules_that_share_a_wall():
    """Abutting modules become neighbours, with an entry point on the seam.

    `ADJMDLEP` is the AI graph, separate from visibility. Without it NPCs have
    no path anywhere, which is the second half of why generators produced
    nothing: the chunk used to be written empty.
    """
    # Unit cubes at 0 and 1 m: their boxes meet at x = 0.5 m.
    first, second = _cubes_at([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    data = build_rif_level([first, second], "lvl")
    entries = _adjmdlep_entries(data)
    assert len(entries) == 2, entries
    for index, listed in enumerate(entries):
        peers = [peer for peer, _flags, _point in listed]
        other = 1 - index
        assert other in peers, f"module {index} did not link to {other}: {listed}"
    # The point is relative to the PEER's origin and must land INSIDE the peer.
    # Two unit cubes centred 1 m apart abut at x = 500 mm, and a point exactly
    # on that plane is in the wall: an NPC sent there walks into masonry and
    # stops, which is what happened before the clamp existed.
    peer, flags, point = entries[0][0]
    assert peer == 1, entries[0]
    assert flags == 0, f"a plain link carries no flags, got {flags}"
    # Arrival is 50 mm inside cube B, whose origin is at (1000, 0, 0) mm.
    assert point == (-450, 0, 0), f"arrival should clear the wall: {point}"
    peer_origin = (1000, 0, 0)
    absolute = tuple(peer_origin[axis] + point[axis] for axis in range(3))
    assert absolute == (550, 0, 0), absolute  # cube B spans 500..1500 mm in x
    print(f"    przybycie do B: {absolute} mm (wewnatrz bryly B)")


@test
def test_adjmdlep_ignores_geometry_that_only_grazes():
    """Edge and corner contacts are not passages.

    Two modules that touch along a line share no walkable opening, and linking
    them would let an NPC path through a wall.
    """
    # Offset diagonally: the boxes then meet along one axis only.
    first, corner = _cubes_at([(0.0, 0.0, 0.0), (1.0, 1.0, 0.0)])
    data = build_rif_level([first, corner], "lvl")
    for index, listed in enumerate(_adjmdlep_entries(data)):
        assert not listed, (
            f"module {index} linked across a corner contact: {listed}")

    # A module far away must not link either.
    near, far = _cubes_at([(0.0, 0.0, 0.0), (20.0, 0.0, 0.0)])
    data = build_rif_level([near, far], "lvl")
    for entries in _adjmdlep_entries(data):
        assert not entries, entries


@test
def test_vmdarray_follows_the_outliner_hierarchy():
    """Parenting in the Outliner is the visibility graph.

    A module sees its parent and its children, and never itself.
    """
    objs = _make_cubes(3)
    data = build_rif_level(objs, "lvl", hierarchy={"Cube0": "Cube1",
                                                   "Cube2": "Cube1"})
    peers = _vmdarray_peers(data)
    assert len(peers) == 3, peers
    assert sorted(peers[0]) == [1], f"Cube0: {peers[0]}"
    assert sorted(peers[1]) == [0, 2], f"Cube1: {peers[1]}"
    assert sorted(peers[2]) == [1], f"Cube2: {peers[2]}"
    print(f"    hierarchia -> widocznosc: {peers}")


@test
def test_module_never_lists_itself():
    """No module may list itself, which is what every retail module does.

    Across all 63 retail levels and 12,805 modules, not one lists itself. Our
    exporter used to put self first, and a module marked visible that way never
    receives a map, so it silently stops drawing. The engine marks the player's
    module on its own (MODULE.C:132).
    """
    objs = _make_cubes(3)
    data = build_rif_level(objs, "lvl", hierarchy={"Cube0": "Cube1",
                                                   "Cube2": "Cube1"})
    for index, listed in enumerate(_vmdarray_peers(data)):
        assert index not in listed, (
            f"module {index} lists itself: {listed}")


@test
def test_unlinked_module_lists_nothing():
    """A module outside the tree sees nothing and renders only from inside.

    That is how a generator space stays out of sight, and it matches retail:
    the list holds other visible modules, never the module's own index.
    """
    objs = _make_cubes(3)
    data = build_rif_level(objs, "lvl", hierarchy={"Cube0": "Cube1"})
    peers = _vmdarray_peers(data)
    assert peers[2] == [], f"an unlinked module lists nothing: {peers[2]}"
    for index in (0, 1):
        assert 2 not in peers[index], (
            f"module {index} must not list the unlinked module: {peers[index]}")
    print(f"    modul niepodpiety nie wymienia nikogo: {peers}")


@test
def test_no_hierarchy_leaves_every_module_with_no_list():
    """Without parenting no module names any other, so each draws alone.

    Exporting warns about it, because a level that used to draw in full would
    silently turn into single-room rendering.
    """
    objs = _make_cubes(3)
    data = build_rif_level(objs, "lvl")
    for index, listed in enumerate(_vmdarray_peers(data)):
        assert listed == [], (index, listed)


def _module_world_boxes(data):
    """Return [(origin, (min), (max))] per module, in world RIF mm.

    Origin comes from OBJHEAD1, bounds from SHPHEAD1, paired through
    shape_id_no. Used to prove that a change of representation leaves the
    module exactly where it was.
    """
    shapes = {}
    for payload in _all_chunk_payloads(data, "SHPHEAD1"):
        file_id = struct.unpack_from("<i", payload, 20)[0]
        bounds = struct.unpack_from("<6i", payload, 36)
        shapes[file_id] = ((bounds[1], bounds[3], bounds[5]),
                           (bounds[0], bounds[2], bounds[4]))
    out = []
    for payload in _all_chunk_payloads(data, "OBJHEAD1"):
        origin = struct.unpack_from("<3i", payload, 20)
        shape_id = struct.unpack_from("<i", payload, 56)[0]
        low, high = shapes[shape_id]
        out.append((origin,
                    tuple(origin[a] + low[a] for a in range(3)),
                    tuple(origin[a] + high[a] for a in range(3))))
    return out


@test
def test_parenting_does_not_move_modules():
    """The Outliner tree links visibility; it must not relocate geometry.

    Parenting changes an object's matrix_local, which is relative to its
    parent. Writing that as the module's world position puts the module
    somewhere else entirely, which is how Origin to Geometry on a parented
    object produced overlapping modules. Positions must come from
    matrix_world, so the exported boxes are identical with and without the
    tree.
    """
    objs = _make_cubes(3)
    positions = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (4.0, 0.0, 0.0)]
    for obj, location in zip(objs, positions):
        obj.location = location
    bpy.context.view_layer.update()

    flat = _module_world_boxes(build_rif_level(objs, "lvl"))
    linked = _module_world_boxes(build_rif_level(
        objs, "lvl", hierarchy={"Cube0": "Cube1", "Cube2": "Cube1"}))
    assert flat == linked, (
        f"parenting moved geometry:\n  bez drzewa: {flat}\n  z drzewem : {linked}")


@test
def test_parented_module_export_keeps_its_world_position():
    """A child object is placed by its world transform, not its local one.

    The parent sits away from the origin, so a module that used the local
    transform would land next to its parent instead of where it belongs.
    """
    parent, child = _cubes_at([(0.0, 0.0, 0.0), (4.0, 0.0, 0.0)])
    child.parent = parent
    bpy.context.view_layer.update()

    boxes = _module_world_boxes(build_rif_level([parent, child], "lvl"))
    # Cubes are 1 m across, so the child spans 3500..4500 mm in x.
    child_low = boxes[1][1]
    assert child_low[0] == 3500, (
        f"child must stay at its world position, got {child_low}")
    print(f"    dziecko na {boxes[1][0]} (swiat), nie przy rodzicielu")


@test
def test_offcentre_mesh_is_recentred_into_the_retail_frame():
    """Geometry is stored around the origin, with the position in location.

    Retail always keeps shape vertices LOCAL and carries the module position in
    object_data.location. World-space vertices with a zero location make the
    engine read m_world as the world origin for every module, which is what
    stops a module drawing. The world box must not move.
    """
    first, second = _cubes_at([(0.0, 0.0, 0.0), (4.0, 0.0, 0.0)])
    # Move the MESH, not the object: vertices end up far from the origin while
    # matrix_world stays put, exactly like a world-space mesh.
    first.data.transform(Matrix.Translation(Vector((16.0, 0.0, 0.0))))
    bpy.context.view_layer.update()

    origin, low, high = _module_world_boxes(
        build_rif_level([first, second], "lvl"))[0]
    assert origin[0] == 16000, f"location must carry the position: {origin}"
    assert origin[0] - low[0] == high[0] - origin[0], (
        f"local bounds must straddle zero: {(low, high)}")
    local_low = tuple(low[a] - origin[a] for a in range(3))
    local_high = tuple(high[a] - origin[a] for a in range(3))
    assert local_low == (-500, -500, -500), local_low
    assert local_high == (500, 500, 500), local_high
    print(f"    lokalne {local_low}..{local_high}  location={origin}")


@test
def test_recentring_survives_parenting():
    """Recentring, world positions and the Outliner tree must all hold at once.

    The three interact: parenting changes matrix_local, recentring changes
    location, and only matrix_world keeps geometry where the artist put it.
    """
    trio = _cubes_at([(0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (9.0, 0.0, 0.0)])
    parent, child = trio[0], trio[1]
    child.data.transform(Matrix.Translation(Vector((2.0, 0.0, 0.0))))
    child.parent = parent
    bpy.context.view_layer.update()

    flat = _module_world_boxes(build_rif_level(trio, "lvl"))
    linked = _module_world_boxes(build_rif_level(
        trio, "lvl", hierarchy={"Cube0": "Cube1"}))
    assert flat == linked, f"parenting moved geometry: {flat} vs {linked}"
    # The child sits at 4 m and its mesh is shifted a further 2 m.
    assert flat[1][0][0] == 6000, flat[1]
    assert flat[1][1][0] == 5500, flat[1]


@test
def test_export_takes_the_whole_scene_by_default():
    """A level is the scene; a stray selection must not shrink the export.

    Exporting only what happened to be selected drops rooms silently, and the
    level then looks broken in game with nothing in Blender to explain it.
    """
    operators = load_operators()
    trio = _cubes_at([(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (4.0, 0.0, 0.0)])
    host = _PanelHost(None)
    host.scene = bpy.context.scene
    host.selected_objects = [trio[0]]

    everything = operators._gather_meshes(host, False)
    assert len(everything) == 3, [o.name for o in everything]
    only_selected = operators._gather_meshes(host, True)
    assert [o.name for o in only_selected] == [trio[0].name], only_selected

    # Order is by name, so object indices and module links are reproducible.
    assert [o.name for o in everything] == sorted(o.name for o in everything)


@test
def test_validator_reports_a_single_module_level():
    """One module matters only when something has to spawn in it.

    The engine will not create an NPC while the generator's module is visible,
    and with a single module everything is always visible -- so the caveat is
    real, but only when the scene holds a generator. A lone module is otherwise
    a level the engine loads and draws, so warning on it said "this is wrong"
    about a block-out that is merely unfinished.
    """
    operators = load_operators()
    _make_cube("Only")

    problems = operators._module_layout_problems(
        list(bpy.context.scene.objects), None)
    assert not problems, f"a lone module with no generator: {problems}"
    print("    sam modul, bez generatora: brak ostrzezen")


@test
def test_validator_reports_single_module_with_a_generator():
    """The spawn caveat appears once a generator exists to be blocked."""
    operators = load_operators()
    _make_cube("Only")
    # The marker is what `_has_generators` looks for; setting it directly keeps
    # the test about the validator rather than about a menu entry's name.
    placeables = load_package_module("placeables")
    bpy.data.objects["Only"][placeables.PLACEABLE_PROP] = placeables.KIND_NPC

    problems = operators._module_layout_problems(
        list(bpy.context.scene.objects), None)
    assert problems, "a one-module level with a generator must be reported"
    assert "one module" in problems[0].lower(), problems[0]
    print(f"    walidator: {problems[0][:90]}")


# ------------------------------------------------------------------ runner

def _suspend_viewport_shading():
    """Switch viewports to a shading mode that keeps no render batches.

    Returns a list of (space, previous_type) to hand back to
    `_restore_viewport_shading`.

    The suite creates and deletes hundreds of objects in a few seconds. In
    RENDERED or MATERIAL shading each one builds GPU render batches, and tearing
    those down while the viewport draws crashes Blender inside
    `DRW_cache_free_old_batches` / `DEG_iterator_objects_next` with no Python
    frame in the report. WIREFRAME keeps no such cache, so the churn becomes
    harmless. Blender in background mode has no viewports and this is a no-op.
    """
    saved = []
    try:
        windows = bpy.context.window_manager.windows
    except AttributeError:
        return saved
    for window in windows:
        for area in getattr(window.screen, "areas", []):
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if getattr(space, "type", None) != "VIEW_3D":
                    continue
                try:
                    previous = space.shading.type
                    space.shading.type = "WIREFRAME"
                    saved.append((space, previous))
                except (AttributeError, TypeError):
                    continue
    return saved


def _restore_viewport_shading(saved):
    """Put each viewport's original shading mode back."""
    for space, previous in saved:
        try:
            space.shading.type = previous
        except (AttributeError, ReferenceError, TypeError):
            pass


def run_all():
    """Run all registered tests, printing PASS/FAIL/SKIP per test.

    Deliberately does NOT call _clear_scene() in a finally block: deleting
    Blender objects that may still be referenced by a running operator can
    trigger an access violation in MSVCP140.dll. Each test helper does its
    own scene cleanup at the start instead.

    Viewport shading is suspended for the duration because the object churn
    otherwise crashes Blender while it draws; see `_suspend_viewport_shading`.
    """
    if not HAS_BPY:
        print("bpy not importable — run this script inside Blender.")
        return False

    saved_shading = _suspend_viewport_shading()

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
    _restore_viewport_shading(saved_shading)
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