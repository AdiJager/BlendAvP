"""Read the game's shape files and turn them into Blender meshes.

A `.rif` holds three kinds of thing -- levels, shapes and animated models -- and
`shape_rifs` holds the second: one model per file, a handful of shapes each, with
its own object placement. The chunk layouts here are the same ones the exporter
writes, read backwards:

    SHPHEAD1   +20 file id, +24 verts, +28 polys, +32 radius, +36 bounds
    SHPRAWVT   x, y, z as int32, 12 bytes per vertex, millimetres
    SHPPOLYS   engine_type, normal_index, flags, colour, up to four indices
    SHPUVCRD   a 4-byte polygon count, then per polygon a count and that many
               (u, v) float pairs -- in texture pixels, not in 0..1

Everything else in a shape -- the normals, the centre, the animation data, the
merge hints -- is either recomputable or irrelevant to a static import, so it is
skipped. An unknown chunk is skipped too, because one of the 322 shipped files
(`sulacofrags`) ends with a chunk whose identifier is a single byte and the rest
of that file is fine.
"""
from __future__ import annotations

import struct

VERTEX_SIZE = 12
POLY_SIZE = 36
UV_HEADER_SIZE = 4

# UVs are stored in texture pixels, and the divisor is the texture's size. The
# BMPNAMES entry carries it in its d3 word, whose two halves read 0x0080 each --
# 128 x 128. Dividing by 256 instead halved every island, so they covered only a
# quarter of the UV space and the texture came out at half scale.
UV_PIXELS_PER_UNIT = 128.0


def _u32(data, offset):
    """Read a little-endian unsigned 32-bit value."""
    return struct.unpack_from("<I", data, offset)[0]


def _i32(data, offset):
    """Read a little-endian signed 32-bit value."""
    return struct.unpack_from("<i", data, offset)[0]


def children(data, start, end):
    """Yield (identifier, payload_start, payload_end) for one container level."""
    offset = start
    while offset + 12 <= end:
        ident = data[offset:offset + 8].rstrip(b"\x00").decode("ascii",
                                                                "replace")
        size = _u32(data, offset + 8)
        if size < 12 or offset + size > end:
            return
        yield ident, offset + 12, offset + size
        offset += size


def rif_to_blender(x, y, z):
    """Convert a RIF position to Blender's axes.

    `blender_to_rif_axis` in the exporter is `(x, -z, y)`, so the inverse is
    `(x, z, -y)`. Using the same rule in both directions keeps a round trip
    identical.
    """
    return x, z, -y


def rif_normal_to_blender(x, y, z):
    """Convert a RIF normal to Blender's axes, the same rule as positions."""
    return x, z, -y


class Shape:
    """One shape from a file: geometry, polygons and the bitmap names."""

    def __init__(self, name):
        self.name = name
        self.vertices = []
        self.polygons = []
        # One bitmap index per polygon, from poly_colour.
        self.polygon_bitmaps = []
        # Which SHPUVCRD record this polygon uses. Records are shared, so this
        # is an index into the UV list and not a position in it.
        self.polygon_uv_indexes = []
        # Per-polygon normals as floats. They are not optional decoration: the
        # engine's winding is not guaranteed to match Blender's, so these are
        # what decides whether a face is turned the right way.
        self.polygon_normals = []
        # Per-vertex normals. A face whose corners share one is meant to be flat;
        # where they differ the surface is curved.
        self.vertex_normals = []
        self.uvs = []
        self.bitmaps = []

    @property
    def is_empty(self):
        """True when the shape has no geometry worth importing."""
        return not self.vertices or not self.polygons


def uv_index_from_colour(colour):
    """Return the UV record index packed into a polygon's colour word.

    `colour & 0xF000` is the top nibble of the index, shifted up by four so it
    clears the bitmap bits; the rest of the index lives above bit 16. A UV
    record is shared by every polygon that names it, so taking the entry at the
    polygon's own position stretches the texture instead of placing it.

    Do not simplify: `colour` must stay unsigned, because the flagged path
    produces indices far larger than 16 bits by design.
    """
    colour = colour & 0xFFFFFFFF
    if colour & 0x0000F000:
        return ((colour & 0x0000F000) << 4) | (colour >> 16)
    return colour >> 16


