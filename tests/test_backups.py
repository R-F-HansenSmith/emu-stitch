"""Tests for emu_stitch.backups: merge, migration and rolling retention of .bak-* directories."""

import os

import pytest

import emu_stitch.backups as backups_mod
from emu_stitch.backups import MERGED_MARKER, merge_tree, migrate_to_backup, prune_old_backups


def _make_backup_dir(base_dir, name, suffix, marker_content="data", merged=True):
    backup_dir = base_dir / f"{name}.bak-{suffix}"
    backup_dir.mkdir()
    (backup_dir / "marker.txt").write_text(marker_content)
    if merged:
        (backup_dir / MERGED_MARKER).touch()
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


def test_never_prunes_backups_that_are_not_marked_as_merged(tmp_path):
    """A backup without the merged marker may hold the only copy of a save
    (or predates the marker), so it must survive pruning."""
    _make_backup_dir(tmp_path, "saves", "20260101-000000", merged=False)
    _make_backup_dir(tmp_path, "saves", "20260102-000000")
    _make_backup_dir(tmp_path, "saves", "20260103-000000")

    prune_old_backups(str(tmp_path / "saves"), retention=1)

    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["saves.bak-20260101-000000", "saves.bak-20260103-000000"]


def test_never_prunes_file_backups(tmp_path):
    """A file backup was never merged anywhere, so it's the only copy."""
    for suffix in ["20260101-000000", "20260102-000000", "20260103-000000"]:
        (tmp_path / f"config.txt.bak-{suffix}").write_text("old contents")

    prune_old_backups(str(tmp_path / "config.txt"), retention=1)

    assert len(list(tmp_path.iterdir())) == 3


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


class TestMergeTree:
    def test_copies_missing_files_and_reports_no_conflicts(self, tmp_path):
        src, dst = tmp_path / "src", tmp_path / "dst"
        (src / "sub").mkdir(parents=True)
        (src / "sub" / "a.sav").write_bytes(b"A")
        (dst / "sub").mkdir(parents=True)
        (dst / "sub" / "b.sav").write_bytes(b"B")

        assert merge_tree(str(src), str(dst)) == 0
        assert (dst / "sub" / "a.sav").read_bytes() == b"A"
        assert (dst / "sub" / "b.sav").read_bytes() == b"B"

    def test_identical_existing_files_are_not_conflicts(self, tmp_path):
        src, dst = tmp_path / "src", tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "a.sav").write_bytes(b"same")
        (dst / "a.sav").write_bytes(b"same")

        assert merge_tree(str(src), str(dst)) == 0

    def test_differing_existing_file_is_a_conflict_and_destination_wins(self, tmp_path):
        src, dst = tmp_path / "src", tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "a.sav").write_bytes(b"old")
        (dst / "a.sav").write_bytes(b"new")

        assert merge_tree(str(src), str(dst)) == 1
        assert (dst / "a.sav").read_bytes() == b"new"

    def test_copies_symlinks_as_symlinks(self, tmp_path):
        src, dst = tmp_path / "src", tmp_path / "dst"
        src.mkdir()
        os.symlink("/nonexistent/target", str(src / "link"))

        assert merge_tree(str(src), str(dst)) == 0
        assert os.readlink(str(dst / "link")) == "/nonexistent/target"


class TestMigrateToBackup:
    @pytest.fixture(autouse=True)
    def _retention(self, monkeypatch):
        monkeypatch.setattr(backups_mod, "get_backup_retention", lambda: 1)

    def test_clean_merge_marks_backup_as_prunable(self, tmp_path):
        path = tmp_path / "saves"
        path.mkdir()
        (path / "a.sav").write_bytes(b"A")

        backup = migrate_to_backup(str(path), str(tmp_path / "profile"))

        assert not path.exists()
        assert os.path.isfile(os.path.join(backup, MERGED_MARKER))
        assert (tmp_path / "profile" / "a.sav").read_bytes() == b"A"

    def test_conflicting_merge_leaves_backup_unmarked_and_it_survives_pruning(self, tmp_path):
        profile = tmp_path / "profile"
        profile.mkdir()
        (profile / "a.sav").write_bytes(b"profile version")

        path = tmp_path / "saves"
        path.mkdir()
        (path / "a.sav").write_bytes(b"only copy of this version")
        conflicted_backup = migrate_to_backup(str(path), str(profile))

        # Two more clean migrations with retention=1 must not prune it.
        for _ in range(2):
            path.mkdir()
            migrate_to_backup(str(path), str(profile))

        assert not os.path.exists(os.path.join(conflicted_backup, MERGED_MARKER))
        assert open(os.path.join(conflicted_backup, "a.sav"), "rb").read() == b"only copy of this version"

    def test_same_second_backups_do_not_collide(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backups_mod, "get_backup_retention", lambda: 0)
        profile = tmp_path / "profile"
        path = tmp_path / "saves"
        backups = set()
        for i in range(3):
            path.mkdir()
            (path / f"{i}.sav").write_bytes(b"x")
            backups.add(migrate_to_backup(str(path), str(profile)))

        assert len(backups) == 3
        assert all(os.path.isdir(b) for b in backups)
