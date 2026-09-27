"""Tests for emu_stitch.ryujinx: save-index parsing, consistency checks and rebuilding."""

import os
import struct

import pytest

import emu_stitch.ryujinx as ryu_mod
from emu_stitch.ryujinx import (
    INDEX_SAVE,
    apply_reindex,
    auto_reindex,
    check_save_index,
    load_index,
    new_user_value,
    parse_index,
    plan_reindex,
    serialize_index,
    value_save_id,
)

GAME_A = 0x0100000000010000
GAME_B = 0x01008CF01BAAC000
GAME_C = 0x0100317013770000
DEFAULT_USER = b"\x01" + b"\x00" * 15


def key(title_id, save_type=1, user=DEFAULT_USER):
    """A SaveDataAttribute, laid out as Ryujinx stores it."""
    return (struct.pack("<Q", title_id) + user + struct.pack("<Q", 0) + bytes([save_type])).ljust(0x40, b"\x00")


SYSTEM_ENTRY = (
    (struct.pack("<QQQQ", 0, 0, 0, 0x8000000000000030)).ljust(0x40, b"\x00"),
    struct.pack("<Q", 0x8000000000000030).ljust(0x40, b"\x00"),  # space ID 0 = system
)


def write_index(ryujinx_dir, user_entries, last_published, slots=("0", "1")):
    """user_entries: [(key_bytes, save_id)]; the system entry is always included."""
    entries = [SYSTEM_ENTRY] + [(k, new_user_value(sid)) for k, sid in user_entries]
    for slot in slots:
        d = ryujinx_dir / INDEX_SAVE / slot
        d.mkdir(parents=True, exist_ok=True)
        (d / "imkvdb.arc").write_bytes(serialize_index(entries))
        (d / "lastPublishedId").write_bytes(struct.pack("<Q", last_published))


def write_save(profile_dir, save_id, attr_key, mtime=None):
    folder = profile_dir / "ryujinx" / "saves" / f"{save_id:016x}"
    (folder / "0").mkdir(parents=True)
    (folder / "ExtraData0").write_bytes(attr_key.ljust(512, b"\x00"))
    (folder / "0" / "data.bin").write_bytes(b"progress")
    if mtime is not None:
        for p in (folder / "ExtraData0", folder / "0" / "data.bin", folder / "0", folder):
            os.utime(p, (mtime, mtime))
    return folder


def user_map(ryujinx_dir):
    entries, _ = load_index(str(ryujinx_dir))
    return {k: value_save_id(v) for k, v in entries if v[0x18] == 1}


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

class TestEncoding:
    def test_round_trip_is_byte_identical_and_sorted(self):
        entries = [(key(GAME_B), new_user_value(2)), SYSTEM_ENTRY, (key(GAME_A), new_user_value(1))]
        data = serialize_index(entries)

        assert serialize_index(parse_index(data)) == data
        assert [k for k, _ in parse_index(data)] == sorted(k for k, _ in entries)

    @pytest.mark.parametrize("data", [
        b"NOPE" + b"\x00" * 8,
        b"IMKV\x00\x00\x00\x00\x05\x00\x00\x00IMEN",
        serialize_index([SYSTEM_ENTRY]) + b"junk",
    ])
    def test_rejects_malformed_archives(self, data):
        with pytest.raises(ValueError):
            parse_index(data)

    def test_new_user_value_layout(self):
        v = new_user_value(7)
        assert len(v) == 0x40
        assert value_save_id(v) == 7
        assert v[0x18] == 1
        assert v[0x19:] == bytes(0x40 - 0x19)


# ---------------------------------------------------------------------------
# check_save_index (audit)
# ---------------------------------------------------------------------------

