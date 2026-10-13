"""Drives and space: where Trillion saves her finished files, and freeing up the C: drive.

The owner can choose another drive for Trillion's finished files (videos, ebooks, pictures, websites, uploads), and can
move downloads and old files off C:. Every move is written down first, file by file, so it can be undone exactly.
Nothing is ever deleted here, and Windows, programs and app settings are never touched.
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import core

OUT_FOLDERS = ["Trillion Videos", "Trillion Ebooks", "Trillion Images", "Trillion Websites", "Trillion Uploads"]
MOVES = core.DATA / "storage" / "moves"
DOWNLOADS_GUID = "{374DE290-123F-4565-9164-39C4925E467B}"
OLD_PLACES = ["Desktop", "Documents", "Pictures", "Videos", "Music"]
SKIP_NAMES = {"desktop.ini", "thumbs.db", "ntuser.dat", ".ds_store"}
SKIP_EXT = {".lnk", ".url", ".ini", ".pst", ".ost", ".sys", ".dll", ".tmp", ".lock", ".crdownload", ".part", ".partial"}
SKIP_DIRS = {"my games", "outlook files", "windowspowershell", "powershell", "custom office templates", "my music", "my pictures",
             "my videos", "onenote notebooks", "zoom", "sound recordings", "camera roll", "saved pictures", "github",
             "visual studio code", "node_modules", ".git", "trillion", "trillion videos", "trillion ebooks", "trillion images",
             "trillion websites", "trillion uploads"}
CLOUD_ATTRS = 0x400000 | 0x1000 | 0x40000            # cloud-only (OneDrive "free up space"), offline, recall-on-open
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
JOB = {"running": False}
_LOCK = threading.Lock()


# ---------- where Trillion saves ----------
def owner_settings():
    o = core.q("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
    return core.settings(o) if o else {}


def root():
    """The folder chosen for Trillion's finished files, or None for the usual places."""
    r = owner_settings().get("storage_root") or ""
    return Path(r) if r and Path(r).exists() else None


def out_dir(user, name):
    r = root()
    if not r:
        return core.user_home(user) / name
    return (r / name) if user["is_admin"] else (r / user["name"] / name)


def user_folder(user):
    r = root()
    if not r:
        return None
    return r if user["is_admin"] else r / user["name"]


# ---------- drives ----------
_DRIVES = {"t": 0, "v": None}


def drives():
    """Each drive letter with its size, free space and, when Windows says, whether it is an SSD."""
    if time.time() - _DRIVES["t"] < 60 and _DRIVES["v"] is not None:
        return _DRIVES["v"]
    kinds = {}
    if os.name == "nt":
        try:
            ps = ("Get-Partition | Where-Object DriveLetter | ForEach-Object { $d = Get-PhysicalDisk | Where-Object DeviceId -eq $_.DiskNumber; "
                  "\"$($_.DriveLetter)|$($d.MediaType)\" }")
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=20,
                                 creationflags=NO_WINDOW).stdout
            for ln in out.splitlines():
                if "|" in ln:
                    l, k = ln.strip().split("|", 1)
                    kinds[l.upper()] = {"SSD": "SSD", "HDD": "hard drive"}.get(k.strip(), "")
        except Exception:
            pass
        letters = [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{c}:\\")]
    else:
        letters = [os.environ.get("TRILLION_TEST_DRIVES", "/")] if not os.environ.get("TRILLION_TEST_DRIVES") else \
            os.environ["TRILLION_TEST_DRIVES"].split(",")
    out = []
    for d in letters:
        try:
            u = shutil.disk_usage(d)
        except OSError:
            continue
        out.append({"drive": d, "letter": d[:1].upper() if os.name == "nt" else d, "total": u.total, "free": u.free,
                    "kind": kinds.get(d[:1].upper(), ""), "system": Path(d) == Path(Path.home().anchor)})
    _DRIVES.update(t=time.time(), v=out)
    return out


def free_on(path):
    try:
        return shutil.disk_usage(Path(path).anchor or "/").free
    except OSError:
        return None


# ---------- what could move off C: ----------
def downloads_dir():
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                v, _ = winreg.QueryValueEx(k, DOWNLOADS_GUID)
                return Path(os.path.expandvars(v))
        except OSError:
            pass
    return Path.home() / "Downloads"


def _skip(p, st):
    n = p.name.lower()
    if n in SKIP_NAMES or p.suffix.lower() in SKIP_EXT or n.startswith("~$"):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & (0x2 | 0x4) or attrs & CLOUD_ATTRS:      # hidden, system, or only in the cloud
        return True
    return False


def _walk(top, older_than=None):
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            p = Path(dirpath) / f
            try:
                st = p.stat()
            except OSError:
                continue
            if _skip(p, st):
                continue
            if older_than and st.st_mtime > older_than:          # changed recently: not an old file
                continue
            yield p, st.st_size, st.st_mtime


def _summary(items, home):
    items = sorted(items, key=lambda x: -x[1])
    return {"count": len(items), "bytes": sum(i[1] for i in items),
            "biggest": [{"path": str(Path(p).relative_to(home)) if str(p).startswith(str(home)) else str(p), "bytes": s,
                         "date": datetime.date.fromtimestamp(m).isoformat()} for p, s, m in items[:12]]}


def scan(months=12):
    """What could leave C:, in three groups. Nothing is moved here."""
    home = Path.home()
    cut = time.time() - months * 30.5 * 86400
    dl = downloads_dir()
    groups = {}
    groups["downloads"] = dict(_summary(list(_walk(dl)) if dl.exists() else [], home), label="Downloads folder",
                               what=f"Everything in {dl}. The Downloads folder itself moves too, so new downloads go to the new drive.",
                               where=str(dl))
    old = []
    for place in OLD_PLACES:
        f = home / place
        if f.exists() and f.resolve() != dl.resolve():
            old += list(_walk(f, older_than=cut))
    groups["old"] = dict(_summary(old, home), label=f"Files not changed in {months} months",
                         what="From Desktop, Documents, Pictures, Videos and Music. Shortcuts, settings files, Outlook mail, "
                              "game saves and OneDrive files that are only in the cloud stay where they are.")
    tri = []
    for u in core.q("SELECT * FROM users"):
        for name in OUT_FOLDERS:
            f = core.user_home(u) / name
            if f.exists() and not f.is_symlink() and not _is_junction(f):
                tri += list(_walk(f))
    groups["trillion"] = dict(_summary(tri, home), label="Trillion's finished files",
                              what="Videos, ebooks, pictures, websites and uploads Trillion made, for every login. From now on new ones are "
                                   "saved on the new drive too.")
    return {"months": months, "groups": groups, "drives": drives(), "target": str(root() or ""),
            "c_free": free_on(home)}


def _is_junction(p):
    try:
        return bool(os.path.isjunction(p)) if hasattr(os.path, "isjunction") else False
    except OSError:
        return False


# ---------- moving, with a record for undo ----------
def _unique(dst):
    if not dst.exists():
        return dst
    i = 2
    while True:
        c = dst.with_name(f"{dst.stem} ({i}){dst.suffix}")
        if not c.exists():
            return c
        i += 1


def _junction(link, target):
    """Leave a signpost at the old place, so anything that remembers the old address still finds the files."""
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True, creationflags=NO_WINDOW)
        return r.returncode == 0
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        return False


