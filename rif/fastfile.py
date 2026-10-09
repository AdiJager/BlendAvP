"""Reader for the game's ``.FFL`` fastfiles (REBOUND "RFFL" archives).

Format, from the header comment in the game's own ``FFREAD.HPP``::

    1. Header
       1.1 DWORD "RFFL"        identifier
       1.2 DWORD version       always 0
       1.3 DWORD num_files
       1.4 DWORD header bytes
       1.5 DWORD data bytes
    2. File table (num_files entries)
       2.1 DWORD offset        relative to the start of the data area
       2.2 DWORD length
       2.3 STRING name         NUL-terminated
    3. Data                    files back to back

The stride between table entries is ``8 + ((strlen(name) + 4) & ~3)``, which is
what ``FFREAD.CPP`` computes. Note that the length field participates in the
alignment: for a name of 3 characters the stride is 12, not 16. Reading the
table with any other rule silently yields garbage names rather than an error.

Only the texture archives matter here. ``<game>/fastfile`` also holds sound
archives (``Snd*.FFL``, ``common.ffl``), and the texture ones are named
``Tex<n>.FFL`` with ``n`` from 1 to 58.

``ffinfo.txt`` is deliberately **not** consulted. It maps directory prefixes to
archive files, but the game only uses it to decide which archive to open first;
the real file names are stored inside each archive, and file lookups compare
names case-insensitively (``FFREAD.CPP``). Reading the archives directly is
both simpler and immune to a missing or edited index file.
"""
from __future__ import annotations

import os
import re
import struct
from pathlib import Path

HEADER = struct.Struct("<4siiii")
HEADER_SIZE = HEADER.size          # 20
ENTRY_FIXED = 8                    # offset + length

MAGIC = b"RFFL"
SUPPORTED_VERSION = 0

# Texture archives are Tex1.FFL .. Tex58.FFL.
TEXTURE_ARCHIVE = re.compile(r"^Tex(\d+)\.FFL$", re.IGNORECASE)
TEXTURE_ARCHIVE_MIN = 1
TEXTURE_ARCHIVE_MAX = 58


class FastFileError(ValueError):
    """Raised when an archive is not a readable RFFL file."""


class Entry:
    """One file inside an archive."""

    __slots__ = ("offset", "length", "name")

    def __init__(self, offset, length, name):
        self.offset = offset
        self.length = length
        self.name = name

    def __repr__(self):
        return f"Entry({self.name!r}, offset={self.offset}, length={self.length})"


def read_header(data):
    """Return ``(version, num_files, header_bytes, data_bytes)``."""
    if len(data) < HEADER_SIZE:
        raise FastFileError("file is shorter than an RFFL header")
    magic, version, num_files, header_bytes, data_bytes = HEADER.unpack_from(
        data, 0)
    if magic != MAGIC:
        raise FastFileError(f"expected {MAGIC!r}, found {magic!r}")
    if version != SUPPORTED_VERSION:
        raise FastFileError(f"unsupported RFFL version {version}")
    return version, num_files, header_bytes, data_bytes


def read_entries(data):
    """Return the archive's file table as a list of :class:`Entry`.

    The table is validated against the header: it must end exactly where the
    header says the data begins, and every file must fit inside the data area.
    Both checks catch a wrong stride immediately instead of producing plausible
    looking nonsense.
    """
    _version, num_files, header_bytes, data_bytes = read_header(data)
    table_end = HEADER_SIZE + header_bytes
    if table_end > len(data):
        raise FastFileError(
            f"header claims {header_bytes} bytes of table, only "
            f"{len(data) - HEADER_SIZE} available")

    entries = []
    pos = HEADER_SIZE
    for index in range(num_files):
        if pos + ENTRY_FIXED > table_end:
            raise FastFileError(
                f"file table truncated at entry {index} of {num_files}")
        offset, length = struct.unpack_from("<ii", data, pos)
        name_end = data.find(b"\0", pos + ENTRY_FIXED, table_end)
        if name_end < 0:
            raise FastFileError(f"entry {index} has no NUL-terminated name")
        name = data[pos + ENTRY_FIXED:name_end].decode("latin-1")
        if offset < 0 or length < 0:
            raise FastFileError(f"entry {index} ({name}) has negative bounds")
        if offset + length > data_bytes:
            raise FastFileError(
                f"entry {index} ({name}) spans {offset}+{length} bytes, past "
                f"the {data_bytes}-byte data area")
        entries.append(Entry(offset, length, name))
        pos += ENTRY_FIXED + ((len(name) + 4) & ~3)

    if pos != table_end:
        raise FastFileError(
            f"file table ended at {pos}, header said {table_end} "
            "(wrong stride?)")
    return entries


