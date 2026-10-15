#!/usr/bin/env python3
"""v0.2 CLI contract tests — network-free.

Covers the v0.2 surface: the canonical ``--comment`` flag (with ``--notify``
kept as an alias), the ``--version`` flag, and that a run with no groups stays
clean. ``--limit 0`` short-circuits fetching, so these tests need neither
network nor gh credentials — they run anywhere, including CI.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("find_dupes.py")
spec = importlib.util.spec_from_file_location("fd", SCRIPT)
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)

BASE = ["--repo", "owner/name", "--limit", "0", "--no-cache"]


def _run(argv: list[str]) -> int:
    """Run ``main(argv)``, converting argparse's SystemExit into a return code."""
    try:
        return fd.main(argv)
    except SystemExit as e:  # argparse errors and --version exit this way
        return 0 if e.code is None else int(e.code)


def test_comment_flag_is_canonical():
    """``--comment`` must parse and exit cleanly (empty run -> nothing posted)."""
    assert _run(BASE + ["--comment", "--dry-run"]) == 0


def test_notify_flag_still_works_as_alias():
    """``--notify`` (the old name) must keep working after the unify."""
    assert _run(BASE + ["--notify", "--dry-run"]) == 0


def test_version_flag_prints_version():
    """``--version`` must print the tool version and exit 0."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = _run(["--version"])
    out = buf.getvalue()
    assert code == 0, f"--version exited {code}"
    assert "0.2.0" in out, f"version not in output: {out!r}"


def test_dry_run_alone_is_fine():
    """``--dry-run`` without ``--comment`` must be a harmless no-op."""
    assert _run(BASE + ["--dry-run"]) == 0


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  [ok  ] {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  [FAIL] {name}: {type(e).__name__}: {e}")
    print(f"\n  {'all tests passed' if not failed else str(failed) + ' failed'}")
    sys.exit(1 if failed else 0)
