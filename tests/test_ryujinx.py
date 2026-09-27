"""Tests for emu_stitch.ryujinx: save-index parsing, consistency checks and rebuilding."""

import os
import struct

import pytest

import emu_stitch.ryujinx as ryu_mod
from emu_stitch.ryujinx import (
    INDEX_SAVE,
    PROFILE_INDEX,
    apply_reindex,
    check_save_index,
    ensure_counter,
    load_index,
    machine_range_base,
    maintain_profile_index,
    merge_index_conflicts,
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
        assert last == 2  # the counter is ensure_counter's job, not the rebuild's
        assert check_save_index(str(profile), str(ryu))["reusable"] == ["0000000000000003", "0000000000000006"]
        ensure_counter(str(ryu / INDEX_SAVE), str(profile), BASE)
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
        backups = [e for e in os.listdir(ryu / INDEX_SAVE.rsplit(os.sep, 1)[0]) if ".reindex-bak-" in e]
        assert len(backups) == ryu_mod.INDEX_BACKUPS_KEPT


BASE = 0x1234 << 32   # a stand-in machine range for tests


def profile_index(profile):
    return profile / PROFILE_INDEX


def write_profile_index(profile, user_entries, last_published):
    """An index living inside a profile, as emu-stitch routes it."""
    write_index(profile / "ryujinx", user_entries, last_published)
    src = profile / "ryujinx" / INDEX_SAVE
    dst = profile_index(profile)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return dst


def counters(index_dir):
    return [struct.unpack("<Q", (index_dir / s / "lastPublishedId").read_bytes())[0] for s in ("0", "1")]


class TestMachineRange:
    def test_range_is_stable_nonzero_and_below_system_ids(self, tmp_path, monkeypatch):
        mid = tmp_path / "machine-id"
        mid.write_text("0123456789abcdef0123456789abcdef\n")
        monkeypatch.setattr(ryu_mod, "MACHINE_ID_PATHS", (str(mid),))

        base = machine_range_base("/home/deck/.config/Ryujinx")

        assert base == machine_range_base("/home/deck/.config/Ryujinx")
        assert base >= 1 << 32
        assert base + (1 << 32) <= 0x8000000000000000

    def test_different_machines_and_installs_get_different_ranges(self, tmp_path, monkeypatch):
        mid = tmp_path / "machine-id"
        monkeypatch.setattr(ryu_mod, "MACHINE_ID_PATHS", (str(mid),))
        mid.write_text("aaaa")
        deck_native = machine_range_base("/home/deck/.config/Ryujinx")
        deck_flatpak = machine_range_base("/home/deck/.var/app/org.ryujinx.Ryujinx/config/Ryujinx")
        mid.write_text("bbbb")
        desktop = machine_range_base("/home/deck/.config/Ryujinx")

        assert len({deck_native, deck_flatpak, desktop}) == 3


class TestEnsureCounter:
    def test_moves_a_legacy_counter_onto_this_machines_range(self, tmp_path):
        profile = tmp_path / "alice"
        index_dir = write_profile_index(profile, [(key(GAME_A), 7)], last_published=7)
        write_save(profile, 7, key(GAME_A))

        msg = ensure_counter(str(index_dir), str(profile), BASE)

        assert counters(index_dir) == [BASE, BASE]
        assert f"{BASE + 1:016x}" in msg

    def test_restores_a_missing_counter(self, tmp_path):
        """lastPublishedId is never synced, so a profile arriving from another
        machine has none; Ryujinx must never start counting from 0."""
        profile = tmp_path / "alice"
        index_dir = write_profile_index(profile, [], last_published=0)
        for slot in ("0", "1"):
            (index_dir / slot / "lastPublishedId").unlink()

        ensure_counter(str(index_dir), str(profile), BASE)

        assert counters(index_dir) == [BASE, BASE]

    def test_counter_in_range_is_only_ever_raised(self, tmp_path):
        profile = tmp_path / "alice"
        index_dir = write_profile_index(profile, [], last_published=BASE + 5)
        write_save(profile, BASE + 9, key(GAME_A))   # ours, e.g. restored from backup

        assert ensure_counter(str(index_dir), str(profile), BASE) is None
        assert counters(index_dir) == [BASE + 9, BASE + 9]
        ensure_counter(str(index_dir), str(profile), BASE)
        assert counters(index_dir) == [BASE + 9, BASE + 9]

    def test_other_machines_folders_do_not_move_our_counter(self, tmp_path):
        profile = tmp_path / "alice"
        index_dir = write_profile_index(profile, [], last_published=BASE + 1)
        write_save(profile, (0x9999 << 32) + 50, key(GAME_A))   # another machine's range

        ensure_counter(str(index_dir), str(profile), BASE)

        assert counters(index_dir) == [BASE + 1, BASE + 1]


def conflict_copy(index_dir, slot, user_entries):
    entries = [SYSTEM_ENTRY] + [(k, new_user_value(sid)) for k, sid in user_entries]
    path = index_dir / slot / "imkvdb.sync-conflict-20260927-120000-ABCDEFG.arc"
    path.write_bytes(serialize_index(entries))
    return path


class TestMergeConflicts:
    DECK = 0x1111 << 32
    DESK = 0x2222 << 32

    def test_merges_new_saves_from_both_machines(self, tmp_path):
        profile = tmp_path / "alice"
        index_dir = write_profile_index(profile, [(key(GAME_A), 1), (key(GAME_B), self.DECK + 1)], last_published=self.DECK + 1)
        conflict = conflict_copy(index_dir, "0", [(key(GAME_A), 1), (key(GAME_C), self.DESK + 1)])
        write_save(profile, 1, key(GAME_A))
        write_save(profile, self.DECK + 1, key(GAME_B))
        write_save(profile, self.DESK + 1, key(GAME_C))

        messages = merge_index_conflicts(str(index_dir), str(profile), str(tmp_path / "backups"))

        mapping = {k: value_save_id(v) for k, v in parse_index((index_dir / "0" / "imkvdb.arc").read_bytes()) if v[0x18] == 1}
        assert mapping == {key(GAME_A): 1, key(GAME_B): self.DECK + 1, key(GAME_C): self.DESK + 1}
        assert (index_dir / "0" / "imkvdb.arc").read_bytes() == (index_dir / "1" / "imkvdb.arc").read_bytes()
        assert not conflict.exists()
        assert len(list((tmp_path / "backups").rglob("*sync-conflict*"))) == 1
        assert "merged 1" in messages[0]

    def test_same_game_started_on_both_machines_prefers_most_recently_played(self, tmp_path):
        profile = tmp_path / "alice"
        index_dir = write_profile_index(profile, [(key(GAME_A), self.DECK + 1)], last_published=self.DECK + 1)
        conflict_copy(index_dir, "1", [(key(GAME_A), self.DESK + 1)])
        deck_save = write_save(profile, self.DECK + 1, key(GAME_A), mtime=1_000_000)
        write_save(profile, self.DESK + 1, key(GAME_A), mtime=2_000_000)

        messages = merge_index_conflicts(str(index_dir), str(profile), str(tmp_path / "backups"))

        entries = parse_index((index_dir / "0" / "imkvdb.arc").read_bytes())
        assert dict(entries)[key(GAME_A)] == new_user_value(self.DESK + 1)
        assert any("most recently played" in m and f"{self.DECK + 1:016x}" in m for m in messages)
        assert (deck_save / "0" / "data.bin").exists()   # the other save is never deleted

    def test_merge_is_deterministic_regardless_of_which_copy_is_the_conflict(self, tmp_path):
        """Both machines may merge the same pair of copies; they must agree,
        or the merged indexes would conflict again."""
        results = []
        for main, other in ((self.DECK, self.DESK), (self.DESK, self.DECK)):
            profile = tmp_path / f"p{main}"
            index_dir = write_profile_index(profile, [(key(GAME_A), main + 1)], last_published=main + 1)
            conflict_copy(index_dir, "0", [(key(GAME_A), other + 1)])
            write_save(profile, self.DECK + 1, key(GAME_A), mtime=1_000_000)
            write_save(profile, self.DESK + 1, key(GAME_A), mtime=1_000_000)
            merge_index_conflicts(str(index_dir), str(profile), str(tmp_path / "b"))
            results.append((index_dir / "0" / "imkvdb.arc").read_bytes())

        assert results[0] == results[1]

    def test_no_conflicts_means_nothing_is_written(self, tmp_path):
        profile = tmp_path / "alice"
        index_dir = write_profile_index(profile, [(key(GAME_A), 1)], last_published=1)
        before = (index_dir / "0" / "imkvdb.arc").stat().st_mtime_ns

        assert merge_index_conflicts(str(index_dir), str(profile), str(tmp_path / "b")) == []
        assert (index_dir / "0" / "imkvdb.arc").stat().st_mtime_ns == before

    def test_unreadable_conflict_copy_is_left_for_review(self, tmp_path):
        profile = tmp_path / "alice"
        index_dir = write_profile_index(profile, [(key(GAME_A), 1)], last_published=1)
        bad = index_dir / "0" / "imkvdb.sync-conflict-20260927-120000-ABCDEFG.arc"
        bad.write_bytes(b"garbage")
        before = (index_dir / "0" / "imkvdb.arc").read_bytes()

        messages = merge_index_conflicts(str(index_dir), str(profile), str(tmp_path / "b"))

        assert "manual review" in messages[0]
        assert bad.exists()
        assert (index_dir / "0" / "imkvdb.arc").read_bytes() == before


def test_maintain_profile_index_merges_then_pins_counter(tmp_path, monkeypatch):
    monkeypatch.setattr(ryu_mod, "machine_range_base", lambda d: BASE)
    profile = tmp_path / "alice"
    index_dir = write_profile_index(profile, [(key(GAME_A), 1)], last_published=1)
    conflict_copy(index_dir, "0", [(key(GAME_B), (0x9999 << 32) + 1)])

    messages = maintain_profile_index("/any/Ryujinx", str(profile), str(tmp_path / "b"))

    assert len(messages) == 2
    assert counters(index_dir) == [BASE, BASE]
    assert key(GAME_B) in dict(parse_index((index_dir / "0" / "imkvdb.arc").read_bytes()))


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