class TestCheckSaveIndex:
    def test_consistent_profile_has_no_problems(self, tmp_path):
        ryu, profile = tmp_path / "Ryujinx", tmp_path / "alice"
        write_index(ryu, [(key(GAME_A), 1)], last_published=1)
        write_save(profile, 1, key(GAME_A))

        assert not any(check_save_index(str(profile), str(ryu)).values())

    def test_detects_mismatched_orphaned_and_reusable(self, tmp_path):
        ryu, profile = tmp_path / "Ryujinx", tmp_path / "alice"
        write_index(ryu, [(key(GAME_B), 2)], last_published=2)
        write_save(profile, 2, key(GAME_A))
        write_save(profile, 5, key(GAME_B))

        problems = check_save_index(str(profile), str(ryu))

        assert len(problems["mismatched"]) == 1 and "0000000000000002" in problems["mismatched"][0]
        assert problems["orphaned"] == ["0000000000000005 (01008cf01baac000)"]
        assert problems["reusable"] == ["0000000000000005"]

    def test_same_title_different_save_type_is_a_mismatch(self, tmp_path):
        """A game's BCAT/device save has the same title ID as its account
        save but a different key; comparing title IDs alone would miss it."""
        ryu, profile = tmp_path / "Ryujinx", tmp_path / "alice"
        write_index(ryu, [(key(GAME_A, save_type=1), 2)], last_published=2)
        write_save(profile, 2, key(GAME_A, save_type=2))

        assert len(check_save_index(str(profile), str(ryu))["mismatched"]) == 1

    def test_no_index_means_no_findings(self, tmp_path):
        profile = tmp_path / "alice"
        write_save(profile, 1, key(GAME_A))

        assert not any(check_save_index(str(profile), str(tmp_path / "none")).values())


# ---------------------------------------------------------------------------
# plan_reindex / apply_reindex
# ---------------------------------------------------------------------------