class FastFile:
    """One RFFL archive, loaded into memory.

    Archives are a few hundred kilobytes, so reading them whole keeps the
    lookup trivial and the code short.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        self.version, self.num_files, self.header_bytes, self.data_bytes = (
            read_header(self.data))
        self.entries = read_entries(self.data)

    @property
    def data_start(self):
        """Offset in the file where the data area begins."""
        return HEADER_SIZE + self.header_bytes

    def payload(self, entry):
        """Return the bytes of one entry."""
        start = self.data_start + entry.offset
        return self.data[start:start + entry.length]

    def names(self):
        """Return the stored names, in table order."""
        return [entry.name for entry in self.entries]

    def __repr__(self):
        return (f"FastFile({self.path.name!r}, {self.num_files} files, "
                f"{self.data_bytes} bytes of data)")


def find_texture_archives(fastfile_dir):
    """Return the ``Tex<n>.FFL`` archives, sorted by number.

    Returns ``(archives, skipped)`` where ``skipped`` lists the names that were
    left alone, so the caller can report them rather than dropping them
    silently. A ``Tex`` archive numbered outside 1..58 is treated as skipped
    too: the retail range is known and a stray file is more likely to be a
    leftover than a texture archive.
    """
    fastfile_dir = Path(fastfile_dir)
    if not fastfile_dir.is_dir():
        raise FastFileError(f"not a directory: {fastfile_dir}")

    numbered = []
    skipped = []
    for path in sorted(fastfile_dir.iterdir()):
        if not path.is_file():
            continue
        match = TEXTURE_ARCHIVE.match(path.name)
        if match is None:
            skipped.append(path.name)
            continue
        number = int(match.group(1))
        if not TEXTURE_ARCHIVE_MIN <= number <= TEXTURE_ARCHIVE_MAX:
            skipped.append(path.name)
            continue
        numbered.append((number, path))

    numbered.sort(key=lambda pair: pair[0])
    return [path for _number, path in numbered], skipped


def _write_atomic(target, payload):
    """Write ``payload`` to ``target`` via a temporary file.

    A run interrupted mid-way must not leave a half-written texture behind,
    because the loader would then trust a truncated image.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".part")
    with open(temp, "wb") as handle:
        handle.write(payload)
    os.replace(temp, target)


class ExtractionReport:
    """Tally of one extraction run."""

    def __init__(self):
        self.written = 0
        self.skipped_existing = 0
        self.duplicates = []          # (name, first_archive, later_archive)
        self.archives_read = 0
        self.skipped_archives = []

    def __repr__(self):
        return (f"ExtractionReport(written={self.written}, "
                f"existing={self.skipped_existing}, "
                f"duplicates={len(self.duplicates)}, "
                f"archives={self.archives_read})")


def extract_all(archives, destination, progress=None, force=False):
    """Extract every entry of every archive into ``destination``.

    Names are lower-cased because the game compares them case-insensitively
    (``_stricmp``); on a case-insensitive filesystem two names differing only
    in case would otherwise overwrite each other silently. A genuine duplicate
    -- the same name in two different archives -- is recorded on the report
    instead of being hidden.

    Existing files of the expected size are left alone, so an interrupted run
    can simply be repeated. ``progress`` is called with ``(done, total, name)``
    when supplied.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    report = ExtractionReport()
    report.skipped_archives = archives

    # Count first so progress can report a meaningful total.
    plans = []
    total_entries = 0
    for path in archives:
        archive = FastFile(path)
        report.archives_read += 1
        for entry in archive.entries:
            plans.append((archive, entry))
            total_entries += 1

    seen = {}
    for index, (archive, entry) in enumerate(plans, start=1):
        name = entry.name.replace("\\", "/").lstrip("/")
        relative = name.lower()
        previous = seen.get(relative)
        if previous is not None and previous != archive.path.name:
            report.duplicates.append((relative, previous, archive.path.name))
        seen[relative] = archive.path.name

        target = destination / relative
        if not force and target.exists() and target.stat().st_size == entry.length:
            report.skipped_existing += 1
        else:
            _write_atomic(target, archive.payload(entry))
            report.written += 1
        if progress is not None:
            progress(index, total_entries, relative)

    return report
