"""RIF exporter round-trip test.

Run from Blender's Text Editor:  press "Run Script".
Or headless:                     blender --background --python tests/blender_roundtrip.py
"""
import importlib
import struct
import sys

import addon_utils
import bpy


# ---------------------------------------------------------------- bootstrap

def _rif_pkg():
    """Return the rif_exporter.rif package, resolving bl_ext.<repo> prefix."""
    for mod in addon_utils.modules():
        if mod.__name__.endswith("rif_exporter"):
            return importlib.import_module(mod.__name__ + ".rif")
    return importlib.import_module("rif_exporter.rif")


# ---------------------------------------------------------------- parsing

def _find_chunk(data: bytes, ident: bytes) -> int:
    """Return offset of ident in buffer, or -1."""
    return data.find(ident)


def _parse_shphead1(data: bytes) -> dict:
    """Parse the fixed 68-byte prefix of SHPHEAD1 from a REBINFF2 buffer."""
    off = _find_chunk(data, b"SHPHEAD1")
    if off < 0:
        raise AssertionError("SHPHEAD1 not found in buffer")
    p = off + 12
    flags, = struct.unpack_from("<I", data, p + 0)
    lock = data[p + 4 : p + 16].rstrip(b"\0").decode("ascii")
    file_id, num_sub, num_v, num_p = struct.unpack_from("<4I", data, p + 16)
    radius, = struct.unpack_from("<f", data, p + 32)
    # Fields at 0x24..0x38 are interleaved per axis: max.x, min.x, max.y, ...
    mx, mnx, my, mny, mz, mnz = struct.unpack_from("<6i", data, p + 36)
    version, num_as_obj = struct.unpack_from("<2I", data, p + 60)
    return dict(
        flags=flags, lock_user=lock,
        file_id_num=file_id, num_subshapes=num_sub,
        num_verts=num_v, num_polys=num_p,
        radius=radius,
        max=(mx, my, mz), min=(mnx, mny, mnz),
        version_no=version, num_as_obj=num_as_obj,
    )


def _parse_shppolys(data: bytes):
    """Return list of dicts, one per polygon record in the first SHPPOLYS."""
    off = _find_chunk(data, b"SHPPOLYS")
    if off < 0:
        raise AssertionError("SHPPOLYS not found in buffer")
    size, = struct.unpack_from("<I", data, off + 8)
    payload = data[off + 12 : off + size]
    n, = struct.unpack_from("<I", payload, 0)
    out = []
    for i in range(n):
        base = 4 + i * 36
        engine, normal_idx, flags, colour = \
            struct.unpack_from("<4I", payload, base)
        verts = struct.unpack_from("<5i", payload, base + 16)
        out.append({
            "engine": engine, "normal_idx": normal_idx,
            "flags": flags, "colour": colour, "verts": verts,
        })
    return out


def _uv_payload_max(data: bytes) -> float:
    """Return the largest 32-bit float found in the SHPUVCRD payload."""
    off = _find_chunk(data, b"SHPUVCRD")
    if off < 0:
        return 0.0
    size, = struct.unpack_from("<I", data, off + 8)
    payload = data[off + 12 : off + size]
    n = (len(payload) - 4) // 4
    floats = struct.unpack_from(f"<{n}f", payload, 4)
    finite = [f for f in floats if -1e6 < f < 1e6]
    return max(finite) if finite else 0.0


def _decode_uv_index(poly_colour: int) -> int:
    """Mirror of community importer's uv_index_from_poly (config.py)."""
    if poly_colour & 0x0000F000:
        return ((poly_colour & 0x0000F000) << 4) | (poly_colour >> 16)
    return poly_colour >> 16


# ---------------------------------------------------------------- fixtures

def _make_cube(size_m: float = 1.0):
    """Return a fresh mesh object: axis-aligned cube of given edge length."""
    bpy.ops.mesh.primitive_cube_add(size=size_m, location=(0, 0, 0))
    obj = bpy.context.view_layer.objects.active
    assert obj and obj.type == "MESH"
    return obj


# ---------------------------------------------------------------- tests

def test_buffer_starts_with_rebinff2():
    """Smoke: writer emits a REBINFF2 root identifier."""
    rif = _rif_pkg()
    obj = _make_cube()
    data = rif.builder.build_rif_bytes(obj.data, object_name="TestCube")
    assert data[:8] == b"REBINFF2", data[:8]
    print(f"  ok  root={data[:8].decode()} size={len(data)} B")


def test_shphead1_bounds_match_metres_to_rif():
    """Calibration: bounds in SHPHEAD1 equal meters_to_rif(vertex) range."""
    rif = _rif_pkg()
    obj = _make_cube(size_m=1.616)        # half-extent 0.808 m → ±808 mm
    data = rif.builder.build_rif_bytes(obj.data, object_name="TestCube")
    h = _parse_shphead1(data)
    expected_hi = rif.types.meters_to_rif(0.808)
    expected_lo = rif.types.meters_to_rif(-0.808)
    assert h["max"] == (expected_hi, expected_hi, expected_hi), h["max"]
    assert h["min"] == (expected_lo, expected_lo, expected_lo), h["min"]
    print(f"  ok  bounds ±{expected_hi} int (calibration target ±808)")


