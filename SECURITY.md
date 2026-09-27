# Security Policy

## Reporting a vulnerability

Please **don't** open a public issue for security problems. Report them privately through GitHub's [private vulnerability reporting](https://github.com/R-F-HansenSmith/emu-stitch/security/advisories/new) instead.

Include what you found, how to reproduce it, and what an attacker could do with it. You should get a reply within a week.

## Supported versions

Only the latest release gets security fixes.

## Scope and trust model

emu-stitch runs entirely as your user and never needs root. Things worth knowing:

- **Syncthing API key.** emu-stitch reads your Syncthing API key from `config.xml` and sends it only to the Syncthing GUI address in that file, or to `SYNCTHING_URL` if you set it.
- **Paired devices.** `emu-stitch pair` shares your `emustitch-*` save folders with the other device. Anyone who controls a paired device can change those saves. `--auto-accept` goes further and lets the device create new synced folders on your machine; it's off by default.
- **Synced files are treated as untrusted.** Profile names are sanitized before being used as paths, including names read from `user_map.json`.
