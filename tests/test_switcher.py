"""Tests for emu_stitch.switcher: profile migration, atomic symlink swap, idempotency."""

import json
import os

import pytest

import emu_stitch.switcher as switcher_mod
from emu_stitch.switcher import run_switch


@pytest.fixture
def fake_steam_user(monkeypatch):
    """Patch detect_active_steam_user to return a fixed, known user."""
    monkeypatch.setattr(
        switcher_mod, "detect_active_steam_user", lambda: ("123", "TestUser")
    )
    # Avoid touching real emulator config directories during tests.
    monkeypatch.setattr(switcher_mod, "configure_all_emulators", lambda *a, **k: None)


def test_run_switch_creates_profile_and_symlink(tmp_path, fake_steam_user):
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()

    profile_name, real_target = run_switch(str(emu_dir))

    assert profile_name == "TestUser"
    active_link = emu_dir / "saves"
    assert active_link.is_symlink()
    assert os.path.realpath(str(active_link)) == real_target
    assert os.path.isdir(real_target)


def test_run_switch_migrates_existing_real_saves_dir(tmp_path, fake_steam_user):
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()
    saves_dir = emu_dir / "saves"
    saves_dir.mkdir()
    (saves_dir / "existing_save.srm").write_text("important save data")

    profile_name, real_target = run_switch(str(emu_dir))

    migrated_file = os.path.join(real_target, "existing_save.srm")
    assert os.path.exists(migrated_file)
    assert open(migrated_file).read() == "important save data"


def test_run_switch_is_idempotent(tmp_path, fake_steam_user):
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()

    profile_name_1, target_1 = run_switch(str(emu_dir))
    profile_name_2, target_2 = run_switch(str(emu_dir))

    assert profile_name_1 == profile_name_2
    assert target_1 == target_2
    active_link = emu_dir / "saves"
    assert active_link.is_symlink()


def test_run_switch_persists_user_map(tmp_path, fake_steam_user):
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()

    run_switch(str(emu_dir))

    map_file = emu_dir / "saves_by_user" / "user_map.json"
    assert map_file.exists()
    user_map = json.loads(map_file.read_text())
    assert user_map["123"] == "TestUser"


def test_run_switch_leaves_no_leftover_tmp_symlink(tmp_path, fake_steam_user):
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()

    run_switch(str(emu_dir))

    entries = os.listdir(str(emu_dir))
    tmp_leftovers = [e for e in entries if ".tmp-" in e]
    assert tmp_leftovers == []
