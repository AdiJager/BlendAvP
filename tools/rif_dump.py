#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
rif_dump.py - Alien vs Predator (1999) .rif inspector.

Supports:
  * REBINFF2  - uncompressed RIF (chunk tree)
  * REBCRIF1  - Huffman-compressed RIF (wraps REBINFF2)

Based on chunk.cpp, chunk.hpp, huffman.cpp, huffman.hpp from the AVP source,
plus the working Huffman decoder from the community importer.

Usage:
    python rif_dump.py <file.rif>            # decompress + walk chunk tree
    python rif_dump.py <file.rif> --raw      # raw hex of first 2 KB
"""

from __future__ import annotations

import sys
import io
import struct
from pathlib import Path

# ---------------------------------------------------------------------------
# Force UTF-8 stdout/stderr (Windows cp1250 fix)
# ---------------------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Chunk ID knowledge base
# ---------------------------------------------------------------------------
KNOWN = {
    # file roots
    "REBINFF2": "GodFather_Chunk (uncompressed file root)",
    "REBCRIF1": "HuffmanPackage (compressed file root)",
    "RIFVERIN": "RIF_Version_Info_Chunk",
    # shape
    "REBSHAPE": "Shape_Chunk",
    "SHPHEAD1": "Shape_Header_Chunk",
    "SHPRAWVT": "Shape_Vertex_Chunk",
    "SHPVNORM": "Shape_Vertex_Normal_Chunk",
    "SHPPNORM": "Shape_Polygon_Normal_Chunk",
    "SHPPOLYS": "Shape_Polygon_Chunk",
    "SHPUVCRD": "Shape_UV_Coord_Chunk",
    "SHPTEXFN": "Shape_Texture_Filenames_Chunk",
    "SHPCENTR": "Shape_Centre_Chunk",
    "SHPMRGDT": "Shape_Merge_Data_Chunk",
    "SHPPCINF": "Shape_Poly_Change_Info_Chunk",
    "SHPFRAGS": "Shape_Fragments_Chunk",
    "SHPFNAME": "Shape_Name_Chunk",
    "SHPPRPRO": "Shape_Preprocessed_Data_Chunk",
    "SHPMORPH": "Shape_Morphing_Data_Chunk",
    "SHPEXTFL": "Shape_External_File_Chunk",
    "SHPEXTFN": "Shape_External_Filename_Chunk",
    "SUBSHAPE": "Shape_Sub_Shape_Chunk",
    "SUBSHPHD": "Shape_Sub_Shape_Header_Chunk",
    "FRAGDATA": "Shape_Fragments_Data_Chunk",
    "FRAGLOCN": "Shape_Fragment_Location_Chunk",
    "FRGSOUND": "Fragment_Type_Sound_Chunk",
    "FRGTYPDC": "Fragment_Type_Chunk",
    "CONSHAPE": "Console_Shape_Chunk",
    "CONSTYPE": "Console_Type_Chunk",
    # animation
    "TEXTANIM": "Animation_Chunk",
    "ANIMSEQU": "Anim_Shape_Sequence_Chunk",
    "ANIMFRAM": "Anim_Shape_Frame_Chunk",
    "ANISEQDT": "Anim_Shape_Sequence_Data_Chunk",
    "ANIFRADT": "Anim_Shape_Frame_Data_Chunk",
    "ASALTTEX": "Anim_Shape_Alternate_Texturing_Chunk",
    "ANSHCEN2": "Anim_Shape_Centre_Chunk",
    "PNOTINBB": "Poly_Not_In_Bounding_Box_Chunk",
    # object
    "RBOBJECT": "Object_Chunk",
    "OBJHEAD1": "Object_Header_Chunk",
    "OBINTDT":  "Object_Interface_Data_Chunk",
    "OBJNOTES": "Object_Notes_Chunk",
    "OBJPRJDT": "Object_Project_Data_Chunk",
    "MODULEDT": "Object_Module_Data_Chunk",
    "VMDARRAY": "VModule_Array_Chunk",
    "ADJMDLEP": "Adjacent_Module_Entry_Points_Chunk",
    "MODFLAGS": "Module_Flag_Chunk",
    "MODZONE":  "Module_Zone_Chunk",
    "MODACOUS": "Module_Acoustics_Chunk",
    "OBJTRAK2": "Object_Track_Chunk2",
    "TRAKSOUN": "Object_Track_Sound_Chunk",
    "ALTLOCAT": "Object_Alternate_Locations_Chunk",
    "SHPVTINT": "Shape_Vertex_Intensities_Chunk",
    # hierarchy / animation (observed in sentry.rif)
    "OBJCHIER": "Object_Hierarchy_Chunk",
    "OBJHIERD": "Object_Hierarchy_Data_Chunk",
    "OBANALLS": "Object_Animation_All_Sequence_Chunk",
    "OBANSEQS": "Object_Animation_Sequences_Chunk",
    "OBANSEQCH": "Object_Animation_Sequence_Chunk",
    "OBASEQHD": "Object_Animation_Sequence_Header_Chunk",
    "OBANSEQ":  "Object_Animation_Sequence_Frame_Chunk",
    "OBHIERNM": "Object_Hierarchy_Name_Chunk",
    # environment
    "REBENVDT": "Environment_Data_Chunk",
    "ENDTHEAD": "Environment_Data_Header_Chunk",
    "ENVSDSCL": "Environment_Scale_Chunk",
    "ENVACOUS": "Environment_Acoustics_Chunk",
    "SOUNDDIR": "Sound_Directory_Chunk",
    "RIFFNAME": "RIF_Name_Chunk",
    "SPECLOBJ": "Special_Objects_Chunk",
    "GAMEMODE": "Environment_Game_Mode_Chunk",
    "AVPENVIR": "AVP_Environment_Settings_Chunk",
    "AVPSTART": "AVP_Player_Start_Chunk",
    "AVPCABLE": "AVP_Power_Cable_Chunk",
    "AVPGENER": "AVP_Generator_Chunk",
    "AVPGENEX": "AVP_Generator_Extra_Data_Chunk",
    "AVPGENNM": "AVP_Generator_Extra_Name_Chunk",
    "GENEXSET": "AVP_Generator_Extended_Settings_Chunk",
    "GLOGENDC": "Global_Generator_Data_Chunk",
    "AVPDECAL": "AVP_Decal_Chunk",
    "PARGENER": "AVP_Particle_Generator_Chunk",
    "PARGENDA": "AVP_Particle_Generator_Data_Chunk",
    "PLACHIER": "Placed_Hierarchy_Chunk",
    "RANTEXID": "Random_Texture_ID_Chunk",
    # bitmaps / palettes
    "BMPNAMES": "Global_BMP_Name_Chunk",
    "BMNAMVER": "BMP_Names_Version_Chunk",
    "BMNAMEXT": "BMP_Names_ExtraData_Chunk",
    "BMPLSTST": "Bitmap_List_Store_Chunk",
    "BMPMD5ID": "Bitmap_MD5_Chunk",
    "MATCHIMG": "Matching_Images_Chunk",
    "ENVPALET": "Environment_Palette_Chunk",
    "CLRLOOKP": "Coloured_Polygons_Lookup_Chunk",
    "ENVTXLIT": "Environment_TLT_Chunk",
    "TLTCONFG": "TLT_Config_Chunk",
    "LIGHTSET": "Light_Set_Chunk",
    "PRSETPAL": "Preset_Palette_Chunk",
    "FRAGTYPE": "Fragment_Type_Chunk",
    # sprites / sound
    "RSPRITES": "Sprite_Collection_Chunk",
    "SPRIHEAD": "Sprite_Header_Chunk",
    "INDSOUND": "Indexed_Sound_Chunk",
}

# Chunks whose payload is a sequence of nested chunks. Everything else
# gets a raw hex dump of the payload.
CONTAINERS = {
    "REBINFF2", "REBENVDT", "SPECLOBJ", "RBOBJECT", "OBINTDT",
    "OBJPRJDT", "MODULEDT", "REBSHAPE", "SUBSHAPE", "SHPMORPH",
    "SHPFRAGS", "ANIMSEQU", "ANIMFRAM", "CONSHAPE", "AVPGENEX",
    "PARGENER", "SHPEXTFL", "OBJCHIER", "RSPRITES",
}


# ---------------------------------------------------------------------------
# Huffman decompression for REBCRIF1
# ---------------------------------------------------------------------------
MAX_DEPTH = 11

# LSB-first bitstream: reverse each byte so we can consume MSB-first.
_REVERSE = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))


def _build_huffman_lookup(codelength_count, byte_assignment):
    """
    Build canonical Huffman {(length, code): symbol} table.
    Codes assigned shortest-first; symbols drawn from byte_assignment[255] down.
    """
    lookup = {}
    code = 0
    sym_idx = 255
    for length in range(1, MAX_DEPTH + 1):
        for _ in range(codelength_count[length - 1]):
            if 0 <= sym_idx < 256:
                lookup[(length, code)] = byte_assignment[sym_idx]
            sym_idx -= 1
            code += 1
        code <<= 1
    return lookup


def _huffman_decode(compressed, lookup, uncompressed_size):
    """Decode LSB-first Huffman bitstream into uncompressed_size bytes."""
    out = bytearray()
    buf = 0
    bit_count = 0
    pos = 0
    truncated = False

    while len(out) < uncompressed_size and pos <= len(compressed):
        # Top up to MAX_DEPTH bits - always enough to match one code.
        while bit_count < MAX_DEPTH and pos < len(compressed):
            buf = (buf << 8) | _REVERSE[compressed[pos]]
            bit_count += 8
            pos += 1
        if bit_count == 0:
            truncated = True
            break
        matched = False
        for depth in range(1, min(bit_count, MAX_DEPTH) + 1):
            shift = bit_count - depth
            code = (buf >> shift) & ((1 << depth) - 1)
            if (depth, code) in lookup:
                out.append(lookup[(depth, code)])
                buf &= (1 << shift) - 1
                bit_count = shift
                matched = True
                break
        if not matched:
            truncated = True
            break

    if truncated:
        print(f"# WARNING: Huffman decode truncated at {len(out)}/{uncompressed_size} bytes")
    return bytes(out)


def decompress_rebcrif1(data: bytes) -> bytes:
    """Decompress a REBCRIF1 payload, returning the inner REBINFF2 bytes."""
    if len(data) < 8 + 4 + 4 + 4 * MAX_DEPTH + 256:
        raise ValueError("REBCRIF1 header truncated")
    if data[:8] != b"REBCRIF1":
        raise ValueError(f"Not a REBCRIF1: {data[:8]!r}")

    compressed_size   = struct.unpack_from("<I", data, 8)[0]
    uncompressed_size = struct.unpack_from("<I", data, 12)[0]
    codelength_count  = list(struct.unpack_from(f"<{MAX_DEPTH}I", data, 16))
    off_assign        = 16 + 4 * MAX_DEPTH
    byte_assignment   = list(data[off_assign:off_assign + 256])
    off_data          = off_assign + 256
    compressed        = data[off_data:off_data + compressed_size]

    if len(compressed) != compressed_size:
        raise ValueError(
            f"compressed data truncated: have {len(compressed)}, need {compressed_size}"
        )

    lookup = _build_huffman_lookup(codelength_count, byte_assignment)
    return _huffman_decode(compressed, lookup, uncompressed_size)


def is_rebcrif1(data: bytes) -> bool:
    """True if data starts with the REBCRIF1 identifier."""
    return len(data) >= 8 and data[:8] == b"REBCRIF1"


# ---------------------------------------------------------------------------
# Chunk tree walking
# ---------------------------------------------------------------------------
def read_chunk_header(data: bytes, offset: int):
    """Read 8-byte ID + 4-byte size at offset. Returns (id_str, size, payload_offset)."""
    if offset + 12 > len(data):
        return None
    cid_raw = data[offset:offset + 8]
    cid = cid_raw.rstrip(b"\x00").decode("ascii", errors="backslashreplace")
    size = struct.unpack_from("<I", data, offset + 8)[0]
    return cid, size, offset + 12


def _hexdump(data: bytes, start: int, end: int, depth: int):
    """Classic 16-bytes-per-line hex dump."""
    prefix = "  " * depth
    for i in range(start, end, 16):
        row = data[i:i + 16]
        hexs = " ".join(f"{b:02X}" for b in row)
        ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        print(f"{prefix}{i:08X}  {hexs:<47}  {ascii_}")


def dump_tree(data: bytes, offset: int, end: int, depth: int = 0, max_depth: int = 12):
    """Recursively print chunk tree with hex dump of leaf payloads."""
    while offset < end:
        hdr = read_chunk_header(data, offset)
        if hdr is None:
            print(f"{'  '*depth}[!] truncated header at 0x{offset:06X}")
            return
        cid, size, payload = hdr
        if size < 12 or offset + size > end:
            print(f"{'  '*depth}[!] bad size {size} for '{cid}' at 0x{offset:06X}")
            return

        label = KNOWN.get(cid, "?unknown?")
        payload_len = size - 12
        print(f"{'  '*depth}@{offset:08X}  {cid:8s}  size={size:6d}  payload={payload_len:6d}  {label}")

        if cid in CONTAINERS and payload_len > 0 and depth < max_depth:
            dump_tree(data, payload, offset + size, depth + 1, max_depth)
        else:
            dump_end = min(offset + size, payload + 128)
            _hexdump(data, payload, dump_end, depth + 1)

        offset += size


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("usage: rif_dump.py <file.rif> [--raw]")
        return 1

    path = Path(sys.argv[1])
    data = path.read_bytes()
    print(f"# {path}  ({len(data)} bytes)")
    print()

    if "--raw" in sys.argv:
        _hexdump(data, 0, min(len(data), 2048), 0)
        return 0

    # Unwrap REBCRIF1 if present.
    if is_rebcrif1(data):
        csize = struct.unpack_from("<I", data, 8)[0]
        usize = struct.unpack_from("<I", data, 12)[0]
        print(f"# REBCRIF1: compressed = {csize}  uncompressed = {usize}")
        print("# Decompressing...")
        try:
            data = decompress_rebcrif1(data)
        except Exception as e:
            print(f"# DECOMPRESSION FAILED: {e}")
            return 2
        print(f"# Decompressed to {len(data)} bytes")
        if len(data) >= 8:
            print(f"# First 8 bytes: {data[:8]!r}")
        print()

    hdr = read_chunk_header(data, 0)
    if hdr is None:
        print("# Empty or truncated file")
        return 0

    cid = hdr[0]
    if cid == "REBINFF2":
        dump_tree(data, 0, len(data), 0)
    else:
        print(f"# Unknown root '{cid}'; scanning top-level chunks")
        dump_tree(data, 0, len(data), 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())