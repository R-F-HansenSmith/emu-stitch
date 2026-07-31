"""Tests for emu_stitch.cli: argument dispatch and top-level error handling."""

import sys

import pytest

import emu_stitch.cli as cli_mod


def test_main_reports_unexpected_errors_cleanly_instead_of_crashing(monkeypatch, capsys):
    """An unexpected exception from a command handler must produce a clean
    error message and exit(1), not a raw traceback to the end user."""
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "switch"])
    monkeypatch.setattr(cli_mod, "detect_emulation_dir", lambda: "/tmp/does-not-matter")

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli_mod, "run_switch", boom)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "boom" in captured.err
