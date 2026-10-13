"""A full backup of Trillion that survives a clean Windows install, and putting it all back afterwards.

What a backup holds (in "Trillion Backup" on a drive that is not C:):
  - Trillion's own data: every login, password, setting, key, note, routine, chat, store link, Bible notes, website
    readings and clones, and the app settings Trillion made (read safely while Trillion runs);
  - Trillion's finished files that are still on C: (videos, ebooks, pictures, websites, uploads);
  - the phone pairing key, so the phones do not have to allow this PC again;
  - each business app's own data (its database and uploads), taken while that app is briefly stopped;
  - a list of everything that pointed somewhere: the PC and Windows user names, folders, the Downloads location,
    the Tailscale name and address, and folders moved to another drive.

Putting it back on a fresh install: every saved path is changed from the old Windows user folder to the new one, the
moved folders get their signposts again, the Downloads location is set again, and the apps are rebuilt with their
data. Files on other drives (D:) are never touched by a backup or a restore.
"""
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

KEEP = 3                                    # newest backups kept; older ones are removed
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SKIP_IN_DATA = {"__pycache__", "cache", "tmp", "restore-staging"}
JOB = {"running": False}
_LOCK = threading.Lock()


# ---------- applied at start-up, before anything opens the database (no other Trillion modules used) ----------
def apply_pending(data, base):
    """If a restore was asked for, put the backup's data in place now, while nothing is using it."""
    marker = data / "restore_pending.json"
    if not marker.exists():
        return None
    try:
        req = json.loads(marker.read_text(encoding="utf-8"))
        src = Path(req["src"])
        man = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        aside = data.parent / f"data-before-restore-{stamp}"
        shutil.copytree(data, aside, ignore=shutil.ignore_patterns("restore_pending.json"))
        for item in data.iterdir():                                   # empty the data folder, then fill it from the backup
            if item.name == "restore_pending.json":
                continue
            shutil.rmtree(item) if item.is_dir() and not item.is_symlink() else item.unlink()
        for item in (src / "data").iterdir():
            dst = data / item.name
            shutil.copytree(item, dst) if item.is_dir() else shutil.copy2(item, dst)
        old_home, new_home = man["home"], str(Path.home())
        old_data, new_data = man["data"], str(data)
        remap_db(data / "trillion.db", [(old_data, new_data), (old_home, new_home)])
        report = {"when": time.time(), "from": str(src), "aside": str(aside), "old_home": old_home, "new_home": new_home,
                  "pc_before": man.get("pc"), "tailscale_before": man.get("tailscale") or {}, "steps": []}
        # Trillion's finished files that were on C:
        hb = src / "home"
        if hb.exists():
            for item in hb.iterdir():
                dst = Path(new_home) / item.name
                if dst.is_dir() and any(dst.iterdir()):
                    report["steps"].append(f"{item.name}: already there, left as it is")
                    continue
                shutil.copytree(item, dst, dirs_exist_ok=True)
                report["steps"].append(f"{item.name}: put back")
        # the phone pairing key
        ab = src / "android"
        if ab.exists():
            ad = Path(new_home) / ".android"
            ad.mkdir(exist_ok=True)
            for f in ab.iterdir():
                shutil.copy2(f, ad / f.name)
            report["steps"].append("Phone pairing key put back: your phones will not ask to allow this PC again.")
        # the business apps' data is put back once Docker runs (see restore_apps)
        if man.get("apps"):
            (data / "restore_apps_pending.json").write_text(json.dumps({"src": str(src), "apps": man["apps"]}), encoding="utf-8")
            report["steps"].append("Business apps: their data is ready to go back as soon as Docker is installed and running.")
        report["links"] = relink(man, old_home, new_home, data)
        (data / "restore_report.json").write_text(json.dumps(report), encoding="utf-8")
    except Exception as e:
        (data / "restore_report.json").write_text(json.dumps({"when": time.time(), "error": f"{type(e).__name__}: {e}"}), encoding="utf-8")
    finally:
        marker.unlink(missing_ok=True)
    return True


