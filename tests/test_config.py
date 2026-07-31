"""Tests for emu_stitch.config: persistent preferences (backup retention)."""

import json
import os

import emu_stitch.config as config_mod
from emu_stitch.config import (
    DEFAULT_BACKUP_RETENTION,
    get_backup_retention,
    is_configured,
    load_config,
    save_config,
    set_backup_retention,
)


def _use_fake_home(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(fake_home)))
    return fake_home


def test_is_configured_false_before_any_save(tmp_path, monkeypatch):
    _use_fake_home(monkeypatch, tmp_path)
    assert is_configured() is False


def test_get_backup_retention_returns_default_when_unconfigured(tmp_path, monkeypatch):
    _use_fake_home(monkeypatch, tmp_path)
    assert get_backup_retention() == DEFAULT_BACKUP_RETENTION


def test_set_and_get_backup_retention_roundtrip(tmp_path, monkeypatch):
    fake_home = _use_fake_home(monkeypatch, tmp_path)

    set_backup_retention(5)

    assert is_configured() is True
    assert get_backup_retention() == 5
    on_disk = json.loads((fake_home / ".config" / "emu-stitch" / "config.json").read_text())
    assert on_disk["backup_retention"] == 5


def test_backup_retention_zero_means_keep_forever(tmp_path, monkeypatch):
    _use_fake_home(monkeypatch, tmp_path)

    set_backup_retention(0)

    assert get_backup_retention() == 0


def test_get_backup_retention_falls_back_to_default_on_invalid_value(tmp_path, monkeypatch):
    fake_home = _use_fake_home(monkeypatch, tmp_path)
    config_dir = fake_home / ".config" / "emu-stitch"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(json.dumps({"backup_retention": -1}))

    assert get_backup_retention() == DEFAULT_BACKUP_RETENTION


def test_load_config_returns_empty_dict_on_corrupted_file(tmp_path, monkeypatch):
    fake_home = _use_fake_home(monkeypatch, tmp_path)
    config_dir = fake_home / ".config" / "emu-stitch"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text("not valid json{{{")

    assert load_config() == {}


def test_save_config_write_does_not_corrupt_existing_file_on_failure(tmp_path, monkeypatch):
    fake_home = _use_fake_home(monkeypatch, tmp_path)
    config_dir = fake_home / ".config" / "emu-stitch"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.json"
    config_path.write_text(json.dumps({"backup_retention": 7}))

    import pytest
    monkeypatch.setattr(
        config_mod.os, "replace",
        lambda *a, **k: (_ for _ in ()).throw(OSError("simulated failure")),
    )

    with pytest.raises(OSError):
        save_config({"backup_retention": 99})

    assert json.loads(config_path.read_text()) == {"backup_retention": 7}
