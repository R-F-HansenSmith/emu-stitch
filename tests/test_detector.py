"""Tests for emu_stitch.detector: VDF parsing, name sanitization, dir/user detection."""

import os
import textwrap

import pytest

from emu_stitch.detector import (
    sanitize_name,
    extract_account_name,
    detect_emulation_dir,
    detect_active_steam_user,
)


def test_sanitize_name_replaces_invalid_chars():
    assert sanitize_name("Player One!") == "Player_One"


def test_sanitize_name_strips_leading_trailing_underscores():
    assert sanitize_name("__weird__name__") == "weird__name"


def test_sanitize_name_empty_falls_back_to_default_user():
    assert sanitize_name("") == "Default_User"


def test_sanitize_name_only_invalid_chars_falls_back_to_default_user():
    assert sanitize_name("!!!") == "Default_User"


def test_extract_account_name_prefers_account_name():
    block = '"AccountName"\t\t"realuser"\n"PersonaName"\t\t"CoolGamerTag"'
    assert extract_account_name(block) == "realuser"


def test_extract_account_name_falls_back_to_persona_name():
    block = '"PersonaName"\t\t"CoolGamerTag"'
    assert extract_account_name(block) == "CoolGamerTag"


def test_extract_account_name_returns_none_when_absent():
    block = '"Timestamp"\t\t"1700000000"'
    assert extract_account_name(block) is None


def test_detect_emulation_dir_uses_env_var(tmp_path, monkeypatch):
    custom_dir = tmp_path / "CustomEmulation"
    custom_dir.mkdir()
    monkeypatch.setenv("EMU_DIR", str(custom_dir))
    monkeypatch.delenv("EMUDECK_DIR", raising=False)

    result = detect_emulation_dir()

    assert result == str(custom_dir)


def test_detect_emulation_dir_falls_back_to_home_emulation(tmp_path, monkeypatch):
    monkeypatch.delenv("EMU_DIR", raising=False)
    monkeypatch.delenv("EMUDECK_DIR", raising=False)
    fake_home = tmp_path / "home"
    (fake_home / "Emulation").mkdir(parents=True)
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(fake_home)))

    result = detect_emulation_dir()

    assert result == str(fake_home / "Emulation")


LOGINUSERS_VDF_TEMPLATE = textwrap.dedent(
    """\
    "users"
    {
        "76561197960287930"
        {
            "AccountName"		"olduser"
            "PersonaName"		"OldUser"
            "MostRecent"		"0"
            "Timestamp"		"1600000000"
        }
        "76561197960287931"
        {
            "AccountName"		"newuser"
            "PersonaName"		"NewUser"
            "MostRecent"		"1"
            "Timestamp"		"1700000000"
        }
    }
    """
)


def _patch_steam_paths(monkeypatch, tmp_path, vdf_content):
    steam_config_dir = tmp_path / ".local" / "share" / "Steam" / "config"
    steam_config_dir.mkdir(parents=True)
    vdf_path = steam_config_dir / "loginusers.vdf"
    vdf_path.write_text(vdf_content)

    userdata_dir = tmp_path / ".local" / "share" / "Steam" / "userdata"
    userdata_dir.mkdir(parents=True)

    def fake_expanduser(path):
        return path.replace("~", str(tmp_path))

    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)
    return vdf_path, userdata_dir


def test_detect_active_steam_user_picks_most_recent_by_flag_and_timestamp(tmp_path, monkeypatch):
    _patch_steam_paths(monkeypatch, tmp_path, LOGINUSERS_VDF_TEMPLATE)

    steamid3, account_name = detect_active_steam_user()

    # 76561197960287931 - 76561197960265728 = 22203
    assert steamid3 == "22203"
    assert account_name == "newuser"


def test_detect_active_steam_user_falls_back_to_userdata_mtime(tmp_path, monkeypatch):
    _patch_steam_paths(monkeypatch, tmp_path, vdf_content="")

    userdata_dir = tmp_path / ".local" / "share" / "Steam" / "userdata"
    old_dir = userdata_dir / "111"
    new_dir = userdata_dir / "222"
    old_dir.mkdir()
    new_dir.mkdir()

    older_time = 1_000_000_000
    newer_time = 2_000_000_000
    os.utime(old_dir, (older_time, older_time))
    os.utime(new_dir, (newer_time, newer_time))

    steamid3, account_name = detect_active_steam_user()

    assert steamid3 == "222"
    assert account_name == "User_222"
