"""
Emulators module for emu-stitch: Manages emulator-specific save symlinks,
anti-loop safeguards, automated save payload mirroring, and save game auditing.
"""

import os
import shutil

KNOWN_TITLE_MAP = {
    "00050000/101c9400": "The Legend of Zelda: Breath of the Wild (US)",
    "00050000/101c9500": "The Legend of Zelda: Breath of the Wild (EU)",
    "00050000/101c9300": "The Legend of Zelda: Breath of the Wild (JP)",
}

def configure_ryujinx_symlinks(active_link):
    """Ensure Ryujinx bis/user/save and saveMeta symlinks are correctly routed."""
    ryujinx_user = os.path.expanduser("~/.config/Ryujinx/bis/user")
    os.makedirs(ryujinx_user, exist_ok=True)
    
    for name, subtarget in [("save", "saves"), ("saveMeta", "saveMeta")]:
        link_path = os.path.join(ryujinx_user, name)
        expected_target = os.path.join(active_link, "ryujinx", subtarget)
        if os.path.islink(link_path):
            current = os.readlink(link_path)
            if current != expected_target:
                os.unlink(link_path)
                os.symlink(expected_target, link_path)
        else:
            if os.path.isdir(link_path) or os.path.isfile(link_path):
                shutil.rmtree(link_path, ignore_errors=True)
            os.symlink(expected_target, link_path)

def auto_mirror_ryujinx_payloads(active_link):
    """
    Auto-mirror Ryujinx save payloads (e.g. File1.bin, Common.bin) across
    all numerical Save ID folders (00000000...) and profile slots (0 and 1).
    """
    ryu_saves = os.path.join(active_link, "ryujinx", "saves")
    ryu_meta = os.path.join(active_link, "ryujinx", "saveMeta")
    if not os.path.exists(ryu_saves):
        return

    payload_files = ["Common.bin", "File1.bin"]
    save_src = None
    for root, _, files in os.walk(ryu_saves):
        if "File1.bin" in files:
            save_src = root
            break

    if save_src:
        for item in os.listdir(ryu_saves):
            item_path = os.path.join(ryu_saves, item)
            if os.path.isdir(item_path) and item.startswith("00000000"):
                for slot in ["0", "1"]:
                    slot_dir = os.path.join(item_path, slot)
                    os.makedirs(slot_dir, exist_ok=True)
                    for fname in payload_files:
                        src_f = os.path.join(save_src, fname)
                        dst_f = os.path.join(slot_dir, fname)
                        if os.path.exists(src_f) and os.path.abspath(src_f) != os.path.abspath(dst_f):
                            shutil.copy2(src_f, dst_f)
                
                meta_dir = os.path.join(ryu_meta, item)
                os.makedirs(meta_dir, exist_ok=True)
                meta_file = os.path.join(meta_dir, "00000001.meta")
                if not os.path.exists(meta_file):
                    existing_meta = None
                    for m_root, _, m_files in os.walk(ryu_meta):
                        if "00000001.meta" in m_files:
                            existing_meta = os.path.join(m_root, "00000001.meta")
                            break
                    if existing_meta:
                        shutil.copy2(existing_meta, meta_file)

def configure_cemu_symlinks(emu_dir, active_link):
    """Ensure Cemu mlc01/usr/save symlinks are correctly routed."""
    cemu_targets = [
        os.path.expanduser("~/.local/share/Cemu/mlc01/usr/save"),
        os.path.join(emu_dir, "roms/wiiu/mlc01/usr/save")
    ]
    expected_cemu_target = os.path.join(active_link, "Cemu/saves")
    for link_path in cemu_targets:
        os.makedirs(os.path.dirname(link_path), exist_ok=True)
        if os.path.islink(link_path):
            current = os.readlink(link_path)
            if current != expected_cemu_target:
                os.unlink(link_path)
                os.symlink(expected_cemu_target, link_path)
        else:
            if os.path.isdir(link_path) or os.path.isfile(link_path):
                shutil.rmtree(link_path, ignore_errors=True)
            os.symlink(expected_cemu_target, link_path)

def audit_emulator_saves(active_link):
    """
    Scans active save profile directory for detected game save data.
    Returns: list of dicts [{ 'emulator': ..., 'name': ..., 'details': ... }]
    """
    detected_saves = []
    if not os.path.exists(active_link):
        return detected_saves

    # 1. Ryujinx (Nintendo Switch)
    ryu_saves = os.path.join(active_link, "ryujinx", "saves")
    if os.path.exists(ryu_saves):
        save_ids = [d for d in os.listdir(ryu_saves) if os.path.isdir(os.path.join(ryu_saves, d)) and d.startswith("00000000")]
        if save_ids:
            has_odyssey = any(
                os.path.exists(os.path.join(ryu_saves, sid, slot, "File1.bin"))
                for sid in save_ids for slot in ["0", "1"]
            )
            title = "Super Mario Odyssey" if has_odyssey else "Switch Game Save Data"
            detected_saves.append({
                "emulator": "Ryujinx (Switch)",
                "name": title,
                "details": f"{len(save_ids)} save ID index(es) active"
            })

    # 2. Cemu (Wii U)
    cemu_saves = os.path.join(active_link, "Cemu", "saves")
    if os.path.exists(cemu_saves):
        for root, dirs, files in os.walk(cemu_saves):
            rel_path = os.path.relpath(root, cemu_saves)
            if rel_path in KNOWN_TITLE_MAP:
                detected_saves.append({
                    "emulator": "Cemu (Wii U)",
                    "name": KNOWN_TITLE_MAP[rel_path],
                    "details": f"Save folder: {rel_path}"
                })
            elif len(rel_path.split(os.sep)) == 2 and rel_path.startswith("00050000"):
                if not any(s["details"].endswith(rel_path) for s in detected_saves):
                    detected_saves.append({
                        "emulator": "Cemu (Wii U)",
                        "name": f"Wii U Title ({rel_path})",
                        "details": f"Save folder: {rel_path}"
                    })

    # 3. RetroArch / General Save Files (.srm, .sav, .state)
    for root, _, files in os.walk(active_link):
        rel = os.path.relpath(root, active_link)
        if rel.startswith("ryujinx") or rel.startswith("Cemu"):
            continue
        save_files = [f for f in files if f.endswith((".srm", ".sav", ".state", ".mcd"))]
        if save_files:
            system_name = os.path.basename(root).upper()
            detected_saves.append({
                "emulator": f"RetroArch / Standalone ({system_name})",
                "name": f"{len(save_files)} save file(s)",
                "details": f"Folder: {rel}"
            })

    return detected_saves

def configure_all_emulators(emu_dir, active_link, profile_name):
    """Run all emulator configuration routines."""
    configure_ryujinx_symlinks(active_link)
    auto_mirror_ryujinx_payloads(active_link)
    configure_cemu_symlinks(emu_dir, active_link)