def _set_downloads(path):
    if os.name != "nt":
        return False
    import winreg
    for key in (r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, DOWNLOADS_GUID, 0, winreg.REG_EXPAND_SZ if "User Shell" in key else winreg.REG_SZ, str(path))
    return True


def start_move(groups, target, months=12):
    target = Path(target)
    groups = [g for g in groups if g in ("downloads", "old", "trillion")]
    if not groups:
        raise ValueError("Tick at least one group to move.")
    if Path(target.anchor) == Path(Path.home().anchor):
        raise ValueError("Choose a folder on another drive, for example D:\\Trillion.")
    if not Path(target.anchor).exists():
        raise ValueError(f"The drive {target.anchor} is not there.")
    with _LOCK:
        if JOB["running"]:
            raise ValueError("A move is already running.")
        JOB.update(running=True, done=0, total=0, bytes=0, detail="Getting ready", id=None, error="", skipped=0)
    t = threading.Thread(target=_move, args=(groups, target, months), daemon=True)
    t.start()
    return True


def _move(groups, target, months):
    mid = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    MOVES.mkdir(parents=True, exist_ok=True)
    rec = {"id": mid, "when": time.time(), "target": str(target), "groups": groups, "files": [], "junctions": [], "downloads": None,
           "storage_root_before": owner_settings().get("storage_root") or "", "undone": False}
    path = MOVES / f"{mid}.json"
    save = lambda: path.write_text(json.dumps(rec), encoding="utf-8")
    JOB["id"] = mid
    home = Path.home()
    try:
        target.mkdir(parents=True, exist_ok=True)
        plan = []                                                     # (source file, destination file)
        if "downloads" in groups:
            dl = downloads_dir()
            new_dl = target / "Downloads"
            for p, s, m in (_walk(dl) if dl.exists() else []):
                plan.append((p, new_dl / p.relative_to(dl), s))
            rec["downloads"] = {"old": str(dl), "new": str(new_dl)}
        if "old" in groups:
            cut = time.time() - months * 30.5 * 86400
            dl = downloads_dir()
            for place in OLD_PLACES:
                f = home / place
                if f.exists() and f.resolve() != dl.resolve():
                    for p, s, m in _walk(f, older_than=cut):
                        plan.append((p, target / "Old files from C" / place / p.relative_to(f), s))
        tri_dirs = []
        if "trillion" in groups:
            for u in core.q("SELECT * FROM users"):
                for name in OUT_FOLDERS:
                    f = core.user_home(u) / name
                    if f.exists() and not _is_junction(f) and not f.is_symlink():
                        dest = (target / name) if u["is_admin"] else (target / u["name"] / name)
                        tri_dirs.append((f, dest))
                        for p, s, m in _walk(f):
                            plan.append((p, dest / p.relative_to(f), s))
        need = sum(s for _, _, s in plan)
        free = free_on(target)
        if free is not None and need > free - 1024 ** 3:
            raise ValueError(f"Not enough space on {target.anchor}: {need / 1024 ** 3:.1f} GB to move, {free / 1024 ** 3:.1f} GB free.")
        JOB.update(total=len(plan), detail=f"Moving {len(plan)} files")
        save()
        for i, (src, dst, size) in enumerate(plan, 1):
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                d = _unique(dst)
                shutil.move(str(src), str(d))
                rec["files"].append([str(src), str(d)])
                JOB["bytes"] += size
            except (OSError, shutil.Error):
                JOB["skipped"] += 1                                    # in use or not allowed: it stays where it is
            JOB["done"] = i
            if i % 50 == 0:
                JOB["detail"] = f"Moved {i} of {len(plan)} files"
                save()
        save()
        # tidy up: empty folders left behind (never the main folders themselves)
        keep = {home / p for p in OLD_PLACES} | {downloads_dir()}
        for src, _ in rec["files"]:
            d = Path(src).parent
            while d not in keep and d != home and d.exists() and str(d).startswith(str(home)):
                try:
                    d.rmdir()
                except OSError:
                    break
                d = d.parent
        if "downloads" in groups and rec["downloads"]:
            Path(rec["downloads"]["new"]).mkdir(parents=True, exist_ok=True)
            rec["downloads"]["registry"] = _set_downloads(rec["downloads"]["new"])
        for old, new in tri_dirs:                                     # the old Trillion folders become signposts to the new place
            new.mkdir(parents=True, exist_ok=True)
            try:
                if old.exists() and not any(old.iterdir()):
                    old.rmdir()
                if not old.exists():
                    old.parent.mkdir(parents=True, exist_ok=True)
                    if _junction(old, new):
                        rec["junctions"].append([str(old), str(new)])
            except OSError:
                pass
        if "trillion" in groups:
            o = core.q("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
            st = core.settings(o)
            st["storage_root"] = str(target)
            core.save_settings(o["id"], st)
        save()
        JOB.update(detail=f"Moved {len(rec['files'])} files ({JOB['bytes'] / 1024 ** 3:.2f} GB)"
                          + (f"; {JOB['skipped']} were in use and stayed on C:" if JOB["skipped"] else ""))
    except ValueError as e:
        JOB.update(error=str(e), detail=str(e))
        if not rec["files"]:
            path.unlink(missing_ok=True)
    except Exception as e:
        JOB.update(error=f"{type(e).__name__}: {e}", detail="Stopped: " + str(e)[:200])
        save()
    finally:
        JOB["running"] = False
        _DRIVES["t"] = 0


def history():
    if not MOVES.exists():
        return []
    out = []
    for f in sorted(MOVES.glob("*.json"), reverse=True)[:20]:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({"id": r["id"], "when": r["when"], "target": r["target"], "groups": r["groups"], "files": len(r["files"]),
                    "undone": r.get("undone", False)})
    return out


def undo(mid):
    """Put every file back exactly where it was, and undo the folder changes."""
    if not re.fullmatch(r"[\d-]+", mid or ""):
        raise ValueError("Unknown move.")
    path = MOVES / f"{mid}.json"
    if not path.exists():
        raise ValueError("Unknown move.")
    r = json.loads(path.read_text(encoding="utf-8"))
    if r.get("undone"):
        raise ValueError("That move was already undone.")
    for old, new in r.get("junctions", []):                          # remove signposts first, so the real folder can return
        try:
            if os.name == "nt":
                os.rmdir(old)
            else:
                os.unlink(old)
        except OSError:
            pass
    back, missing = 0, 0
    for src, dst in reversed(r["files"]):
        if not Path(dst).exists():
            missing += 1
            continue
        Path(src).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(dst, str(_unique(Path(src))))
        back += 1
    if r.get("downloads") and r["downloads"].get("registry"):
        _set_downloads(r["downloads"]["old"])
    if "trillion" in r["groups"]:
        o = core.q("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
        st = core.settings(o)
        st["storage_root"] = r.get("storage_root_before") or ""
        core.save_settings(o["id"], st)
    r["undone"] = True
    path.write_text(json.dumps(r), encoding="utf-8")
    _DRIVES["t"] = 0
    return {"back": back, "missing": missing}


def set_root(target):
    """New finished files go here from now on (existing ones stay unless moved)."""
    o = core.q("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
    st = core.settings(o)
    if not target:
        st["storage_root"] = ""
    else:
        t = Path(target)
        if not Path(t.anchor).exists():
            raise ValueError(f"The drive {t.anchor} is not there.")
        t.mkdir(parents=True, exist_ok=True)
        st["storage_root"] = str(t)
    core.save_settings(o["id"], st)
    return st["storage_root"]