class TestReindex:
    def test_rebuilt_index_matches_profile_and_keeps_system_entry(self, tmp_path):
        ryu, profile = tmp_path / "Ryujinx", tmp_path / "alice"
        write_index(ryu, [(key(GAME_B), 2)], last_published=2)
        write_save(profile, 2, key(GAME_A))   # index had GAME_B here
        write_save(profile, 3, key(GAME_B))
        write_save(profile, 6, key(GAME_C))   # synced in, never indexed

        plan = plan_reindex(str(profile), str(ryu))
        apply_reindex(str(ryu), plan)

        assert user_map(ryu) == {key(GAME_A): 2, key(GAME_B): 3, key(GAME_C): 6}
        entries, last = load_index(str(ryu))
        assert SYSTEM_ENTRY in entries
        assert last == 6
        assert not any(check_save_index(str(profile), str(ryu)).values())

    def test_writes_both_slots_identically(self, tmp_path):
        ryu, profile = tmp_path / "Ryujinx", tmp_path / "alice"
        write_index(ryu, [], last_published=0)
        write_save(profile, 1, key(GAME_A))

        apply_reindex(str(ryu), plan_reindex(str(profile), str(ryu)))

        base = ryu / INDEX_SAVE
        assert (base / "0" / "imkvdb.arc").read_bytes() == (base / "1" / "imkvdb.arc").read_bytes()
        assert (base / "0" / "lastPublishedId").read_bytes() == (base / "1" / "lastPublishedId").read_bytes()

    def test_duplicates_pick_most_recently_modified_and_leave_others_untouched(self, tmp_path):
        ryu, profile = tmp_path / "Ryujinx", tmp_path / "alice"
        write_index(ryu, [(key(GAME_B), 2)], last_published=3)
        write_save(profile, 2, key(GAME_B), mtime=1_000_000)
        newer = write_save(profile, 3, key(GAME_B), mtime=2_000_000)

        plan = plan_reindex(str(profile), str(ryu))
        apply_reindex(str(ryu), plan)

        assert user_map(ryu)[key(GAME_B)] == 3
        assert any("most recently modified" in n and "0000000000000002" in n for n in plan.notes)
        assert (profile / "ryujinx" / "saves" / "0000000000000002" / "0" / "data.bin").exists()
        assert (newer / "0" / "data.bin").read_bytes() == b"progress"

    def test_keeps_other_profiles_entries_unless_their_number_is_taken(self, tmp_path):
        """Other profiles on this machine rely on the shared index; dropping
        their entries would make Ryujinx start fresh saves for them."""
        ryu, profile = tmp_path / "Ryujinx", tmp_path / "alice"
        bobs_game, clashing = key(GAME_C), key(0x0100AAAA00000000)
        write_index(ryu, [(bobs_game, 4), (clashing, 1)], last_published=4)
        write_save(profile, 1, key(GAME_A))   # alice's number 1 is a different save

        plan = plan_reindex(str(profile), str(ryu))
        apply_reindex(str(ryu), plan)

        mapping = user_map(ryu)
        assert mapping[bobs_game] == 4
        assert clashing not in mapping
        assert mapping[key(GAME_A)] == 1

    def test_last_published_never_decreases_and_covers_all_profiles(self, tmp_path):
        ryu, alice, bob = tmp_path / "Ryujinx", tmp_path / "alice", tmp_path / "bob"
        write_index(ryu, [], last_published=3)
        write_save(alice, 1, key(GAME_A))
        write_save(bob, 9, key(GAME_B))

        plan = plan_reindex(str(alice), str(ryu), [str(alice), str(bob)])

        assert plan.last_published == 9

    def test_consistent_index_has_no_changes(self, tmp_path):
        ryu, profile = tmp_path / "Ryujinx", tmp_path / "alice"
        write_index(ryu, [(key(GAME_A), 1)], last_published=1)
        write_save(profile, 1, key(GAME_A))

        assert plan_reindex(str(profile), str(ryu)).has_changes is False

    def test_unreadable_extradata_is_left_alone(self, tmp_path):
        ryu, profile = tmp_path / "Ryujinx", tmp_path / "alice"
        write_index(ryu, [], last_published=0)
        (profile / "ryujinx" / "saves" / "0000000000000001").mkdir(parents=True)

        plan = plan_reindex(str(profile), str(ryu))

        assert any("no readable ExtraData" in n for n in plan.notes)
        assert all(v[0x18] != 1 for _, v in plan.entries)

    def test_no_index_returns_none(self, tmp_path):
        assert plan_reindex(str(tmp_path / "alice"), str(tmp_path / "Ryujinx")) is None

    def test_backs_up_original_index_and_limits_backups(self, tmp_path):
        ryu, profile = tmp_path / "Ryujinx", tmp_path / "alice"
        write_index(ryu, [(key(GAME_B), 2)], last_published=2)
        original = (ryu / INDEX_SAVE / "0" / "imkvdb.arc").read_bytes()
        write_save(profile, 2, key(GAME_A))

        backup = apply_reindex(str(ryu), plan_reindex(str(profile), str(ryu)))
        assert open(os.path.join(backup, "0", "imkvdb.arc"), "rb").read() == original

        for _ in range(ryu_mod.INDEX_BACKUPS_KEPT + 3):
            apply_reindex(str(ryu), plan_reindex(str(profile), str(ryu)))
        backups = [e for e in os.listdir(ryu / INDEX_SAVE.rsplit(os.sep, 1)[0]) if ".bak-" in e]
        assert len(backups) == ryu_mod.INDEX_BACKUPS_KEPT


