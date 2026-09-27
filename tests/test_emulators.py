"""Tests for emu_stitch.emulators: installed-emulator detection and safe symlink replacement."""

import os
import shutil

import pytest

import subprocess

import emu_stitch.emulators as emulators_mod
from emu_stitch.emulators import (
    _safe_replace_with_symlink,
    audit_emulator_saves,
    detect_installed_emulators,
    is_flatpak_installed,
)


def test_safe_replace_migrates_real_directory_and_creates_backup(tmp_path):
    link_path = tmp_path / "bis_user_save"
    link_path.mkdir()
    (link_path / "File1.bin").write_bytes(b"precious save data")

    target_dir = tmp_path / "profile" / "ryujinx" / "saves"

    _safe_replace_with_symlink(str(link_path), str(target_dir))

    assert os.path.islink(str(link_path))
    assert os.path.realpath(str(link_path)) == os.path.realpath(str(target_dir))

    migrated_file = target_dir / "File1.bin"
    assert migrated_file.read_bytes() == b"precious save data"

    backups = [p for p in tmp_path.iterdir() if p.name.startswith("bis_user_save.bak-")]
    assert len(backups) == 1
    assert (backups[0] / "File1.bin").read_bytes() == b"precious save data"


def test_safe_replace_prunes_old_backups_beyond_configured_retention(tmp_path, monkeypatch):
    import emu_stitch.config as config_mod

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(fake_home)))
    config_mod.set_backup_retention(1)

    link_path = tmp_path / "bis_user_save"
    (tmp_path / "bis_user_save.bak-20260101-000000").mkdir()
    (tmp_path / "bis_user_save.bak-20260101-000000" / ".emu-stitch-merged").touch()
    link_path.mkdir()
    (link_path / "File1.bin").write_bytes(b"precious save data")

    target_dir = tmp_path / "profile" / "ryujinx" / "saves"
    _safe_replace_with_symlink(str(link_path), str(target_dir))

    backups = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("bis_user_save.bak-"))
    assert len(backups) == 1
    assert "bis_user_save.bak-20260101-000000" not in backups


def test_safe_replace_is_noop_when_already_correct_symlink(tmp_path):
    target_dir = tmp_path / "profile" / "ryujinx" / "saves"
    target_dir.mkdir(parents=True)
    link_path = tmp_path / "bis_user_save"
    os.symlink(str(target_dir), str(link_path))

    _safe_replace_with_symlink(str(link_path), str(target_dir))

    assert os.path.islink(str(link_path))
    assert os.readlink(str(link_path)) == str(target_dir)
    backups = [p for p in tmp_path.iterdir() if p.name.startswith("bis_user_save.bak-")]
    assert backups == []


def test_safe_replace_relinks_stale_symlink(tmp_path):
    old_target = tmp_path / "old_profile"
    old_target.mkdir()
    new_target = tmp_path / "new_profile"
    link_path = tmp_path / "bis_user_save"
    os.symlink(str(old_target), str(link_path))

    _safe_replace_with_symlink(str(link_path), str(new_target))

    assert os.path.islink(str(link_path))
    assert os.readlink(str(link_path)) == str(new_target)
    assert os.path.isdir(str(new_target))


def test_safe_replace_does_not_overwrite_existing_files_at_destination(tmp_path):
    link_path = tmp_path / "bis_user_save"
    link_path.mkdir()
    (link_path / "File1.bin").write_bytes(b"OLD DATA")

    target_dir = tmp_path / "profile" / "ryujinx" / "saves"
    target_dir.mkdir(parents=True)
    (target_dir / "File1.bin").write_bytes(b"NEWER DATA ALREADY THERE")

    _safe_replace_with_symlink(str(link_path), str(target_dir))

    # Destination file wins; source is preserved untouched in the backup.
    assert (target_dir / "File1.bin").read_bytes() == b"NEWER DATA ALREADY THERE"
    backups = [p for p in tmp_path.iterdir() if p.name.startswith("bis_user_save.bak-")]
    assert (backups[0] / "File1.bin").read_bytes() == b"OLD DATA"


def test_safe_replace_does_not_crash_when_target_dir_is_already_a_symlink(tmp_path):
    # Simulates EmuDeck pre-creating ryujinx/saves as a symlink before emu-stitch runs.
    real_dir = tmp_path / "real_saves"
    real_dir.mkdir()
    target_dir = tmp_path / "ryujinx" / "saves"
    target_dir.parent.mkdir()
    os.symlink(str(real_dir), str(target_dir))  # target_dir itself is a symlink

    link_path = str(tmp_path / "bis_user_save")

    # Must not raise FileExistsError
    _safe_replace_with_symlink(link_path, str(target_dir))

    assert os.path.islink(link_path)


