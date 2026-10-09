"""Reload the RIF exporter test suite from disk, then run it.

Paste this into Blender's Text Editor and press Run Script (Alt+P), or open it
with Text > Open and run it.

Why this exists: the test suite also lives as a Text datablock inside the
.blend file, and that copy is a *snapshot*. Editing
`avp_rif_exporter/tests/blender_roundtrip.py` on disk does not update the
datablock, so running the datablock keeps executing the old code and keeps
reporting failures that were already fixed. This script imports the module
straight from the file on disk instead, using Blender's own reload machinery.

Every run also writes its full output to a timestamped `.log` next to this
file, so a result can be read back without copying from the console, and the
newest log is always at `run_tests_latest.log`.
"""
import importlib
import os
import sys
import time
import traceback

TESTS_DIR = r"G:\RIF Exporter\avp_rif_exporter"
MODULE = "tests.blender_roundtrip"
# Where logs are written: the tests folder, beside blender_roundtrip.py.
#
# This CANNOT be derived from `__file__`. When Blender runs this from a Text
# datablock, `__file__` is a virtual path inside the .blend
# ("...\blends\AvP.blend\run_tests_from_disk.py.003"), so the directory does not
# exist and opening a log there raises FileNotFoundError. `TESTS_DIR` above is
# already the authoritative location — keep both consistent.
LOG_DIR = TESTS_DIR + r"\tests"


def log_path():
    """Return (timestamped log path, latest-log path) in the tests folder."""
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    return (os.path.join(LOG_DIR, f"run_tests_{stamp}.log"),
            os.path.join(LOG_DIR, "run_tests_latest.log"))


class Tee:
    """File-like object that writes to the console and to a log file at once.

    Blender's Text Editor keeps its own output buffer, so a log written only
    there is easy to lose; and writing only to the file would hide the result
    while the tests run. Duplicating keeps both useful. Flushing every write
    matters because Blender crashes have already truncated runs mid-way, and a
    log that stops at the crash is more valuable than one that loses the buffer.
    """

    def __init__(self, stream, handle):
        self._stream = stream
        self._handle = handle

    def write(self, text):
        """Write to both destinations, tolerating a dead console stream."""
        try:
            self._stream.write(text)
        except (ValueError, OSError):
            pass
        try:
            self._handle.write(text)
            self._handle.flush()
        except (ValueError, OSError):
            pass
        return len(text)

    def flush(self):
        """Flush both destinations."""
        for target in (self._stream, self._handle):
            try:
                target.flush()
            except (ValueError, OSError, AttributeError):
                pass

    def isatty(self):
        """Report console-ness from the real stream, if it has the notion."""
        return bool(getattr(self._stream, "isatty", lambda: False)())


def report_loaded_modules(module):
    """Print which files the tests are actually executing.

    A long-lived Blender session keeps the add-on's modules in `sys.modules`, so
    without this it is impossible to tell a real failure from a stale one — and
    that ambiguity has already cost several rounds of chasing fixed bugs. The
    timestamp makes it obvious at a glance.
    """
    from datetime import datetime

    print("# --- kod pod testem ---")
    # The suite imports these as top-level modules (see
    # `load_package_module`), so look them up under both names.
    for name in ("rif", "rif.builder", "rif.types", "rif.lighting",
                 "rif.texture_library"):
        module_file = None
        for candidate in (name, f"avp_rif_exporter.{name}"):
            loaded = sys.modules.get(candidate)
            if loaded is not None and getattr(loaded, "__file__", None):
                module_file = loaded.__file__
                break
        if not module_file:
            print(f"#   {name}: NIE ZALADOWANY")
            continue
        try:
            when = datetime.fromtimestamp(os.path.getmtime(module_file))
            print(f"#   {name}: {module_file}  ({when:%Y-%m-%d %H:%M:%S})")
        except OSError:
            print(f"#   {name}: {module_file}")
    print(f"#   testy: {module.__file__}")


def run_tests():
    """Import (or re-import) the on-disk test module and run every test."""
    if TESTS_DIR not in sys.path:
        sys.path.insert(0, TESTS_DIR)

    # Drop the cached module and any stale package entry so the import below
    # reads the file again rather than a previously compiled copy. The add-on's
    # own modules are refreshed by `load_package_module` inside the test file.
    for name in list(sys.modules):
        if name == "tests" or name.startswith("tests."):
            del sys.modules[name]
    importlib.invalidate_caches()

    try:
        module = importlib.import_module(MODULE)
    except Exception:
        traceback.print_exc()
        print(f"FAILED to import {MODULE} from {TESTS_DIR}")
        return 1

    print(f"# loaded {module.__file__}")
    report_loaded_modules(module)
    runner = getattr(module, "run_all", None)
    if runner is None:
        print("FAILED: the module has no run_all(); nothing was executed")
        return 1
    return runner()


def open_log(path=None, latest=None):
    """Open the log for writing, falling back to a temp dir on any failure.

    Returns (timestamped_path, latest_path, handle). Logging must never be the
    reason a test run fails: if the folder is missing, read-only, or on a
    disconnected drive, the suite still has to run and the log goes somewhere
    reachable instead.

    `path` and `latest` default to `log_path()`. They are parameters rather than
    being computed here so a caller (or a test) can redirect the log without
    patching the module.
    """
    import tempfile

    if path is None or latest is None:
        path, latest = log_path()
    directories = [os.path.dirname(path)]
    if os.path.dirname(latest) not in directories:
        directories.append(os.path.dirname(latest))
    directories.append(tempfile.gettempdir())

    for directory in directories:
        candidate = os.path.join(directory, os.path.basename(path))
        latest_candidate = os.path.join(directory, os.path.basename(latest))
        try:
            os.makedirs(directory, exist_ok=True)
            return candidate, latest_candidate, \
                open(candidate, "w", encoding="utf-8")
        except OSError as exc:
            print(f"# nie moge pisac logu w {directory}: {exc}")
    # Last resort: no file at all, but the console still works.
    return None, None, None


def main():
    """Run the suite with stdout/stderr mirrored into a timestamped log."""
    path, latest, handle = open_log()
    original_out, original_err = sys.stdout, sys.stderr
    result = 1
    try:
        if handle is not None:
            sys.stdout = Tee(original_out, handle)
            sys.stderr = Tee(original_err, handle)
        print(f"# start: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        result = run_tests()
        print("# wynik: " + ("OK" if result in (0, True) else "BLAD"))
    except BaseException:
        # A crash or a manual stop must still leave a usable log.
        traceback.print_exc()
        print("# przerwane")
        raise
    finally:
        sys.stdout, sys.stderr = original_out, original_err
        if handle is not None:
            handle.close()
            try:
                with open(path, encoding="utf-8") as source, \
                        open(latest, "w", encoding="utf-8") as target:
                    target.write(source.read())
            except OSError:
                pass
            print(f"# log zapisany: {path}")
    return result


if __name__ == "__main__":
    main()