def remap_db(db, pairs):
    """Change saved paths from the old PC's folders to the new ones, in every text column of every table."""
    if not db.exists():
        return 0
    con = sqlite3.connect(str(db))
    n = 0
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for t in tables:
            cols = [c[1] for c in con.execute(f'PRAGMA table_info("{t}")') if (c[2] or "").upper() in ("", "TEXT") or "CHAR" in (c[2] or "").upper()]
            for c in cols:
                for old, new in pairs:
                    if not old or old == new:
                        continue
                    for o, nw in {(old, new), (old.replace("\\", "\\\\"), new.replace("\\", "\\\\")), (old.replace("\\", "/"), new.replace("\\", "/"))}:
                        cur = con.execute(f'UPDATE "{t}" SET "{c}"=REPLACE("{c}",?,?) WHERE typeof("{c}")=\'text\' AND instr("{c}",?)>0',
                                          (o, nw, o))
                        n += cur.rowcount
        con.commit()
    finally:
        con.close()
    return n


def relink(man, old_home, new_home, data):
    """Folders moved to another drive get their signposts again, and Downloads points where it did."""
    out = []
    swap = lambda p: (new_home + p[len(old_home):]) if p.startswith(old_home) else p
    for old, new in man.get("junctions", []):
        o, nw = Path(swap(old)), Path(new)
        if not nw.exists():
            out.append(f"{nw} is not there; {o.name} could not be linked. Is the drive connected with the same letter?")
            continue
        if o.exists():
            continue
        o.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            ok = subprocess.run(["cmd", "/c", "mklink", "/J", str(o), str(nw)], capture_output=True, creationflags=NO_WINDOW).returncode == 0
        else:
            try:
                os.symlink(nw, o, target_is_directory=True)
                ok = True
            except OSError:
                ok = False
        out.append(f"{o} -> {nw}" + ("" if ok else " (could not be made)"))
    dl = man.get("downloads")
    if dl and dl != str(Path.home() / "Downloads") and Path(dl).exists():
        if os.name == "nt":
            try:
                import winreg
                for key in (r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"):
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                        winreg.SetValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}", 0,
                                          winreg.REG_EXPAND_SZ if "User Shell" in key else winreg.REG_SZ, dl)
                out.append(f"Downloads folder set to {dl} again (sign out and in once to see it everywhere)")
            except OSError:
                out.append(f"Could not set the Downloads folder to {dl}; set it in its Properties, Location tab")
        else:
            out.append(f"Downloads folder: {dl}")
    return out


# ---------- making a backup ----------
def default_target():
    import storage
    r = storage.root()
    if r:
        return str(Path(r.anchor) / "Trillion Backup")
    for d in storage.drives():
        if not d["system"]:
            return str(Path(d["drive"]) / "Trillion Backup")
    return ""


