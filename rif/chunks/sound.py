"""SOUNDOB2 and SOUNDDIR — a sound placed in the world, and the level's folder.

A sound is the simplest thing the exporter writes: a position, two radii, a
volume, a pitch, and two names. No strategy, no links, no children.

Layout from `SNDCHUNK.HPP::size_chunk()` and the writer at `SNDCHUNK.CPP:74`,
confirmed against 2062 retail chunks with zero parse failures:

    +0   ChunkVectorInt position    3 x i32, millimetres
    +12  u32 inner_range            full volume inside this radius
    +16  u32 outer_range            silence outside it
    +20  i32 max_volume             0-127
    +24  i32 pitch                  1/1536ths of an octave
    +28  i32 flags
    +32  i32 probability
    +36  i32 pad3
    +40  char[] snd_name            NUL-terminated
         char[] wav_name            NUL-terminated, or a single NUL
         padding to a 4-byte boundary

`inner_range` is always smaller than `outer_range` in the shipping data, and the
engine has no handling for the reverse, so the operator warns rather than writes
it silently.

**Two names, two jobs.** `snd_name` is the name of the emitter in this level --
`sound1`, `comp`, `v4` in retail -- and the exporter takes it from the Blender
object name. `wav_name` is the file, resolved against `SOUNDDIR`. Keeping them
separate is what lets the same emitter be renamed without changing the sound.

`SOUNDDIR` (`ENVCHUNK.HPP:160`) is a single NUL-terminated path padded to four
bytes, one per level, sitting beside `SPECLOBJ`. Retail's values look like
`env\\Temple` and `env\\Caverns`: the level's own sound folder. Every one of the
62 retail levels that places a sound has one, so it is not optional.
"""
from __future__ import annotations

import struct

from ..chunk_writer import Chunk

# `SOUNDOB2` is 12 header + 12 position + 28 fields before the names.
FIXED_BYTES = 52

FLAG_NOT_PLAYING_AT_START = 0x00000001
FLAG_NO_LOOP = 0x00000002

MAX_VOLUME = 127


def _pad4(data: bytes) -> bytes:
    """Return `data` padded to a multiple of four bytes.

    The reference implementation writes `4 - size % 4`, which adds four bytes
    even when already aligned; the shipping data is all multiples of four with
    no such surplus, so the remainder is taken modulo four here.
    """
    return data + b"\x00" * (-len(data) % 4)


class SoundObjectChunk(Chunk):
    """SOUNDOB2 — one sound emitter."""

    identifier = "SOUNDOB2"

    def __init__(self, position, wav_name, snd_name="",
                 inner_range=5000, outer_range=20000, max_volume=MAX_VOLUME,
                 pitch=0, flags=0, probability=0):
        """Store one emitter.

        position : (x, y, z) in RIF millimetres.
        wav_name : the file, e.g. "wind2.wav", resolved against SOUNDDIR.
        snd_name : this level's name for the emitter.
        """
        self.position = tuple(position)
        self.wav_name = wav_name or ""
        self.snd_name = snd_name or ""
        self.inner_range = int(inner_range)
        self.outer_range = int(outer_range)
        self.max_volume = int(max_volume)
        self.pitch = int(pitch)
        self.flags = int(flags)
        self.probability = int(probability)

    def payload(self) -> bytes:
        """Serialise the emitter."""
        buf = bytearray()
        buf += struct.pack("<3i", *self.position)
        buf += struct.pack("<2I", self.inner_range & 0xFFFFFFFF,
                           self.outer_range & 0xFFFFFFFF)
        buf += struct.pack("<2i", self.max_volume, self.pitch)
        buf += struct.pack("<3i", self.flags, self.probability, 0)
        buf += self.snd_name.encode("latin-1", "replace") + b"\x00"
        # A missing file is written as a lone NUL rather than omitted.
        buf += (self.wav_name.encode("latin-1", "replace") + b"\x00"
                if self.wav_name else b"\x00")
        return _pad4(bytes(buf))


class SoundDirectoryChunk(Chunk):
    """SOUNDDIR — the folder this level's sounds come from.

    Retail uses the level's own name: `env\\Temple` for Temple.RIF. A custom
    level has no such folder, so the artist picks an existing one -- the sounds
    themselves all live in the shared `common.ffl`, so any folder works.
    """

    identifier = "SOUNDDIR"

    def __init__(self, directory):
        """Store the folder path."""
        self.directory = directory or ""

    def payload(self) -> bytes:
        """Serialise the path, padded to four bytes."""
        return _pad4(self.directory.encode("latin-1", "replace") + b"\x00")