def test_safe_replace_cleans_dangling_target_dir_symlink(tmp_path):
    # Simulates cross-machine synced dangling symlink inside profile (e.g. Cemu/saves -> /mnt/Storage/...)
    dangling_dest = tmp_path / "non_existent_mnt" / "Storage" / "save"
    target_dir = tmp_path / "profile" / "Cemu" / "saves"
    target_dir.parent.mkdir(parents=True)
    os.symlink(str(dangling_dest), str(target_dir))

    link_path = str(tmp_path / "cemu_save_link")

    _safe_replace_with_symlink(link_path, str(target_dir))

    assert not os.path.islink(str(target_dir))
    assert os.path.isdir(str(target_dir))
    assert os.path.islink(link_path)
    assert os.path.realpath(link_path) == os.path.realpath(str(target_dir))


def test_safe_replace_is_noop_when_target_reachable_through_symlink(tmp_path):
    # target_dir contains a symlink in its path (simulates saves -> saves_by_user/alice)
    real_saves = tmp_path / "saves_by_user" / "alice"
    real_saves.mkdir(parents=True)
    saves_link = tmp_path / "saves"
    os.symlink(str(real_saves), str(saves_link))

    # link_path is inside the saves symlink — this is the exact path passed for Ryujinx
    link_path = str(saves_link / "ryujinx" / "save")
    target_dir = str(saves_link / "ryujinx" / "saves")

    # Should not raise FileExistsError
    _safe_replace_with_symlink(link_path, target_dir)

    assert os.path.islink(link_path)
    # Calling again must be a no-op (idempotent)
    _safe_replace_with_symlink(link_path, target_dir)


def test_auto_mirror_ryujinx_payloads_removed():
    assert not hasattr(emulators_mod, "auto_mirror_ryujinx_payloads")


def test_detect_installed_emulators_all_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(tmp_path)))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(emulators_mod, "is_flatpak_installed", lambda app_id: False)

    result = detect_installed_emulators()

    assert result == {"ryujinx": False, "cemu": False}


def test_detect_installed_emulators_ryujinx_present_via_config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(tmp_path)))
    (tmp_path / ".config" / "Ryujinx").mkdir(parents=True)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(emulators_mod, "is_flatpak_installed", lambda app_id: False)

    result = detect_installed_emulators()

    assert result["ryujinx"] is True
    assert result["cemu"] is False


def test_detect_installed_emulators_cemu_present_via_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(tmp_path)))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/cemu" if name == "cemu" else None)
    monkeypatch.setattr(emulators_mod, "is_flatpak_installed", lambda app_id: False)

    result = detect_installed_emulators()

    assert result["cemu"] is True
    assert result["ryujinx"] is False