def find_backups():
    """Backups on any drive: the usual folder at the top of each drive, and the one in the settings."""
    import storage
    seen, out = set(), []
    places = [Path(d["drive"]) / "Trillion Backup" for d in storage.drives()]
    st = owner_settings()
    if st.get("backup_target"):
        places.insert(0, Path(st["backup_target"]))
    for p in places:
        if not p.exists():
            continue
        for b in sorted(p.iterdir(), reverse=True):
            m = b / "manifest.json"
            if m.exists() and str(b) not in seen:
                seen.add(str(b))
                try:
                    j = json.loads(m.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                out.append({"path": str(b), "when": j.get("when"), "pc": j.get("pc"), "version": j.get("version"),
                            "bytes": j.get("bytes"), "apps": [a["key"] for a in j.get("apps", [])], "complete": j.get("complete", False)})
    return out


def owner_settings():
    import core
    o = core.q("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
    return core.settings(o) if o else {}


def start(target, include_apps=True):
    target = Path(target or default_target())
    if not str(target):
        raise ValueError("Choose a folder on another drive for the backup, for example D:\\Trillion Backup.")
    if Path(target.anchor) == Path(Path.home().anchor) and os.name == "nt":
        raise ValueError("A backup on C: would be wiped by a clean Windows install. Choose another drive, for example D:.")
    if not Path(target.anchor).exists():
        raise ValueError(f"The drive {target.anchor} is not there.")
    with _LOCK:
        if JOB["running"]:
            raise ValueError("A backup is already running.")
        JOB.update(running=True, detail="Starting", error="", path="", bytes=0, when=time.time())
    threading.Thread(target=_make, args=(target, include_apps), daemon=True).start()
    return True


def _size(p):
    return sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file()) if Path(p).exists() else 0


def _make(target, include_apps):
    import core, storage, apps, phone  # noqa: F401
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H%M")
    dest = target / f"Trillion {stamp}"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        free = storage.free_on(target)
        need = _size(core.DATA) + sum(_size(core.user_home(u) / n) for u in core.q("SELECT * FROM users WHERE is_admin=1")
                                      for n in storage.OUT_FOLDERS if not storage._is_junction(core.user_home(u) / n))
        if free is not None and need > free - 1024 ** 3:
            raise ValueError(f"Not enough space on {target.anchor}: about {need / 1024 ** 3:.1f} GB is needed.")
        JOB["detail"] = "Copying Trillion's data"
        dd = dest / "data"
        shutil.copytree(core.DATA, dd, ignore=lambda d, names: [n for n in names if n in SKIP_IN_DATA or n.endswith(("-wal", "-shm", ".tmp"))
                                                               or n in ("trillion.db", "restore_report.json")])
        src = sqlite3.connect(str(core.DATA / "trillion.db"))     # a consistent copy, even while Trillion is busy
        dst = sqlite3.connect(str(dd / "trillion.db"))
        src.backup(dst)
        dst.close()
        src.close()
        home = Path.home()
        JOB["detail"] = "Copying finished files that are on C:"
        for n in storage.OUT_FOLDERS:
            f = home / n
            if f.exists() and not storage._is_junction(f) and not f.is_symlink():
                shutil.copytree(f, dest / "home" / n)
        ak = home / ".android"
        if (ak / "adbkey").exists():
            (dest / "android").mkdir(exist_ok=True)
            for name in ("adbkey", "adbkey.pub"):
                if (ak / name).exists():
                    shutil.copy2(ak / name, dest / "android" / name)
        app_list = []
        if include_apps:
            app_list = backup_apps(dest)
        ts = {}
        try:
            import app as _app
            v = _app.TAILSCALE.get("v") or {}
            ts = {"name": v.get("name"), "dns": v.get("dns"), "ip": v.get("ip")}
        except Exception:
            pass
        juncs = []
        for rec in sorted(storage.MOVES.glob("*.json")) if storage.MOVES.exists() else []:
            try:
                r = json.loads(rec.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if not r.get("undone"):
                juncs += r.get("junctions", [])
        man = {"version": (core.BASE / "VERSION").read_text().strip() if (core.BASE / "VERSION").exists() else "",
               "when": time.time(), "pc": os.environ.get("COMPUTERNAME") or (os.uname().nodename if hasattr(os, "uname") else ""),
               "user": os.environ.get("USERNAME") or os.environ.get("USER", ""), "home": str(home), "data": str(core.DATA),
               "storage_root": str(storage.root() or ""), "downloads": str(storage.downloads_dir()), "junctions": juncs,
               "tailscale": ts, "apps": app_list, "complete": True}
        man["bytes"] = _size(dest)
        (dest / "manifest.json").write_text(json.dumps(man, indent=1), encoding="utf-8")
        (dest / "READ ME.txt").write_text(readme(man), encoding="utf-8")
        old = sorted([p for p in target.iterdir() if (p / "manifest.json").exists()], reverse=True)[KEEP:]
        for p in old:
            shutil.rmtree(p, ignore_errors=True)
        o = core.q("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
        st = core.settings(o)
        st["backup_target"], st["last_backup"] = str(target), time.time()
        core.save_settings(o["id"], st)
        JOB.update(detail=f"Backed up ({man['bytes'] / 1024 ** 3:.2f} GB) to {dest}", path=str(dest), bytes=man["bytes"])
        core.log(o["id"], f"Backup made: {dest}", "done")
    except Exception as e:
        JOB.update(error=f"{e}", detail="Backup stopped: " + str(e)[:200])
        shutil.rmtree(dest, ignore_errors=True)
    finally:
        JOB["running"] = False


def backup_apps(dest):
    """Each installed app's data, taken while that app is stopped for a moment so its database is consistent."""
    import apps, core
    out = []
    if not apps.docker_state().get("running"):
        return out
    for r in core.q("SELECT * FROM apps WHERE status IN ('installed','running','stopped')"):
        key = r["key"]
        JOB["detail"] = f"Backing up {apps.APPS[key]['name']} (it pauses for a moment)"
        project = apps.PROJECT.format(key)
        code, names = apps.run(["volume", "ls", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.Name}}"], timeout=60)
        vols = [v.strip() for v in (names or "").splitlines() if v.strip()] if code == 0 else []
        live = "running" in apps.running_projects().get(project, "")
        if live:
            apps.compose(key, "stop", timeout=300)
        saved = []
        try:
            (dest / "apps").mkdir(exist_ok=True)
            for v in vols:
                c, o = apps.run(["run", "--rm", "-v", f"{v}:/data:ro", "-v", f"{(dest / 'apps').resolve()}:/backup", "alpine:3.20",
                                 "tar", "czf", f"/backup/{v}.tgz", "-C", "/data", "."], timeout=3600)
                if c == 0:
                    saved.append(v)
        finally:
            if live:
                apps.compose(key, "up", "-d", timeout=900)
        out.append({"key": key, "volumes": saved, "host": r["host"]})
    return out


def readme(man):
    when = datetime.datetime.fromtimestamp(man["when"]).strftime("%d %B %Y %H:%M")
    ts = man.get("tailscale") or {}
    return (f"Trillion backup made on {when} (version {man.get('version')}) on the PC {man.get('pc')}.\n\n"
            "AFTER A CLEAN WINDOWS INSTALL\n"
            "1. Install Trillion again the usual way (1 - Setup.bat), and sign in as the owner.\n"
            "2. Settings, Backup and restore: this backup is listed. Press RESTORE. Trillion restarts by herself.\n"
            "3. Install Tailscale and sign in. In the Tailscale admin page, remove the old PC, then give the new PC the old\n"
            f"   name ({ts.get('name') or 'the same name'}) and the old address ({ts.get('ip') or 'the same 100.x address'}), so every\n"
            "   link on the phones and laptop keeps working.\n"
            "4. Install Docker from the Apps page if you use the business apps; they come back with their data.\n\n"
            "Keep this folder private: it holds Trillion's saved keys and passwords.\n")


# ---------- restoring ----------
def request_restore(path):
    import core
    p = Path(path)
    if not (p / "manifest.json").exists() or not (p / "data" / "trillion.db").exists():
        raise ValueError("That folder is not a complete Trillion backup.")
    (core.DATA / "restore_pending.json").write_text(json.dumps({"src": str(p), "asked": time.time()}), encoding="utf-8")
    threading.Timer(3, lambda: os._exit(42)).start()          # the start script brings Trillion back; the restore runs first
    return True


def report():
    import core
    f = core.DATA / "restore_report.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return None


def pending_apps():
    import core
    f = core.DATA / "restore_apps_pending.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def restore_apps(host):
    """Once Docker runs again: put each app's data back into fresh volumes and start it with this PC's settings."""
    import apps, core
    pend = pending_apps()
    if not pend:
        return {"done": [], "failed": []}
    if not apps.docker_state().get("running"):
        raise ValueError("Docker is not running yet. Install or start it on the Apps page first.")
    done, failed = [], []
    src = Path(pend["src"]) / "apps"
    for a in pend["apps"]:
        key = a["key"]
        project = apps.PROJECT.format(key)
        try:
            for v in a["volumes"]:
                short = v[len(project) + 1:] if v.startswith(project + "_") else v
                apps.run(["volume", "create", "--label", f"com.docker.compose.project={project}", "--label",
                          f"com.docker.compose.volume={short}", "--label", "com.docker.compose.version=2", v], timeout=60)
                c, o = apps.run(["run", "--rm", "-v", f"{v}:/data", "-v", f"{src.resolve()}:/backup:ro", "alpine:3.20",
                                 "sh", "-c", f"tar xzf /backup/{v}.tgz -C /data"], timeout=3600)
                if c != 0:
                    raise apps.AppError(f"could not put back {short}")
            apps.configure(key, apps.folder(key), host)
            c, o = apps.compose(key, "up", "-d", timeout=3600)
            if c != 0:
                raise apps.AppError(apps._tail(o))
            if apps.prefs()["private"]:
                apps.share_on_tailscale(apps.APPS[key]["port"])
            apps._set(key, status="installed", host=host, detail="Put back from the backup")
            done.append(apps.APPS[key]["name"])
        except apps.AppError as e:
            failed.append(f"{apps.APPS[key]['name']}: {e}")
    if not failed:
        (core.DATA / "restore_apps_pending.json").unlink(missing_ok=True)
    apps.write_wslconfig(apps.prefs()["memory"])
    return {"done": done, "failed": failed}


def due(now=None):
    """The weekly backup: Sunday night, if the owner switched it on and a backup folder is known."""
    st = owner_settings()
    if not st.get("backup_weekly") or not st.get("backup_target"):
        return False
    now = now or datetime.datetime.now()
    last = st.get("last_backup") or 0
    return now.weekday() == 6 and now.hour >= 2 and time.time() - last > 5 * 86400
