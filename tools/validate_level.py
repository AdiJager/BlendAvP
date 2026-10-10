#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""validate_level.py — check an exported .rif against the game loader's rules.

Every check below comes from a specific place in the AvP (1999) sources, so a
failure here is a real loader-side hazard rather than a style opinion:

  * SHPVTINT.num_vertices must equal the linked SHPHEAD1.num_verts.
    Kshape.c::VertexIntensity_Standard_Opt indexes Global_EID_IPtr[] by vertex
    number with no bounds check, and Projload.cpp:1436 allocates
    `12 * svic->num_vertices` bytes for the whole shape.

  * SHPVTINT.light_set_name must be "NORMALLT", the active set.
    Projload.cpp:1425/2037 only adopt a SHPVTINT whose name matches
    `::light_set_name`; otherwise the prelight data is ignored.

  * RBOBJECT.shape_id_no must match exactly one SHPHEAD1.file_id_num.
    MISHCHNK.CPP:1920 pairs them; an unmatched object has no shape.

  * Every polygon colour.bitmap_idx (bits 0..11, plus the 0x8000 escape) must
    appear as some BMPNAMES entry's `index` field. chnkload.cpp:1175 sizes
    tex_index_nos[] with max(bns().index) and fills tex_index_nos[bns().index];
    copy_to_mainshpl (chnkload.cpp:3390) degrades any polygon whose index does
    not resolve to I_Polygon | iflag_nolight — a flat, unlit surface.

  * A LEVEL file needs SPECLOBJ > AVPSTART inside REBENVDT, otherwise
    setup_start_position (Projload.cpp:114) falls back to the world origin,
    which is usually outside every module; AllNewModuleHandler (MODULE.C:123)
    then finds no player module and creates no DISPLAYBLOCK at all.

  * ShapeFlag_PreLit (0x1000) is informational: Projload.cpp:1455/2067 OR it
    into the runtime shape whenever a SHPVTINT is present, so a missing bit is
    reported as a note instead of an error.

Usage:
    python validate_level.py <file.rif> [...]
Exit status is 0 when no check failed, 1 otherwise.
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rif_dump import is_rebcrif1, decompress_rebcrif1  # noqa: E402

# Where to look for the game, so texture names can be checked against the real
# archives. Set AVP_GAME_DIR or pass --game <dir>.
GAME_DIR_ENV = "AVP_GAME_DIR"

CONTAINERS = {
    "REBINFF2", "REBSHAPE", "RBOBJECT", "MODULEDT", "REBENVDT",
    "SPECLOBJ", "OBINTDT", "SHPFRAGS", "SUBSHAPE", "OBJCHIER",
    "LIGHTSET", "OBJPRJDT", "SUBSHPHD", "SHPMORPH", "SHPEXTFL",
}

LIGHT_SET_NAME = "NORMALLT"
SHAPE_FLAG_PRELIT = 0x00001000
# shape.h:421 defines ShapeFlag_PreLit and SHPCHUNK.HPP:15 defines
# SHAPE_FLAG_EXTERNALFILE with the SAME value. Projload.cpp:1974 branches on
# the latter, so a level shape carrying this bit never reaches copy_to_module()
# and the level renders empty.
SHAPE_FLAG_EXTERNALFILE = 0x00001000
OBJECT_FLAG_PLACED_OBJECT = 0x0000400
# LTCHUNK.HPP:15 — SetUpRunTimeLights (Objsetup.cpp:3502) only adopts a
# STDLIGHT that carries this bit in local_light_flags. Without it the light is
# inert, which is exactly the symptom "point lights do not light the player".
LIGHT_LOCAL_FLAG_RUNTIME = 0x00000010


class Findings:
    """Errors, warnings and notes for one file."""

    def __init__(self, path):
        self.path = str(path)
        self.errors = []
        self.warnings = []
        self.notes = []

    def error(self, msg):
        """Record a loader-side hazard."""
        self.errors.append(msg)

    def warn(self, msg):
        """Record something suspicious that is not necessarily fatal."""
        self.warnings.append(msg)

    def note(self, msg):
        """Record an informational observation."""
        self.notes.append(msg)


