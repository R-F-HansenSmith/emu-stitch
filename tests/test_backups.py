"""Tests for emu_stitch.backups: rolling retention of .bak-* directories."""

import os

from emu_stitch.backups import prune_old_backups


def _make_backup_dir(base_dir, name, suffix, marker_content="data"):
    backup_dir = base_dir / f"{name}.bak-{suffix}"
    backup_dir.mkdir()
    (backup_dir / "marker.txt").write_text(marker_content)
    return backup_dir


def test_keeps_only_the_n_most_recent_backups(tmp_path):
    for suffix in ["20260101-000000", "20260102-000000", "20260103-000000", "20260104-000000"]:
        _make_backup_dir(tmp_path, "saves", suffix)

    prune_old_backups(str(tmp_path / "saves"), retention=2)

    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["saves.bak-20260103-000000", "saves.bak-20260104-000000"]


def test_retention_zero_keeps_everything(tmp_path):
    for suffix in ["20260101-000000", "20260102-000000", "20260103-000000"]:
        _make_backup_dir(tmp_path, "saves", suffix)

    prune_old_backups(str(tmp_path / "saves"), retention=0)

    assert len(list(tmp_path.iterdir())) == 3


def test_noop_when_backup_count_is_at_or_below_retention(tmp_path):
    _make_backup_dir(tmp_path, "saves", "20260101-000000")
    _make_backup_dir(tmp_path, "saves", "20260102-000000")

    prune_old_backups(str(tmp_path / "saves"), retention=3)

    assert len(list(tmp_path.iterdir())) == 2


def test_prunes_file_backups_not_just_directories(tmp_path):
    for suffix in ["20260101-000000", "20260102-000000", "20260103-000000"]:
        (tmp_path / f"config.txt.bak-{suffix}").write_text("old contents")

    prune_old_backups(str(tmp_path / "config.txt"), retention=1)

    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["config.txt.bak-20260103-000000"]


def test_does_not_touch_unrelated_files_with_similar_names(tmp_path):
    _make_backup_dir(tmp_path, "saves", "20260101-000000")
    _make_backup_dir(tmp_path, "saves", "20260102-000000")
    # A sibling backup for a *different* base name must be untouched.
    _make_backup_dir(tmp_path, "saves_extra", "20260101-000000")

    prune_old_backups(str(tmp_path / "saves"), retention=1)

    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["saves.bak-20260102-000000", "saves_extra.bak-20260101-000000"]


def test_preserves_newest_backup_contents(tmp_path):
    _make_backup_dir(tmp_path, "saves", "20260101-000000", marker_content="old")
    _make_backup_dir(tmp_path, "saves", "20260102-000000", marker_content="newest")

    prune_old_backups(str(tmp_path / "saves"), retention=1)

    remaining = list(tmp_path.iterdir())
    assert len(remaining) == 1
    assert (remaining[0] / "marker.txt").read_text() == "newest"


def test_missing_parent_directory_does_not_raise(tmp_path):
    prune_old_backups(str(tmp_path / "does-not-exist" / "saves"), retention=1)
