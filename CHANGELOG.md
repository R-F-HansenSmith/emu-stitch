# Changelog

## 1.1.0

### Security
- `pair` no longer sets `autoAcceptFolders` on the remote device. Previously any paired device could create synced folders on this machine without asking. It's now opt-in via `pair --auto-accept`, with a warning.
- `setup` now asks before installing the systemd Steam-account watcher and lists it in the change summary. Previously it was always installed.
- Profile names loaded from `user_map.json` are re-sanitized, so a hand-edited or synced map can't point outside `saves_by_user/`.
- Device and profile names are escaped in `audit` output. A name containing `[/]` used to crash it.
- Syncthing folder IDs are URL-encoded in API requests.

### Data safety
- Backups are only pruned if every file in them is also in the profile, byte for byte. Previously a backup holding the only copy of a conflicting save could be pruned after a few migrations.
- An existing Syncthing folder pointing at a different path is no longer silently repointed. Previously profiles differing only by case, or a moved Emulation folder, could merge two users' saves.
- Steam accounts whose names sanitize to the same folder name (or differ only by case) get distinct profiles. Non-Latin names fall back to `User_<steamid>` instead of a shared `Default_User`.
- `switch` takes a lock, so the autostart entry and watcher can't run it concurrently.
- New: Ryujinx's save index is now part of each profile (`ryujinx/saveIndex`) and swapped in on every switch, so a profile's saves and index always match. Previously the index was shared by the whole machine, so switched or synced saves could be loaded for the wrong game or not at all.
- New: each machine numbers new Ryujinx saves from its own range (the counter is excluded from sync), so two machines never give the same folder number to different games.
- New: if both machines added Ryujinx saves while apart, the two copies of the index are merged automatically; a game started on both machines uses the most recently played save, and the other copy is kept.
- New: `audit` checks the active profile against its Ryujinx save index and lists unused duplicate saves. `emu-stitch ryujinx-reindex` repairs a profile's index from its own save folders (for saves from before this version).
- `switch` no longer touches Ryujinx's saves or index while Ryujinx is running.
- Profiles created after `setup` are now registered with Syncthing on their first `switch` and shared with the same devices as existing profiles (if sync was enabled in `setup`). Previously only the profile active during `setup` was ever synced.
- `audit` counts Ryujinx saves in every numbering range.
- The watcher also runs `switch` when Ryujinx saves arrive from another machine, and passes the Emulation directory explicitly. `setup` updates an older watcher.

### Fixes
- Flatpak Ryujinx and Cemu are routed inside their sandbox directories. Previously they were detected but the native paths were linked instead.
- Flatpak Steam and `~/.steam` installs are detected.
- The autostart entry and systemd unit use the actual installed `emu-stitch` path (quoted), not a hard-coded `~/.local/bin`.
- The Syncthing GUI address and TLS setting are read from `config.xml`.
- Syncthing is configured via scoped endpoints (`/rest/config/folders`, `/rest/config/devices`) instead of rewriting the whole config.
- Lowercase device IDs are accepted by `pair`/`unpair`.
- Cemu's EmuDeck `roms/wiiu/mlc01` path is only created if `roms/wiiu` exists.
- `uninstall.sh` always disables the watcher unit and removes `~/.config/emu-stitch`.
- `install.sh` only runs `uv tool update-shell` when `emu-stitch` isn't already on `PATH`.
- New `--debug` flag for verbose logging and full tracebacks.

### Other
- Requires Python 3.9+.
- CI runs the test suite on Python 3.9, 3.11 and 3.13.

## 1.0.0

- Initial release.
