"""
huffman.py - Port of AVP 1999 Huffman compression (huffman.cpp).
Used for REBCRIF1 compressed .rif files.

Based on huffman.cpp from the AVP source.
"""
import struct

MAX_DEPTH = 11


def decompress_rebcrif1(data: bytes) -> bytes:
    """
    Decompress a REBCRIF1 HuffmanPackage.
    Returns uncompressed bytes (typically a REBINFF2 chunk tree).
    """
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
            f"compressed size mismatch: {len(compressed)} vs {compressed_size}"
        )

    # --- Build decode table (mimics MakeHuffmanDecodeTable) ---
    # Each entry is a 16-bit value: low byte = code length, high byte = symbol.
    # Table has 2^MAX_DEPTH entries; index = next MAX_DEPTH bits of stream
    # (with the LSB cleared, mirroring  `bits & 0xFFE` on the reader side).
    table = [0] * (1 << MAX_DEPTH)

    lenbits   = 0
    repcount  = 1 << MAX_DEPTH
    repspace  = 1
    depthbit  = 4
    p         = 255            # walks byte_assignment backwards
    o         = 0
    depth_idx = 0

    while depth_idx < MAX_DEPTH:
        # find next non-empty depth
        while True:
            lenbits  += 1
            depthbit <<= 1
            repspace <<= 1
            repcount >>= 1
            if depth_idx >= MAX_DEPTH:
                break
            thisdepth = codelength_count[depth_idx]
            depth_idx += 1
            if thisdepth:
                break
        if depth_idx > MAX_DEPTH:
            break

        for _ in range(thisdepth):
            if p < 0:
                temp = 0xFF
            else:
                temp = (lenbits & 0xFF) | (byte_assignment[p] << 8)
                p -= 1

            # fill repcount entries spaced repspace apart
            # (in half-table units — the reader indexes shorts)
            idx = o >> 1
            for _ in range(repcount):
                if 0 <= idx < len(table):
                    table[idx] = temp
                idx += repspace

            # increment o (Gray-code-like ripple)
            bt = depthbit
            while True:
                o ^= bt
                if (o & bt) or bt == 0:
                    break
                bt >>= 1

    # --- Decode bit stream (mimics HuffmanDecode) ---
    out      = bytearray()
    bit_pos  = 0
    total    = len(compressed) * 8

    def read_bits(n: int) -> int:
        """Read n bits MSB-first from compressed, zero-pad beyond end."""
        nonlocal bit_pos
        v = 0
        for i in range(n):
            pos = bit_pos + i
            if pos >= total:
                v = (v << 1)
                continue
            byte_idx = pos // 8
            bit_idx  = 7 - (pos % 8)
            v = (v << 1) | ((compressed[byte_idx] >> bit_idx) & 1)
        bit_pos += n
        return v

    while len(out) < uncompressed_size:
        # read enough bits to index the table (MAX_DEPTH bits)
        peek = 0
        for i in range(MAX_DEPTH):
            pos = bit_pos + i
            if pos >= total:
                peek = peek << 1
                continue
            byte_idx = pos // 8
            bit_idx  = 7 - (pos % 8)
            peek = (peek << 1) | ((compressed[byte_idx] >> bit_idx) & 1)

        # Reader uses `bits & 0xFFE` — clear LSB, then treats as short index.
        idx = (peek << 1) & 0xFFE
        entry = table[idx] if idx < len(table) else 0
        wid   = entry & 0xFF
        sym   = (entry >> 8) & 0xFF

        if wid == 0:
            break  # malformed stream
        if sym == 0x100 or (wid == 0xFF and sym == 0xFF):
            break  # terminator

        out.append(sym)
        bit_pos += wid

    if len(out) != uncompressed_size:
        # not fatal — dump what we got
        pass

    return bytes(out)


def is_rebcrif1(data: bytes) -> bool:
    """Quick check whether data starts with REBCRIF1 identifier."""
    return len(data) >= 8 and data[:8] == b"REBCRIF1"