def load(path):
    """Read a .rif, decompressing REBCRIF1 transparently."""
    data = Path(path).read_bytes()
    return decompress_rebcrif1(data) if is_rebcrif1(data) else data


def sibs(data, start, end):
    """Yield (cid, off, size) for each sibling chunk in [start, end)."""
    off = start
    while off + 12 <= end:
        cid = data[off:off + 8].rstrip(b"\x00").decode("ascii", "replace")
        size = struct.unpack_from("<I", data, off + 8)[0]
        if size < 12 or off + size > end:
            return
        yield (cid, off, size)
        off += size


def load_texture_catalogue(game_dir):
    """Return ``{basename: {full_name, ...}}`` from the game's Tex*.FFL archives.

    Mirrors how the engine resolves a texture: it compares the basename
    case-insensitively (FFREAD.CPP, `_stricmp`) but narrows the archive search
    by the name's directory prefix (FFSTDIO.CPP `CouldInclude`). Both pieces are
    captured so a name can be judged for "found at all" and for "ambiguous".

    Returns ``None`` when the archives cannot be read, so the check is silently
    skipped rather than reporting a false failure.
    """
    fastfile_dir = Path(game_dir) / "fastfile"
    if not fastfile_dir.is_dir():
        return None
    archives = sorted(p for p in fastfile_dir.glob("Tex*.FFL")
                      if p.name.lower().startswith("tex"))
    if not archives:
        return None

    catalogue = {}
    for path in archives:
        try:
            data = path.read_bytes()
            _magic, _version, num_files, header_bytes, _datasize = \
                struct.unpack_from("<4siiii", data, 0)
        except (OSError, struct.error):
            return None
        pos = 20
        for _ in range(num_files):
            try:
                offset, length = struct.unpack_from("<ii", data, pos)
                end = data.index(b"\0", pos + 8)
            except (struct.error, ValueError):
                break
            name = data[pos + 8:end].decode("latin-1")
            lowered = name.lower().replace("/", "\\")
            basename = lowered.rsplit("\\", 1)[-1]
            # The archive's own `graphics\` root is not part of what a level
            # names, so strip it for comparison.
            if lowered.startswith("graphics\\"):
                lowered = lowered[len("graphics\\"):]
            catalogue.setdefault(basename, set()).add(lowered)
            pos += 8 + ((len(name) + 4) & ~3)
    return catalogue


def check_texture_names(info, catalogue, findings):
    """Report BMPNAMES entries the game would not resolve.

    Two things have to line up. The loader treats the name as a path relative to
    the textures root, which is `FirstTex_Directory = "Graphics"`
    (Projload.cpp:97), so it looks up `Graphics\\<name>`; and a `graphics\\`
    prefix inside the name therefore double-counts the folder and resolves
    nowhere. Retail levels write `Envrnmts\\Battle\\timegg.RIM`, never
    `graphics\\Envrnmts\\...`.

    An unresolved texture does not merely lose its image: chnkload.cpp:3390
    rewrites the polygon to I_Polygon with iflag_nolight, so the surface stops
    being drawn as well. A level where every texture is unresolved renders as
    nothing at all while its collisions still work, which is exactly the symptom
    this check exists to name.
    """
    if not catalogue or not info["bmp_entries"]:
        return
    missing = []
    double_prefixed = []
    for _index, name in info["bmp_entries"]:
        lowered = name.lower().replace("/", "\\").lstrip("\\")
        basename = lowered.rsplit("\\", 1)[-1]
        if basename not in catalogue:
            missing.append(name)
            continue
        # The engine prepends the textures root to whatever the name says.
        if lowered.startswith("graphics\\"):
            double_prefixed.append(name)

    total = len(info["bmp_entries"])
    if missing:
        shown = ", ".join(repr(n) for n in missing[:5])
        findings.warn(
            f"{len(missing)}/{total} BMPNAMES texture name(s) are not in the "
            f"game's archives, so those polygons become I_Polygon with "
            f"iflag_nolight (chnkload.cpp:3390) - unlit and effectively "
            f"invisible: {shown}")
    if double_prefixed:
        shown = ", ".join(repr(n) for n in double_prefixed[:3])
        findings.error(
            f"{len(double_prefixed)}/{total} BMPNAMES name(s) start with "
            f"'graphics\\', but the loader already prefixes the textures root "
            f"(FirstTex_Directory = \"Graphics\", Projload.cpp:97), so it will "
            f"look for 'Graphics\\graphics\\...' and find nothing - the level "
            f"renders empty while collisions still work: {shown}")
    if not missing and not double_prefixed:
        findings.note(f"all {total}/{total} BMPNAMES names resolve to the "
                      f"game's archives")


