"""Batch extraction and conversion of the game's textures, and a PNG writer.

Two independent jobs live here:

* turning one decoded :class:`~rif.iff.Ilbm` into a PNG file, and
* walking ``Tex<n>.FFL`` archives so every texture lands as a ``.png`` next to
  its ``.rim`` under ``<game>/fastfile/_ffl_unpacked``.

The PNG encoder is written out rather than pulled from a library because the
add-on must run inside Blender with no third-party packages available. Only the
three PNG filters that are actually useful for indexed art are offered, and
``filter=0`` is the default: speed matters more than a few saved kilobytes for
18 MB of small textures.

PNG files are written **beside** the ``.rim`` they came from, keeping the same
base name. That is deliberate: the game looks textures up by their ``.RIM``
name, so the exporter can keep writing the original name into ``BMPNAMES``
while Blender uses the ``.png``.
"""
from __future__ import annotations

import binascii
import struct
import zlib
from pathlib import Path

from . import fastfile
from .iff import IffError, parse_ilbm

# Where the unpacked archives go, relative to the game directory.
UNPACKED_DIRNAME = "_ffl_unpacked"

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# PNG filter type 0 (None). See the module docstring for why only this one.
_FILTER_NONE = 0


def _chunk(tag, payload):
    """Return one PNG chunk: length, tag, payload, CRC."""
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", binascii.crc32(tag + payload) & 0xFFFFFFFF))


def write_png_rgb(path, width, height, rgb, compress_level=6):
    """Write a 24-bit RGB PNG from tightly packed ``rgb`` bytes."""
    expected = width * height * 3
    if len(rgb) != expected:
        raise ValueError(f"expected {expected} bytes of RGB, got {len(rgb)}")
    return _write_png(path, width, height, 2, rgb, 3, compress_level)


def write_png_rgba(path, width, height, rgba, compress_level=6):
    """Write a 32-bit RGBA PNG from tightly packed ``rgba`` bytes."""
    expected = width * height * 4
    if len(rgba) != expected:
        raise ValueError(f"expected {expected} bytes of RGBA, got {len(rgba)}")
    return _write_png(path, width, height, 6, rgba, 4, compress_level)


def _write_png(path, width, height, colour_type, pixels, channels,
               compress_level):
    """Assemble and write a PNG with filter type 0 on every row."""
    stride = width * channels
    raw = bytearray()
    for y in range(height):
        raw.append(_FILTER_NONE)
        start = y * stride
        raw += pixels[start:start + stride]

    header = struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0)
    body = (_PNG_SIGNATURE
            + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), compress_level))
            + _chunk(b"IEND", b""))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write through a temporary file so an interrupted run cannot leave a
    # truncated PNG that the importer would happily load.
    temp = path.with_name(path.name + ".part")
    with open(temp, "wb") as handle:
        handle.write(body)
    temp.replace(path)
    return len(body)


def rim_to_png(payload, png_path):
    """Decode one ``.RIM`` payload and write it as a PNG.

    Returns ``(width, height, uses_transparency)``.
    """
    image = parse_ilbm(payload)
    if image.uses_transparency:
        write_png_rgba(png_path, image.width, image.height, image.rgba())
    else:
        write_png_rgb(png_path, image.width, image.height, image.rgb())
    return image.width, image.height, image.uses_transparency


class ConversionReport:
    """Tally of one conversion run."""

    def __init__(self):
        self.converted = 0
        self.skipped_existing = 0
        self.transparent = 0
        self.failed = []              # (path, reason)
        self.sizes = {}

    def __repr__(self):
        return (f"ConversionReport(converted={self.converted}, "
                f"existing={self.skipped_existing}, "
                f"transparent={self.transparent}, failed={len(self.failed)})")


def convert_tree(unpacked_root, progress=None, force=False):
    """Convert every ``.rim`` under ``unpacked_root`` to a sibling ``.png``.

    A ``.png`` that already exists is left alone unless ``force`` is set, so the
    one-off conversion can be resumed. A texture that cannot be decoded is
    recorded on the report with its reason instead of aborting the run: one
    unusual menu graphic must not stop the other thousand.
    """
    unpacked_root = Path(unpacked_root)
    report = ConversionReport()
    sources = sorted(p for p in unpacked_root.rglob("*")
                     if p.is_file() and p.suffix.lower() == ".rim")
    total = len(sources)

    for index, source in enumerate(sources, start=1):
        target = source.with_suffix(".png")
        if not force and target.exists():
            report.skipped_existing += 1
        else:
            try:
                width, height, transparent = rim_to_png(
                    source.read_bytes(), target)
            except (IffError, ValueError, OSError) as exc:
                report.failed.append((source, f"{type(exc).__name__}: {exc}"))
                if progress is not None:
                    progress(index, total, source.name)
                continue
            report.converted += 1
            if transparent:
                report.transparent += 1
            report.sizes[(width, height)] = report.sizes.get((width, height), 0) + 1
        if progress is not None:
            progress(index, total, source.name)

    return report


# Where the unpacked textures go. The names match the add-on preference enum.
UNPACK_IN_FFL_DIR = "FFL"
UNPACK_IN_GAME_DIR = "GAME"
UNPACK_IN_CUSTOM_DIR = "CUSTOM"


def unpacked_root_for(game_dir, mode=UNPACK_IN_FFL_DIR, custom_dir=""):
    """Return the folder the unpacked textures live in.

    `mode` is one of the UNPACK_IN_* constants. The default keeps the old
    location, so an existing installation finds its textures where it left them.
    """
    game_dir = Path(game_dir)
    # Only the original location gets a subfolder: the game root and a folder
    # the author picked are used as they are, because the author already chose
    # where the textures should go and a wrapper only hides them.
    if mode == UNPACK_IN_GAME_DIR:
        return game_dir
    if mode == UNPACK_IN_CUSTOM_DIR:
        if not str(custom_dir).strip():
            return game_dir / "fastfile" / UNPACKED_DIRNAME
        return Path(custom_dir)
    return game_dir / "fastfile" / UNPACKED_DIRNAME


def load_game_textures(game_dir, progress=None, force=False,
                       mode=UNPACK_IN_FFL_DIR, custom_dir=""):
    """Unpack every texture archive and convert the result to PNG.

    Returns ``(extraction, conversion)``. ``force`` re-does work that already
    exists on disk. `mode` and `custom_dir` choose the destination, and the
    caller must pass the same pair to `unpacked_root_for` when importing.
    """
    game_dir = Path(game_dir)
    archives, skipped_names = fastfile.find_texture_archives(
        game_dir / "fastfile")
    if not archives:
        raise fastfile.FastFileError(
            f"no Tex<n>.FFL archives in {game_dir / 'fastfile'}")

    destination = unpacked_root_for(game_dir, mode, custom_dir)
    extraction = fastfile.extract_all(
        archives, destination, progress=progress, force=force)
    extraction.skipped_archives = skipped_names
    conversion = convert_tree(destination, progress=progress, force=force)
    return extraction, conversion