class TestAutoReindex:
    def test_skips_and_warns_while_ryujinx_is_running(self, tmp_path, monkeypatch):
        ryu, profile = tmp_path / "Ryujinx", tmp_path / "alice"
        write_index(ryu, [], last_published=0)
        write_save(profile, 1, key(GAME_A))
        before = (ryu / INDEX_SAVE / "0" / "imkvdb.arc").read_bytes()
        monkeypatch.setattr(ryu_mod, "ryujinx_running", lambda: True)

        messages = auto_reindex(str(profile), [str(ryu)], [str(profile)])

        assert "running" in messages[0]
        assert (ryu / INDEX_SAVE / "0" / "imkvdb.arc").read_bytes() == before

    def test_switching_profiles_repoints_shared_index(self, tmp_path, monkeypatch):
        """The two-profile case from a real Deck: the same game lives in a
        different folder number in each profile."""
        monkeypatch.setattr(ryu_mod, "ryujinx_running", lambda: False)
        ryu, alice, bob = tmp_path / "Ryujinx", tmp_path / "alice", tmp_path / "bob"
        write_index(ryu, [(key(GAME_B), 2)], last_published=2)
        write_save(alice, 2, key(GAME_B))
        write_save(bob, 2, key(GAME_A, save_type=2))
        write_save(bob, 3, key(GAME_B))
        both = [str(alice), str(bob)]

        auto_reindex(str(bob), [str(ryu)], both)
        assert user_map(ryu)[key(GAME_B)] == 3
        auto_reindex(str(alice), [str(ryu)], both)
        assert user_map(ryu)[key(GAME_B)] == 2
        assert check_save_index(str(alice), str(ryu))["mismatched"] == []


def _fake_proc(tmp_path, processes):
    for pid, (comm, argv0) in processes.items():
        d = tmp_path / "proc" / str(pid)
        d.mkdir(parents=True)
        (d / "comm").write_text(comm + "\n")
        (d / "cmdline").write_bytes(argv0.encode() + b"\x00--flag\x00")
    (tmp_path / "proc" / "self").mkdir()
    return str(tmp_path / "proc")


@pytest.mark.parametrize("comm,argv0,expected", [
    ("Ryujinx", "/opt/Ryujinx/Ryujinx", True),
    ("AppRun", "/tmp/.mount_x/Ryujinx.AppImage", True),
    ("bash", "/usr/bin/bash", False),
])
def test_ryujinx_running_detection(tmp_path, monkeypatch, comm, argv0, expected):
    monkeypatch.setattr(ryu_mod, "PROC_DIR", _fake_proc(tmp_path, {4242: (comm, argv0)}))
    assert ryu_mod.ryujinx_running() is expected


def test_unused_duplicate_is_reported_separately_not_as_orphan(tmp_path):
    ryu, profile = tmp_path / "Ryujinx", tmp_path / "alice"
    write_index(ryu, [(key(GAME_B), 2)], last_published=3)
    write_save(profile, 2, key(GAME_B))
    write_save(profile, 3, key(GAME_B))

    problems = check_save_index(str(profile), str(ryu))

    assert problems["orphaned"] == []
    assert problems["duplicates"] == ["0000000000000003 (01008cf01baac000) is unused; Ryujinx loads 0000000000000002"]


# A real imkvdb.arc from Ryujinx: one system save plus three games' account
# saves under Ryujinx's default user. Contains only public title IDs.
REAL_RYUJINX_INDEX = bytes.fromhex(
    "494d4b560000000004000000494d454e4000000040000000000000000000000000000000000000000000000000000000"
    "300000000000008000000000000000000000000000000000000000000000000000000000000000003000000000000080"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000494d454e400000004000000000000100000000010100000000000000000000000000000000000000"
    "000000000100000000000000000000000000000000000000000000000000000000000000010000000000000000000000"
    "000000000000000000000000010000000000000000000000000000000000000000000000000000000000000000000000"
    "00000000494d454e40000000400000000000771370310001010000000000000000000000000000000000000000000000"
    "010000000000000000000000000000000000000000000000000000000000000004000000000000000000000000000000"
    "000000000000000001000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "494d454e400000004000000000c0aa1bf08c000101000000000000000000000000000000000000000000000001000000"
    "000000000000000000000000000000000000000000000000000000000200000000000000000000000000000000000000"
    "0000000001000000000000000000000000000000000000000000000000000000000000000000000000000000"
)


def test_real_ryujinx_index_round_trips_byte_for_byte():
    entries = parse_index(REAL_RYUJINX_INDEX)

    assert len(entries) == 4
    assert serialize_index(entries) == REAL_RYUJINX_INDEX
    assert {value_save_id(v) for _, v in entries} == {0x8000000000000030, 1, 2, 4}
