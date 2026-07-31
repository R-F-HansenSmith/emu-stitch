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


def test_user_map_write_does_not_corrupt_existing_file_on_failure(tmp_path, fake_steam_user, monkeypatch):
    """If the final rename step fails, the on-disk map must remain the
    last-known-good JSON, never a partial/corrupted write."""
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()
    saves_base = emu_dir / "saves_by_user"
    saves_base.mkdir()
    map_file = saves_base / "user_map.json"
    map_file.write_text(json.dumps({"999": "OldUser"}, indent=2))

    monkeypatch.setattr(
        switcher_mod.os, "replace",
        lambda *a, **k: (_ for _ in ()).throw(OSError("simulated failure")),
    )

    with pytest.raises(OSError):
        run_switch(str(emu_dir))

    assert json.loads(map_file.read_text()) == {"999": "OldUser"}


def test_run_switch_leaves_no_leftover_tmp_symlink(tmp_path, fake_steam_user):
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()

    run_switch(str(emu_dir))

    entries = os.listdir(str(emu_dir))
    tmp_leftovers = [e for e in entries if ".tmp-" in e]
    assert tmp_leftovers == []


def test_run_switch_prunes_old_backups_beyond_configured_retention(tmp_path, fake_steam_user, monkeypatch):
    import emu_stitch.config as config_mod

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(fake_home)))
    config_mod.set_backup_retention(2)

    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()
    # Two pre-existing backups from earlier migrations.
    (emu_dir / "saves.bak-20260101-000000").mkdir()
    (emu_dir / "saves.bak-20260102-000000").mkdir()
    saves_dir = emu_dir / "saves"
    saves_dir.mkdir()
    (saves_dir / "existing.srm").write_text("data")

    run_switch(str(emu_dir))

    backups = sorted(p.name for p in emu_dir.iterdir() if p.name.startswith("saves.bak-"))
    assert len(backups) == 2
    assert "saves.bak-20260101-000000" not in backups
    assert "saves.bak-20260102-000000" in backups


def test_run_switch_migration_leaves_backup_not_deletes(tmp_path, fake_steam_user):
    """Original saves dir must be renamed to a backup, never deleted."""
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()
    saves_dir = emu_dir / "saves"
    saves_dir.mkdir()
    (saves_dir / "precious.srm").write_text("very important save")

    run_switch(str(emu_dir))

    # The original 'saves' real directory must no longer exist at the original path
    assert not saves_dir.exists() or (saves_dir.exists() and saves_dir.is_symlink())

    # A timestamped backup must exist
    backups = [p for p in emu_dir.iterdir() if p.name.startswith("saves.bak-")]
    assert len(backups) == 1
    assert (backups[0] / "precious.srm").read_text() == "very important save"


def test_run_switch_recognizes_correct_relative_symlink(tmp_path, fake_steam_user, monkeypatch):
    """A relative `saves` symlink must be resolved relative to its own
    directory, not the process CWD, when deciding whether it already points
    at the right profile."""
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()
    saves_base = emu_dir / "saves_by_user"
    saves_base.mkdir()
    target_dir = saves_base / "TestUser"
    target_dir.mkdir()

    active_link = emu_dir / "saves"
    relative_target = os.path.join("saves_by_user", "TestUser")
    os.symlink(relative_target, str(active_link))

    # Run from an unrelated CWD so a CWD-relative abspath computation would
    # incorrectly conclude the symlink points elsewhere.
    monkeypatch.chdir(tmp_path)

    run_switch(str(emu_dir))

    assert os.readlink(str(active_link)) == relative_target


def test_setup_systemd_watcher_creates_unit_files(tmp_path, monkeypatch):
    import subprocess
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(fake_home)))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, "", ""))

    from emu_stitch.switcher import setup_systemd_watcher
    ok, msg = setup_systemd_watcher()

    assert ok is True
    path_file = fake_home / ".config" / "systemd" / "user" / "emu-stitch-watcher.path"
    service_file = fake_home / ".config" / "systemd" / "user" / "emu-stitch-watcher.service"

    assert path_file.exists()
    assert service_file.exists()
    assert "loginusers.vdf" in path_file.read_text()
    assert "emu-stitch switch" in service_file.read_text()


def test_setup_systemd_watcher_survives_a_hung_systemctl(tmp_path, monkeypatch):
    """A hung `systemctl` call must not hang the CLI forever, and must be
    reported as a clean failure rather than an unhandled exception."""
    import subprocess

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(fake_home)))

    captured = {}

    def fake_run(cmd, capture_output=False, text=False, timeout=None):
        captured["timeout"] = timeout
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    from emu_stitch.switcher import setup_systemd_watcher
    ok, msg = setup_systemd_watcher()

    assert ok is False
    assert captured["timeout"] is not None


class TestListProfiles:
    def test_lists_all_profile_directories_and_marks_active_one(self, tmp_path, fake_steam_user):
        emu_dir = tmp_path / "Emulation"
        emu_dir.mkdir()

        run_switch(str(emu_dir))  # creates the "TestUser" profile and activates it
        saves_base = emu_dir / "saves_by_user"
        (saves_base / "bob").mkdir()

        from emu_stitch.switcher import list_profiles
        profiles = list_profiles(str(emu_dir))

        names = {p["name"]: p["active"] for p in profiles}
        assert names == {"TestUser": True, "bob": False}

    def test_ignores_user_map_json_and_other_files(self, tmp_path):
        emu_dir = tmp_path / "Emulation"
        saves_base = emu_dir / "saves_by_user"
        saves_base.mkdir(parents=True)
        (saves_base / "user_map.json").write_text("{}")
        (saves_base / "alice").mkdir()

        from emu_stitch.switcher import list_profiles
        profiles = list_profiles(str(emu_dir))

        assert [p["name"] for p in profiles] == ["alice"]

    def test_returns_empty_list_when_no_profiles_exist_yet(self, tmp_path):
        emu_dir = tmp_path / "Emulation"
        emu_dir.mkdir()

        from emu_stitch.switcher import list_profiles
        profiles = list_profiles(str(emu_dir))

        assert profiles == []
