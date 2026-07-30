"""Tests for emu_stitch.fstab: mount-point boundary matching and /home short-circuit."""

import subprocess

import pytest

from emu_stitch.fstab import audit_mount_permissions


FAKE_MOUNT_OUTPUT = (
    "/dev/sda1 on /mnt/deck type ext4 (rw,relatime)\n"
    "/dev/sdb1 on /mnt/deck2 type ext4 (rw,noexec,relatime)\n"
)


def test_audit_mount_permissions_does_not_confuse_similarly_prefixed_mounts(monkeypatch):
    def fake_run(cmd, capture_output, text, check):
        assert cmd == ["mount"]
        return subprocess.CompletedProcess(cmd, 0, stdout=FAKE_MOUNT_OUTPUT, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    # /mnt/deck2/Emulation should match the /mnt/deck2 (noexec) mount,
    # NOT the /mnt/deck (exec) mount, despite the string-prefix overlap.
    is_noexec, mount_pt, msg = audit_mount_permissions("/mnt/deck2/Emulation")

    assert mount_pt == "/mnt/deck2"
    assert is_noexec is True


# Only a single mount point (/mnt/dec) exists here, with no longer /mnt/dec2
# mount to win on length. This isolates the naive-prefix bug: a target path
# under an unrelated similarly-named directory (/mnt/dec2) must NOT be
# attributed to the /mnt/dec mount at all.
SINGLE_MOUNT_OUTPUT = "/dev/sda1 on /mnt/dec type ext4 (rw,noexec,relatime)\n"


def test_audit_mount_permissions_does_not_match_unrelated_sibling_directory(monkeypatch):
    def fake_run(cmd, capture_output, text, check):
        return subprocess.CompletedProcess(cmd, 0, stdout=SINGLE_MOUNT_OUTPUT, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    # /mnt/dec2 is an unrelated directory that happens to share the "/mnt/dec"
    # string prefix. It must NOT be reported as noexec via the /mnt/dec mount.
    is_noexec, mount_pt, msg = audit_mount_permissions("/mnt/dec2/Emulation")

    assert mount_pt != "/mnt/dec"


def test_audit_mount_permissions_matches_exact_mount_point(monkeypatch):
    def fake_run(cmd, capture_output, text, check):
        return subprocess.CompletedProcess(cmd, 0, stdout=FAKE_MOUNT_OUTPUT, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    is_noexec, mount_pt, msg = audit_mount_permissions("/mnt/deck")

    assert mount_pt == "/mnt/deck"
    assert is_noexec is False


def test_audit_mount_permissions_skips_home_paths(monkeypatch):
    import os

    # Bypass OS-specific realpath resolution (e.g. macOS resolves /home via
    # an automount symlink to /System/Volumes/Data/home) so this test reflects
    # the target Linux behavior, where /home is a normal top-level directory.
    monkeypatch.setattr(os.path, "realpath", lambda p: p)

    is_noexec, mount_pt, msg = audit_mount_permissions("/home/deck/Emulation")

    assert mount_pt == "/home"
    assert is_noexec is False
