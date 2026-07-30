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
