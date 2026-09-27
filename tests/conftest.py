"""Shared test setup."""

import pytest

import emu_stitch.ryujinx as ryujinx_mod


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Point HOME at a throwaway directory for every test, so nothing can
    read or write the real ~/.config (emu-stitch settings, Ryujinx's save
    index, systemd units) even if a test forgets to patch expanduser."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(ryujinx_mod, "PROC_DIR", str(home / "no-proc"))
    return home
