"""Tests for emu_stitch.emulators: installed-emulator detection and safe symlink replacement."""

import os
import shutil

import pytest

import emu_stitch.emulators as emulators_mod
from emu_stitch.emulators import _safe_replace_with_symlink, detect_installed_emulators


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