def test_is_flatpak_installed_passes_a_timeout_and_survives_a_hang(monkeypatch):
    """A hung `flatpak info` call must not hang the CLI forever."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["timeout"] = timeout
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert is_flatpak_installed("org.ryujinx.Ryujinx") is False
    assert captured["timeout"] is not None


def _write_ryujinx_extra_data(save_dir, title_id: int) -> None:
    import struct
    save_dir.mkdir(parents=True)
    (save_dir / "ExtraData0").write_bytes(struct.pack("<Q", title_id) + b"\x00" * 504)


def test_audit_emulator_saves_counts_unique_ryujinx_titles_not_save_folders(tmp_path):
    """Ryujinx can create multiple save-ID folders for the same title (e.g.
    one per in-emulator user profile) — the count must reflect distinct
    games, not raw save folders, and must not guess a game name."""
    active_link = tmp_path / "profile"
    ryu_saves = active_link / "ryujinx" / "saves"
    # Two save folders for the same game (Odyssey's real title ID) plus one
    # for a different game: 3 save folders, 2 unique games.
    _write_ryujinx_extra_data(ryu_saves / "0000000000000001", 0x0100000000010000)
    _write_ryujinx_extra_data(ryu_saves / "0000000000000002", 0x0100000000010000)
    _write_ryujinx_extra_data(ryu_saves / "0000000000000003", 0x01008cf01baac000)

    detected = audit_emulator_saves(str(active_link))
    ryu_entries = [d for d in detected if d["emulator"] == "Ryujinx (Switch)"]

    assert len(ryu_entries) == 1
    assert ryu_entries[0]["count"] == 2
    assert "Odyssey" not in ryu_entries[0]["name"]
    assert "2" in ryu_entries[0]["name"]


def test_audit_emulator_saves_only_counts_cemu_game_category_not_system_apps(tmp_path):
    """Wii U system applications (Mii Maker, Health & Safety Info, etc.) live
    under the 00050010 title-ID prefix and must not be counted as games —
    only the 00050000 (Game) category counts."""
    active_link = tmp_path / "profile"
    cemu_saves = active_link / "Cemu" / "saves"
    (cemu_saves / "00050010" / "1004a000").mkdir(parents=True)  # system app
    (cemu_saves / "00050010" / "1004a100").mkdir(parents=True)  # system app
    (cemu_saves / "00050000" / "101c9500").mkdir(parents=True)  # real game (BOTW)
    (cemu_saves / "system" / "pdm").mkdir(parents=True)

    detected = audit_emulator_saves(str(active_link))
    cemu_entries = [d for d in detected if d["emulator"] == "Cemu (Wii U)"]

    assert len(cemu_entries) == 1
    assert cemu_entries[0]["count"] == 1


def test_flatpak_ryujinx_is_routed_inside_its_sandbox_not_native_config(tmp_path, monkeypatch):
    """A Flatpak-only Ryujinx never reads ~/.config/Ryujinx, so routing must
    target its ~/.var/app copy and must not create the native directory."""
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(tmp_path)))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(emulators_mod, "is_flatpak_installed", lambda app_id: app_id == "org.ryujinx.Ryujinx")
    active_link = tmp_path / "Emulation" / "saves"
    active_link.mkdir(parents=True)

    emulators_mod.configure_all_emulators(str(tmp_path / "Emulation"), str(active_link), "alice")

    flatpak_save = tmp_path / ".var" / "app" / "org.ryujinx.Ryujinx" / "config" / "Ryujinx" / "bis" / "user" / "save"
    assert flatpak_save.is_symlink()
    assert not (tmp_path / ".config" / "Ryujinx").exists()


def test_flatpak_cemu_save_path(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(tmp_path)))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(emulators_mod, "is_flatpak_installed", lambda app_id: app_id == "info.cemu.Cemu")

    assert emulators_mod.cemu_save_paths() == [
        str(tmp_path / ".var" / "app" / "info.cemu.Cemu" / "data" / "Cemu" / "mlc01" / "usr" / "save")
    ]


def test_cemu_emudeck_path_only_used_when_roms_wiiu_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(tmp_path)))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/cemu" if name == "cemu" else None)
    monkeypatch.setattr(emulators_mod, "is_flatpak_installed", lambda app_id: False)
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()

    assert len(emulators_mod.cemu_save_paths(str(emu_dir))) == 1
    (emu_dir / "roms" / "wiiu").mkdir(parents=True)
    assert emulators_mod.cemu_save_paths(str(emu_dir))[-1] == str(emu_dir / "roms/wiiu/mlc01/usr/save")



class TestRyujinxIndexRouting:
    """The save index is swapped with the profile, like the saves themselves."""

    def _ryujinx(self, tmp_path, monkeypatch, running=False):
        import struct
        from emu_stitch.ryujinx import new_user_value, serialize_index
        import emu_stitch.ryujinx as ryu_mod
        ryu = tmp_path / "Ryujinx"
        key_a = struct.pack("<Q", 0x0100000000010000).ljust(0x40, b"\x00")
        for slot in ("0", "1"):
            d = ryu / "bis" / "system" / "save" / "8000000000000000" / slot
            d.mkdir(parents=True)
            (d / "imkvdb.arc").write_bytes(serialize_index([(key_a, new_user_value(1))]))
            (d / "lastPublishedId").write_bytes(struct.pack("<Q", 1))
        monkeypatch.setattr(emulators_mod, "ryujinx_config_dirs", lambda: [str(ryu)])
        monkeypatch.setattr(emulators_mod, "cemu_save_paths", lambda emu_dir=None: [])
        monkeypatch.setattr(emulators_mod, "ryujinx_running", lambda: running)
        monkeypatch.setattr(ryu_mod, "machine_range_base", lambda d: 0x1234 << 32)
        return ryu

    def _activate(self, emu_dir, name):
        profile = emu_dir / "saves_by_user" / name
        profile.mkdir(parents=True, exist_ok=True)
        link = emu_dir / "saves"
        if link.is_symlink():
            link.unlink()
        os.symlink(str(profile), str(link))
        return profile

    def test_index_moves_into_profile_and_swaps_on_switch(self, tmp_path, monkeypatch):
        ryu = self._ryujinx(tmp_path, monkeypatch)
        index_link = ryu / "bis" / "system" / "save" / "8000000000000000"
        original = (index_link / "0" / "imkvdb.arc").read_bytes()
        emu_dir = tmp_path / "Emulation"

        alice = self._activate(emu_dir, "alice")
        emulators_mod.configure_all_emulators(str(emu_dir), str(emu_dir / "saves"), "alice")
        assert index_link.is_symlink()
        assert os.path.realpath(str(index_link)) == str(alice / "ryujinx" / "saveIndex")
        assert (alice / "ryujinx" / "saveIndex" / "0" / "imkvdb.arc").read_bytes() == original
        # Ryujinx's own copy is kept as a backup, never deleted.
        assert any(p.name.startswith("8000000000000000.bak-") for p in index_link.parent.iterdir())

        bob = self._activate(emu_dir, "bob")
        emulators_mod.configure_all_emulators(str(emu_dir), str(emu_dir / "saves"), "bob")
        assert os.path.realpath(str(index_link)) == str(bob / "ryujinx" / "saveIndex")
        # A new profile starts from a copy of the previous index.
        assert (bob / "ryujinx" / "saveIndex" / "0" / "imkvdb.arc").read_bytes() == original

        emulators_mod.configure_all_emulators(str(emu_dir), str(emu_dir / "saves"), "bob")
        self._activate(emu_dir, "alice")
        emulators_mod.configure_all_emulators(str(emu_dir), str(emu_dir / "saves"), "alice")
        assert os.path.realpath(str(index_link)) == str(alice / "ryujinx" / "saveIndex")

    def test_counter_is_moved_onto_this_machines_range(self, tmp_path, monkeypatch):
        import struct
        self._ryujinx(tmp_path, monkeypatch)
        emu_dir = tmp_path / "Emulation"
        alice = self._activate(emu_dir, "alice")

        messages = emulators_mod.configure_all_emulators(str(emu_dir), str(emu_dir / "saves"), "alice")

        counter = (alice / "ryujinx" / "saveIndex" / "0" / "lastPublishedId").read_bytes()
        assert struct.unpack("<Q", counter)[0] == 0x1234 << 32
        assert any("numbered from" in m for m in messages)

    def test_nothing_is_routed_before_ryujinx_creates_its_index(self, tmp_path, monkeypatch):
        ryu = self._ryujinx(tmp_path, monkeypatch)
        shutil.rmtree(ryu / "bis" / "system" / "save" / "8000000000000000")
        emu_dir = tmp_path / "Emulation"
        alice = self._activate(emu_dir, "alice")

        emulators_mod.configure_all_emulators(str(emu_dir), str(emu_dir / "saves"), "alice")

        assert not (ryu / "bis" / "system" / "save" / "8000000000000000").exists()
        assert not (alice / "ryujinx" / "saveIndex").exists()

    def test_nothing_ryujinx_is_touched_while_it_runs(self, tmp_path, monkeypatch):
        ryu = self._ryujinx(tmp_path, monkeypatch, running=True)
        emu_dir = tmp_path / "Emulation"
        self._activate(emu_dir, "alice")

        messages = emulators_mod.configure_all_emulators(str(emu_dir), str(emu_dir / "saves"), "alice")

        assert "Ryujinx is running" in messages[0]
        assert not (ryu / "bis" / "system" / "save" / "8000000000000000").is_symlink()
        assert not (ryu / "bis" / "user" / "save").exists()


def test_audit_counts_saves_numbered_in_a_machine_range(tmp_path):
    """Save folders are numbered from each machine's own range (e.g.
    7a0444bb00000001), not just 00000000xxxxxxxx."""
    active_link = tmp_path / "profile"
    ryu_saves = active_link / "ryujinx" / "saves"
    _write_ryujinx_extra_data(ryu_saves / "7a0444bb00000001", 0x0100000000010000)
    _write_ryujinx_extra_data(ryu_saves / "7a0444bb00000002", 0x01008cf01baac000)
    _write_ryujinx_extra_data(ryu_saves / "0000000000000003", 0x01008cf01baac000)

    ryu = [d for d in audit_emulator_saves(str(active_link)) if d["emulator"] == "Ryujinx (Switch)"]

    assert ryu[0]["count"] == 2
    assert "across 3 save record(s)" in ryu[0]["details"]