def test_shphead1_radius_matches_bounds_diagonal():
    """Radius from SHPCENTR must equal the AABB half-diagonal in int units."""
    rif = _rif_pkg()
    obj = _make_cube(size_m=1.616)
    data = rif.builder.build_rif_bytes(obj.data, object_name="TestCube")
    h = _parse_shphead1(data)
    hi = h["max"][0]
    expected = (3.0 ** 0.5) * hi
    assert abs(h["radius"] - expected) < 1e-3, (h["radius"], expected)
    print(f"  ok  radius={h['radius']:.3f}  expected={expected:.3f}")


def test_shphead1_counts_match_mesh():
    """num_verts / num_polys in SHPHEAD1 match the source mesh."""
    rif = _rif_pkg()
    obj = _make_cube()
    data = rif.builder.build_rif_bytes(obj.data, object_name="TestCube")
    h = _parse_shphead1(data)
    assert h["num_verts"] == len(obj.data.vertices)
    assert h["num_polys"] == len(obj.data.polygons)
    print(f"  ok  verts={h['num_verts']} polys={h['num_polys']}")


def test_uv_max_under_pixel_span():
    """All UV components must be in [0, DEFAULT_UV_PIXELS]."""
    rif = _rif_pkg()
    obj = _make_cube()
    data = rif.builder.build_rif_bytes(obj.data, object_name="TestCube")
    hi = _uv_payload_max(data)
    assert 0.0 <= hi <= rif.types.UV_PIXEL_MAX, hi
    print(f"  ok  uv_max={hi:.3f}  (limit {rif.types.UV_PIXEL_MAX})")


def test_poly_colour_encodes_uv_index():
    """uv_index_from_poly must return poly_idx for every polygon (desk1 conv)."""
    rif = _rif_pkg()
    obj = _make_cube()
    data = rif.builder.build_rif_bytes(obj.data, object_name="TestCube")
    polys = _parse_shppolys(data)
    for i, p in enumerate(polys):
        uv_idx = _decode_uv_index(p["colour"])
        assert uv_idx == i, f"poly {i}: uv_idx decoded as {uv_idx}"
    print(f"  ok  uv_idx==poly_idx for {len(polys)} polys")


def test_uv_roundtrip_is_lossless_at_edges():
    """u*128 with 1/128 importer scale must round-trip u=0 and u=1 exactly."""
    rif = _rif_pkg()
    for u in (0.0, 0.25, 0.5, 0.75, 1.0):
        px, _ = rif.types.uv_to_rif_pixels(u, 0.0)
        back = px / rif.types.DEFAULT_UV_PIXELS
        assert abs(back - u) < 1e-9, f"u={u} -> {px} -> {back}"
    print("  ok  UV round-trip lossless at edges")


def test_roundtrip_via_community_importer():
    """Optional: import the emitted file via community importer, verify mesh."""
    rif = _rif_pkg()
    try:
        import io_scene_rif  # noqa: F401  (community importer)
    except ImportError:
        print("  skip community importer not installed")
        return

    obj = _make_cube(size_m=1.616)
    data = rif.builder.build_rif_bytes(obj.data, object_name="TestCube")

    tmp = "/tmp/_rif_roundtrip.rif"
    with open(tmp, "wb") as fh:
        fh.write(data)

    before = len(bpy.data.objects)
    bpy.ops.import_scene.rif(filepath=tmp)
    imported = bpy.data.objects[-1] if len(bpy.data.objects) > before else None
    assert imported and imported.type == "MESH"
    assert len(imported.data.vertices) == 8
    assert len(imported.data.polygons) == 6
    print(f"  ok  importer produced mesh with {len(imported.data.vertices)} verts")


# ---------------------------------------------------------------- runner

ALL_TESTS = [
    test_buffer_starts_with_rebinff2,
    test_shphead1_bounds_match_metres_to_rif,
    test_shphead1_radius_matches_bounds_diagonal,
    test_shphead1_counts_match_mesh,
    test_uv_max_under_pixel_span,
    test_poly_colour_encodes_uv_index,
    test_uv_roundtrip_is_lossless_at_edges,
    test_roundtrip_via_community_importer,
]


def run_all() -> int:
    """Execute every test, print a summary, return the failure count."""
    print("=" * 60)
    print("RIF exporter — round-trip test suite")
    print("=" * 60)
    failures = 0
    for fn in ALL_TESTS:
        name = fn.__name__
        try:
            print(f"[ RUN  ] {name}")
            fn()
        except Exception as exc:                      # noqa: BLE001
            failures += 1
            print(f"[ FAIL ] {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"[ PASS ] {name}")
    print("-" * 60)
    print(f"{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return failures


if __name__ == "__main__":
    n = run_all()
    if n and not bpy.app.background:
        sys.stderr.write(f"{n} test(s) failed\n")