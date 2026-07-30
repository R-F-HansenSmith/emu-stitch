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