def parse_shape(data, start, end, bitmaps):
    """Parse one REBSHAPE payload into a Shape, or None if it is unusable."""
    header = None
    raw_verts = None
    poly_data = None
    uv_data = None
    normals = None
    vertex_normals = None
    for ident, pstart, pend in children(data, start, end):
        if ident == "SHPHEAD1":
            header = (pstart, pend)
        elif ident == "SHPRAWVT":
            raw_verts = (pstart, pend)
        elif ident == "SHPPOLYS":
            poly_data = (pstart, pend)
        elif ident == "SHPUVCRD":
            uv_data = (pstart, pend)
        elif ident == "SHPPNORM":
            normals = (pstart, pend)
        elif ident == "SHPVNORM":
            vertex_normals = (pstart, pend)
    if header is None or raw_verts is None or poly_data is None:
        return None

    num_verts = _i32(data, header[0] + 24)
    num_polys = _i32(data, header[0] + 28)
    if num_verts <= 0 or num_polys <= 0:
        return None

    shape = Shape("")
    shape.bitmaps = bitmaps

    span = raw_verts[1] - raw_verts[0]
    vertex_count = min(num_verts, span // VERTEX_SIZE)
    for index in range(vertex_count):
        x, y, z = struct.unpack_from("<3i", data, raw_verts[0] + index * 12)
        shape.vertices.append(rif_to_blender(x, y, z))

    span = poly_data[1] - poly_data[0]
    poly_count = min(num_polys, span // POLY_SIZE)
    for index in range(poly_count):
        base = poly_data[0] + index * POLY_SIZE
        (_engine, _normal, _flags, _colour,
         v0, v1, v2, v3) = struct.unpack_from("<iiIIiiii", data, base)
        corners = [v for v in (v0, v1, v2, v3) if v >= 0]
        if len(corners) < 3:
            continue
        # A negative index ends the list; anything past the vertices is junk.
        if max(corners) >= vertex_count:
            continue
        shape.polygons.append(corners)
        # The low 16 bits of poly_colour are the bitmap index; the rest is
        # shading. Matches the `index` field of a BMPNAMES entry, not its
        # position in the list.
        shape.polygon_bitmaps.append(_colour & 0x00000FFF)
        shape.polygon_uv_indexes.append(uv_index_from_colour(_colour))
        shape.polygon_normals.append(None)

    if vertex_normals is not None:
        span = vertex_normals[1] - vertex_normals[0]
        available = min(vertex_count, span // 12)
        for index in range(available):
            x, y, z = struct.unpack_from("<3f", data,
                                         vertex_normals[0] + index * 12)
            shape.vertex_normals.append(rif_normal_to_blender(x, y, z))

    if normals is not None:
        span = normals[1] - normals[0]
        available = span // 12
        for index in range(len(shape.polygons)):
            if index >= available:
                break
            x, y, z = struct.unpack_from("<3f", data,
                                         normals[0] + index * 12)
            shape.polygon_normals[index] = rif_normal_to_blender(x, y, z)

    if uv_data is not None:
        # Parsed for every polygon, then filtered by the same rule the polygons
        # were: a degenerate entry has no corners and no place in either list.
        # Parsing them independently let the two lists end up different lengths,
        # and the importer then skipped the UVs entirely for those shapes.
        records = _parse_uvs(data, uv_data[0], uv_data[1])
        for index, corners in enumerate(shape.polygons):
            wanted = (shape.polygon_uv_indexes[index]
                      if index < len(shape.polygon_uv_indexes) else index)
            if 0 <= wanted < len(records):
                shape.uvs.append(records[wanted])
            else:
                shape.uvs.append([(0.0, 0.0)] * len(corners))

    return None if shape.is_empty else shape


def _parse_uvs(data, start, end):
    """Return a list of per-polygon (u, v) lists."""
    out = []
    if end - start < UV_HEADER_SIZE + 4:
        return out
    position = start + UV_HEADER_SIZE
    while position + 4 <= end:
        nv = _i32(data, position)
        position += 4
        if nv < 1 or nv > 4:
            break
        if position + nv * 8 > end:
            return out
        corners = []
        for _ in range(nv):
            u, v = struct.unpack_from("<2f", data, position)
            position += 8
            corners.append((u / UV_PIXELS_PER_UNIT,
                            v / UV_PIXELS_PER_UNIT))
        out.append(corners)
    return out


def parse_bitmaps(data, start, end):
    """Return the bitmap names a file lists, in index order."""
    # {index: name}, keyed by each entry's own index field. That field is what
    # a polygon's poly_colour refers to, and it is NOT the position in the list:
    # the first entry of 52_cabfrg has index 1, so matching by position found
    # nothing and every polygon fell back to one unnamed material.
    names = {}
    header = _u32(data, start)
    count = header & 0xFFFF
    position = start + 4
    for _ in range(min(count, 512)):
        if position + 20 > end:
            break
        index = _u32(data, position + 4)
        position += 20                     # flags, index, d1, d2, d3
        stop = data.find(b"\x00", position, end)
        if stop < 0:
            break
        names[index] = data[position:stop].decode("latin-1", "replace")
        position += ((stop - position) + 4) & ~3
    return names


def _find_bitmaps(data, start, end):
    """Return the bitmap names from BMPNAMES, wherever it sits.

    It is not a top-level chunk: it lives inside REBENVDT, which is why looking
    only at the top level found nothing and every shape came in untextured.
    """
    for ident, pstart, pend in children(data, start, end):
        if ident == "BMPNAMES":
            return parse_bitmaps(data, pstart, pend)
        if ident in ("REBENVDT", "REBSHAPE"):
            found = _find_bitmaps(data, pstart, pend)
            if found:
                return found
    return []


def normalise_rim(path):
    """Return a comparable key for a game texture path.

    The same texture is written two ways in this engine's own data: the archives
    say `graphics/envrnmts/furyall/metal6.RIM` and a shape says
    `Envrnmts/Furyall/Metal6.RIM`. Case does not matter on disk and the
    `graphics` prefix belongs to the archive, so both are dropped -- comparing
    the raw strings made every shape look untextured and build a second
    material.
    """
    text = str(path or "").replace("/", "\\").strip().lower()
    prefix = "graphics\\"
    while text.startswith(prefix):
        text = text[len(prefix):]
    return text


def build_mesh(shape, name, stem=None, number=0):
    """Return a finished Blender mesh for one Shape.

    Everything a shape needs to look right happens here, in one place: the
    geometry, the winding corrected against the stored normals, the smooth
    shading the vertex normals ask for, the UV layer, and the materials. Doing
    only some of it is what made pickups arrive flat and untextured while
    `Load Shapes` looked correct.
    """
    import bpy

    mesh = bpy.data.meshes.new(name)
    faces = [tuple(corners) for corners in shape.polygons]
    mesh.from_pydata([(x * 0.001, y * 0.001, z * 0.001)
                      for x, y, z in shape.vertices], [], faces)
    mesh.update()

    fix_winding(mesh, shape)
    apply_smoothing(mesh, shape)

    if shape.uvs:
        layer = mesh.uv_layers.new(name="UVMap")
        for face_index, corners in enumerate(shape.uvs):
            if face_index >= len(faces):
                break
            start = mesh.polygons[face_index].loop_start
            for corner_index, (u, v) in enumerate(corners):
                if corner_index >= len(faces[face_index]):
                    break
                loop_index = start + corner_index
                if loop_index < len(layer.data):
                    layer.data[loop_index].uv = (u, v)

    try:
        assign_materials(mesh, shape, stem or name, number)
    except (RuntimeError, KeyError):
        pass
    return mesh


def fix_winding(mesh, shape):
    """Turn each face so its normal agrees with the one the engine stored.

    `SHPPNORM` holds a real normal per polygon, and the engine's winding does not
    have to match Blender's -- it does not, and the result is meshes lit from
    behind that look unshaded. Reading the stored normal is more reliable than a
    global rule, because a shape can mix both.
    """
    for face_index, wanted in enumerate(shape.polygon_normals):
        if wanted is None or face_index >= len(mesh.polygons):
            continue
        polygon = mesh.polygons[face_index]
        current = polygon.normal
        if (current.x * wanted[0] + current.y * wanted[1]
                + current.z * wanted[2]) < 0.0:
            polygon.flip()
    mesh.update()


def apply_smoothing(mesh, shape):
    """Smooth the faces whose vertices disagree about which way is out.

    `SHPVNORM` carries a normal per vertex: a face whose corners all share one is
    a flat surface, and where they differ the surface is meant to be curved.
    Without this every shape is faceted.
    """
    normals = shape.vertex_normals
    if not normals:
        return
    for polygon in mesh.polygons:
        shared = [normals[i] for i in polygon.vertices if i < len(normals)]
        if len(shared) < 3:
            continue
        first = shared[0]
        polygon.use_smooth = any(
            first[0] * other[0] + first[1] * other[1]
            + first[2] * other[2] < 0.9998 for other in shared[1:])
    mesh.update()


def assign_materials(mesh, shape, stem, number):
    """Give the mesh one material per bitmap its polygons use.

    A material already in the file whose RIM path matches is reused, so a shape
    imported after Load Game Textures is textured. The path lives in
    RIM_PATH_PROP, which Load Game Textures fills in; matching on a made-up name
    found none of them and made a fresh untextured material each time.
    """
    import bpy

    from .texture_library import RIM_LIBRARY_PROP, RIM_PATH_PROP

    by_path = {}
    for material in bpy.data.materials:
        path = material.get(RIM_PATH_PROP)
        if not path:
            continue
        path = normalise_rim(path)
        # A library material wins over a bare stand-in of the same path, because
        # only the library one has the image behind it.
        if path not in by_path or material.get(RIM_LIBRARY_PROP):
            by_path[path] = material

    slot_of = {}
    for face_index, colour_index in enumerate(shape.polygon_bitmaps):
        if face_index >= len(mesh.polygons):
            break
        if isinstance(shape.bitmaps, dict):
            name = shape.bitmaps.get(colour_index, "")
        else:
            name = (shape.bitmaps[colour_index - 1]
                    if 0 < colour_index <= len(shape.bitmaps) else "")
        key = normalise_rim(name) if name else f"{stem}_{number}"
        if key not in slot_of:
            material = by_path.get(key) if name else None
            if material is None:
                material = bpy.data.materials.get(name or key)
            if material is None:
                material = bpy.data.materials.new(name or key)
                if name:
                    material[RIM_PATH_PROP] = name
            slot_of[key] = len(mesh.materials)
            mesh.materials.append(material)
        mesh.polygons[face_index].material_index = slot_of[key]


def read_shapes(path):
    """Return every usable Shape in one .rif file."""
    with open(path, "rb") as handle:
        data = handle.read()
    # The shape files are REBINFF2 and carry no compression; only LEVEL files
    # are REBCRIF1 and need the Huffman pass. Reading a compressed level here
    # would find no REBSHAPE at the top level and return nothing, which is a
    # clear enough failure for a function only ever pointed at shape_rifs.

    bitmaps = _find_bitmaps(data, 12, len(data))

    shapes = []
    for ident, pstart, pend in children(data, 12, len(data)):
        if ident != "REBSHAPE":
            continue
        shape = parse_shape(data, pstart, pend, bitmaps)
        if shape is not None:
            shapes.append(shape)
    return shapes