def parse_shape(data, off, size):
    """Return a dict describing one REBSHAPE subtree."""
    shape = {"flags": None, "file_id": None, "num_verts": None,
             "num_polys": None, "bitmap_idx": set(), "num_uvs": None}
    for cid, coff, csize in sibs(data, off + 12, off + size):
        if cid == "SHPHEAD1":
            p = coff + 12
            shape["flags"], = struct.unpack_from("<i", data, p)
            (shape["file_id"], shape["num_verts"],
             shape["num_polys"]) = struct.unpack_from("<3i", data, p + 20)
        elif cid == "SHPPOLYS":
            p = coff + 12
            for i in range((csize - 12) // 36):
                colour, = struct.unpack_from("<I", data, p + 36 * i + 12)
                shape["bitmap_idx"].add(colour & 0x7FFF)
        elif cid == "SHPUVCRD":
            shape["num_uvs"], = struct.unpack_from("<i", data, coff + 12)
    return shape


def parse_object(data, off, size):
    """Return a dict describing one RBOBJECT subtree."""
    obj = {"children": [], "flags": None, "quat": None, "index_num": None,
           "shape_id": None, "svic": None, "vmdarray": None}
    for cid, coff, csize in sibs(data, off + 12, off + size):
        obj["children"].append(cid)
        if cid == "OBJHEAD1":
            p = coff + 12
            obj["flags"], = struct.unpack_from("<i", data, p)
            obj["quat"] = struct.unpack_from("<4f", data, p + 32)
            (obj["index_num"], _ver, obj["shape_id"]) = \
                struct.unpack_from("<3i", data, p + 48)
        elif cid == "SHPVTINT":
            p = coff + 12
            lsn = data[p:p + 8].rstrip(b"\x00").decode("ascii", "replace")
            _pad, cnt = struct.unpack_from("<2i", data, p + 8)
            obj["svic"] = {"light_set": lsn, "count": cnt,
                           "payload": csize - 12}
        elif cid == "VMDARRAY":
            obj["vmdarray"], = struct.unpack_from("<i", data, coff + 12)
    return obj


def parse_bmpnames(data, off, size):
    """Return the list of BMPNAMES `index` field values."""
    p = off + 12
    header, = struct.unpack_from("<I", data, p)
    count = header & 0xFFFF
    indices = []
    q = p + 4
    for _ in range(count):
        try:
            _flags, index = struct.unpack_from("<2I", data, q)
        except struct.error:
            break
        q += 20
        nul = data.find(b"\x00", q)
        if nul < 0:
            break
        q = (nul + 1 + 3) & ~3
        indices.append(index)
    return count, indices


def parse_bmpnames_entries(data, off, size):
    """Return [(index, name)] for a BMPNAMES chunk.

    Separate from `parse_bmpnames` because the names matter for one check only:
    the game resolves a texture by basename, so a level that names one basename
    under two different directory prefixes is ambiguous. No retail level does
    this (checked across all 66), so seeing it means the export mixed textures.
    """
    p = off + 12
    header, = struct.unpack_from("<I", data, p)
    count = header & 0xFFFF
    entries = []
    q = p + 4
    for _ in range(count):
        try:
            _flags, index = struct.unpack_from("<2I", data, q)
        except struct.error:
            break
        q += 20
        nul = data.find(b"\x00", q)
        if nul < 0:
            break
        entries.append((index, data[q:nul].decode("latin-1")))
        q = (nul + 1 + 3) & ~3
    return entries


def parse_stdlight(data, off, size):
    """Return a dict describing one STDLIGHT chunk."""
    p = off + 12
    light = {}
    light["number"], = struct.unpack_from("<i", data, p)
    light["location"] = struct.unpack_from("<3i", data, p + 4)
    light["brightness"], = struct.unpack_from("<i", data, p + 52)
    light["spread"], = struct.unpack_from("<i", data, p + 56)
    light["range"], = struct.unpack_from("<i", data, p + 60)
    light["colour"], = struct.unpack_from("<i", data, p + 64)
    light["engine_flags"], = struct.unpack_from("<i", data, p + 68)
    light["local_flags"], = struct.unpack_from("<i", data, p + 72)
    return light


def collect(data):
    """Walk the file and return the structures the checks need."""
    out = {"shapes": [], "objects": [], "bmp_count": 0, "bmp_indices": [],
           "bmp_entries": [], "avpstart": False, "lightsets": 0, "lights": [],
           "ambience": None, "roots": []}

    def walk(start, end, depth=0):
        for cid, off, size in sibs(data, start, end):
            if depth == 0:
                out["roots"].append(cid)
            if cid == "REBSHAPE":
                out["shapes"].append(parse_shape(data, off, size))
            elif cid == "RBOBJECT":
                out["objects"].append(parse_object(data, off, size))
            elif cid == "BMPNAMES" and not out["bmp_count"]:
                out["bmp_count"], out["bmp_indices"] = \
                    parse_bmpnames(data, off, size)
                out["bmp_entries"] = parse_bmpnames_entries(data, off, size)
            elif cid == "AVPSTART":
                out["avpstart"] = True
            elif cid == "LIGHTSET":
                out["lightsets"] += 1
            elif cid == "STDLIGHT":
                out["lights"].append(parse_stdlight(data, off, size))
            elif cid == "AMBIENCE":
                out["ambience"], = struct.unpack_from("<i", data, off + 12)
            if cid in CONTAINERS and depth < 6:
                walk(off + 12, off + size, depth + 1)

    walk(12, len(data))
    return out


def check(path, catalogue=None):
    """Validate one file; return Findings."""
    data = load(path)
    f = Findings(path)
    info = collect(data)
    shapes = {s["file_id"]: s for s in info["shapes"] if s["file_id"] is not None}
    bmp = set(info["bmp_indices"])

    if not info["roots"]:
        f.error("no top-level chunks found")
    elif info["roots"][0] not in ("REBINFF2", "RIFVERIN"):
        f.error(f"unexpected first root chunk {info['roots'][0]!r}; the "
                "loader expects REBINFF2 (or RIFVERIN straight after it)")

    for fid, sh in sorted(shapes.items()):
        bad = sorted(b for b in sh["bitmap_idx"] if b not in bmp)
        if bad:
            # Not a hard error: tex_index_nos[] lives on the RIFF handle and is
            # (re)filled from whichever RIF loaded its BMPNAMES last, so a
            # character RIF can satisfy an index this level does not declare —
            # retail hive_uncompressed.rif ships bitmap_idx 1 and 2 with no
            # matching entry. It is still the first thing to check when
            # geometry renders as flat unlit surfaces.
            f.warn(f"SHPHEAD1 file_id={fid}: polygon bitmap_idx {bad} have no "
                   "matching BMPNAMES index in this file; unless another "
                   "loaded RIF declares them, those polygons become I_Polygon "
                   "with iflag_nolight (chnkload.cpp:3390)")
        if sh["num_uvs"] and sh["num_polys"] and sh["num_uvs"] != sh["num_polys"]:
            f.warn(f"SHPHEAD1 file_id={fid}: SHPUVCRD holds {sh['num_uvs']} "
                   f"records for {sh['num_polys']} polygons; the UV index is "
                   "the polygon's high word, so a gap reads the wrong UVs")
        if sh["num_polys"] and not sh["bitmap_idx"]:
            f.warn(f"SHPHEAD1 file_id={fid}: no polygon colour data found")

    prelit_shapes = {fid for fid, sh in shapes.items()
                     if sh["flags"] is not None
                     and sh["flags"] & SHAPE_FLAG_PRELIT}

    # ShapeFlag_PreLit without SHPVTINT is legal and common in retail data
    # (Battle.RIF: 15 prelit shapes, only 14 objects with SHPVTINT). The
    # engine then simply does not set the runtime bit (Projload.cpp:1455), so
    # the shape falls back to dynamic lighting instead of baked prelight.
    prelit_with_data = {ob["shape_id"] for ob in info["objects"] if ob["svic"]}
    stale = sorted(prelit_shapes - prelit_with_data)
    if stale:
        f.note(f"{len(stale)} shape(s) set ShapeFlag_PreLit with no SHPVTINT "
               f"on any linked object (file_id {stale[:8]}"
               f"{'...' if len(stale) > 8 else ''}); the loader ignores the bit "
               "and uses dynamic lighting — expected in retail data")

    for ob in info["objects"]:
        sid = ob["shape_id"]
        tag = f"RBOBJECT index_num={ob['index_num']}"
        if sid not in shapes:
            f.error(f"{tag}: shape_id_no={sid} matches no "
                    "SHPHEAD1.file_id_num (MISHCHNK.CPP:1920)")
        if ob["quat"] is not None and sum(c * c for c in ob["quat"]) < 1e-9:
            f.warn(f"{tag}: orientation is the zero quaternion; retail uses "
                   "(0, 0, 0, 1)")
        svic = ob["svic"]
        if svic is None:
            continue
        if svic["light_set"] != LIGHT_SET_NAME:
            f.error(f"{tag}: SHPVTINT light_set_name="
                    f"{svic['light_set']!r} != {LIGHT_SET_NAME!r} — "
                    "Projload.cpp:2037 ignores it")
        sh = shapes.get(sid)
        if sh is not None and svic["count"] != sh["num_verts"]:
            f.error(f"{tag}: SHPVTINT num_vertices={svic['count']} != "
                    f"SHPHEAD1.num_verts={sh['num_verts']} — Kshape.c reads "
                    "the array without a bounds check")
        expected = 16 + 4 * svic["count"]
        if svic["payload"] != expected:
            f.error(f"{tag}: SHPVTINT payload is {svic['payload']} B, "
                    f"expected {expected} B")
        if sh is not None and not (sh["flags"] & SHAPE_FLAG_PRELIT):
            f.note(f"{tag}: SHPHEAD1.flags lacks ShapeFlag_PreLit; the loader "
                   "sets it from SHPVTINT (Projload.cpp:1455)")

    # The S6 F9 root cause. Not every 0x1000 shape is broken: the flag is
    # legitimate on imported shapes whose objects are PLACED_OBJECT (retail
    # Battle.RIF has 15 such shapes, all attached to placed objects). It is
    # fatal for MODULE objects, where Projload.cpp:2005 hits
    # GLOBALASSERT("Shouldn't be any modules using imported shapes") and the
    # object never reaches copy_to_module(), so no DISPLAYBLOCK is created.
    linked = {}
    for ob in info["objects"]:
        linked.setdefault(ob["shape_id"], []).append(ob)
    for fid, sh in sorted(shapes.items()):
        if sh["flags"] is None or not (sh["flags"] & SHAPE_FLAG_EXTERNALFILE):
            continue
        module_objs = [o for o in linked.get(fid, [])
                       if not (o["flags"] or 0) & OBJECT_FLAG_PLACED_OBJECT]
        if module_objs:
            f.error(
                f"SHPHEAD1 file_id={fid}: flags 0x{sh['flags']:08X} has bit "
                "0x1000 set, which shape.h:421 names ShapeFlag_PreLit and "
                "SHPCHUNK.HPP:15 names SHAPE_FLAG_EXTERNALFILE. RBOBJECT "
                f"index_num={module_objs[0]['index_num']} links it without "
                "OBJECT_FLAG_PLACED_OBJECT, so Projload.cpp:1974 takes the "
                "imported-shape branch and GLOBALASSERT fires at :2005 — the "
                "object never reaches copy_to_module() and the level renders "
                "empty. Clear the bit: prelight comes from SHPVTINT.")
        else:
            f.note(f"SHPHEAD1 file_id={fid}: bit 0x1000 set on a shape whose "
                   "objects are all placed objects (imported-shape path) — "
                   "legal, matches retail")

    if not info["objects"]:
        f.warn("no RBOBJECT: nothing gets placed into a module")
    if not info["avpstart"]:
        f.warn("no SPECLOBJ > AVPSTART: setup_start_position falls back to the "
               "world origin, usually outside every module — "
               "AllNewModuleHandler then creates no DISPLAYBLOCK")
    if not info["bmp_count"]:
        f.warn("no BMPNAMES: every textured polygon degrades to I_Polygon "
               "with iflag_nolight")
    else:
        # The game resolves a texture by basename, but narrows the archive
        # search using the BMPNAMES directory prefix (ffinfo.txt maps prefixes
        # to archives). A basename used under two different prefixes is
        # therefore ambiguous. Verified: none of the 66 retail levels does it.
        prefixes = {}
        for _index, name in info["bmp_entries"]:
            lowered = name.lower()
            basename = lowered.rsplit("\\", 1)[-1]
            prefix = lowered.rsplit("\\", 1)[0] if "\\" in lowered else ""
            prefixes.setdefault(basename, set()).add(prefix)
        clashes = {b: sorted(p) for b, p in prefixes.items() if len(p) > 1}
        if clashes:
            detail = "; ".join(f"{b} in {p}" for b, p in
                               sorted(clashes.items())[:3])
            f.warn(f"{len(clashes)} texture basename(s) appear under more than "
                   f"one directory prefix, so which archive the game picks is "
                   f"undefined: {detail}")
        check_texture_names(info, catalogue, f)
    if not info["lightsets"]:
        f.note("no LIGHTSET (tolerated by the loader; retail Hive has one)")

    # Lights: these chunks are the only source of lighting for dynamic objects.
    inert = [l for l in info["lights"]
             if not (l["local_flags"] & LIGHT_LOCAL_FLAG_RUNTIME)]
    if info["lights"] and inert:
        f.warn(f"{len(inert)}/{len(info['lights'])} STDLIGHT chunk(s) lack "
               f"LOFlag_Runtime (0x10) in local_light_flags; "
               "SetUpRunTimeLights (Objsetup.cpp:3502) skips them, so they "
               "will not light the player or NPCs even though static geometry "
               "still shows the bake")
    dead = [l for l in info["lights"] if l["brightness"] <= 0 or l["range"] <= 0]
    if dead:
        f.warn(f"{len(dead)} STDLIGHT chunk(s) have zero brightness or range")
    if info["lights"]:
        f.note(f"{len(info['lights'])} STDLIGHT chunk(s), "
               f"{len(info['lights']) - len(inert)} with LOFlag_Runtime; "
               f"AMBIENCE={info['ambience']}")
    elif info["ambience"] is not None:
        f.note(f"no STDLIGHT; AMBIENCE={info['ambience']} only")

    f.note(f"shapes={len(info['shapes'])} objects={len(info['objects'])} "
           f"bmpnames={info['bmp_count']} lightsets={info['lightsets']} "
           f"prelit_objects={sum(1 for o in info['objects'] if o['svic'])} "
           f"avpstart={'yes' if info['avpstart'] else 'no'}")
    return f


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1

    # --game <dir> (or AVP_GAME_DIR) enables the texture-name check.
    game_dir = os.environ.get(GAME_DIR_ENV)
    paths = []
    index = 0
    while index < len(argv):
        if argv[index] == "--game" and index + 1 < len(argv):
            game_dir = argv[index + 1]
            index += 2
            continue
        paths.append(argv[index])
        index += 1

    if not paths:
        print(__doc__)
        return 1

    catalogue = load_texture_catalogue(game_dir) if game_dir else None
    if game_dir and catalogue is None:
        print(f"# cannot read texture archives under {game_dir}\\fastfile; "
              "skipping the texture-name check")

    errors = 0
    for i, path in enumerate(paths):
        if i:
            print()
        print(f"=== {path}")
        f = check(path, catalogue)
        for msg in f.notes:
            print(f"  note    {msg}")
        for msg in f.warnings:
            print(f"  WARN    {msg}")
        for msg in f.errors:
            print(f"  ERROR   {msg}")
        if not f.errors and not f.warnings:
            print("  OK      all loader-side checks passed")
        errors += len(f.errors)
    print(f"\n{errors} error(s) across {len(paths)} file(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
