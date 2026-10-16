"""INDSOUND — a sound attached to another chunk, chosen by index.

`Indexed_Sound_Chunk` (`SNDCHUNK.HPP:100`) is not a placed sound: it has no
position of its own, and belongs to whatever contains it -- a particle
generator, a placed hierarchy, a mission. Its `index` is what picks it out of
the list when the container plays one.

    int index;
    char* wav_name;
    unsigned long inner_range;   // millimetres
    unsigned long outer_range;   // millimetres
    int max_volume;              // 0 to 127
    int pitch;                   // in 1/1536ths of an octave
    int flags;                   // IndexedSoundFlag_Loop
    int num_extra_data;
    int* extra_data;

`size_chunk` gives the reason the name sits where it does:

    chunk_size = 12 + 24;
    chunk_size += 4 + 4 * num_extra_data;
    chunk_size += wav_name ? (strlen(wav_name) + 4) & ~3 : 4;

so the payload is six integers, the name padded past its terminator, then the
extra array. Two retail records make it plain: a 44-byte chunk holding a
fourteen-character name is 16 + 24 + 4, and a 40-byte one holding an
eleven-character name is 12 + 24 + 4.

The ranges are millimetres and the volume is 0 to 127, which matches how a
`SOUNDOB2` describes itself, so a name borrowed from the library can be given
the same numbers in either chunk.
"""

from __future__ import annotations

import struct

from ..chunk_writer import Chunk

# `SNDCHUNK.HPP:99`.
INDEXED_SOUND_FLAG_LOOP = 0x00000001

# Six integers before the extras, and the name sits behind the first of them.
INDEXED_SOUND_FIXED_BYTES = 24

# The volume is 0 to 127, not a fraction.
INDEXED_SOUND_MAX_VOLUME = 127


def pad_name(name: str) -> bytes:
    """Return a NUL-terminated name padded the way `size_chunk` counts it.

    The length is rounded up past the terminator: `(strlen + 4) & ~3`, which is
    why a fourteen-character name occupies sixteen bytes.
    """
    raw = name.encode("latin-1") + b"\x00"
    while len(raw) % 4:
        raw += b"\x00"
    return raw


class TrackSoundChunk(Chunk):
    """TRAKSOUN — the same values, laid out the other way round.

    `Object_Track_Sound_Chunk::fill_data_block` (`OBCHUNK.CPP:1318`) writes the
    six integers first and the name last, and there is no extra array:

        inner_range, outer_range, max_volume, pitch, flags, index, name
    """

    identifier = "TRAKSOUN"

    def __init__(self, wav_name, index=0, inner_range=0, outer_range=0,
                 max_volume=INDEXED_SOUND_MAX_VOLUME, pitch=0, loop=False):
        self.wav_name = str(wav_name)
        self.index = int(index)
        self.inner_range = int(inner_range)
        self.outer_range = int(outer_range)
        self.max_volume = int(max_volume)
        self.pitch = int(pitch)
        self.flags = INDEXED_SOUND_FLAG_LOOP if loop else 0

    def payload(self) -> bytes:
        """Serialise the six values, then the name."""
        buf = bytearray()
        buf += struct.pack("<6i", self.inner_range, self.outer_range,
                           self.max_volume, self.pitch, self.flags,
                           self.index)
        buf += pad_name(self.wav_name)
        return bytes(buf)


class IndexedSoundChunk(Chunk):
    """INDSOUND — an indexed sound belonging to another chunk.

    The index comes first and the name second, and an extra array follows the
    six values. `TRAKSOUN` is the same idea written differently; see
    `TrackSoundChunk`.

        index, name, inner_range, outer_range, max_volume, pitch, flags,
        num_extra_data, extra_data[]
    """

    identifier = "INDSOUND"

    def __init__(self, wav_name, index=0, inner_range=0, outer_range=0,
                 max_volume=INDEXED_SOUND_MAX_VOLUME, pitch=0, loop=False,
                 extra_data=()):
        self.wav_name = str(wav_name)
        self.index = int(index)
        self.inner_range = int(inner_range)
        self.outer_range = int(outer_range)
        self.max_volume = int(max_volume)
        self.pitch = int(pitch)
        self.flags = INDEXED_SOUND_FLAG_LOOP if loop else 0
        self.extra_data = [int(v) for v in extra_data]

    def payload(self) -> bytes:
        """Serialise the index, the name, the ranges and the extras."""
        buf = bytearray()
        buf += struct.pack("<i", self.index)
        buf += pad_name(self.wav_name)
        buf += struct.pack("<5i", self.inner_range, self.outer_range,
                           self.max_volume, self.pitch, self.flags)
        buf += struct.pack("<i", len(self.extra_data))
        for value in self.extra_data:
            buf += struct.pack("<i", value)
        return bytes(buf)
