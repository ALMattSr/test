#!/usr/bin/env python3
"""
tvrename_gui.py - a friendly browser window for tvrename.

Run it (or double-click "TV Renamer.command" on a Mac) and a page opens in your
web browser. Everything runs on your own computer; the only thing that goes out
to the internet is the lookups to TheTVDB.

Uses only the Python standard library.
"""
from __future__ import annotations

import json
import os
import platform
import secrets
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import movierename as mr
import tvrename as tr

HERE = Path(__file__).resolve().parent
PAGE = HERE / "tvrename_gui.html"
TOKEN = secrets.token_urlsafe(24)


class Session:
    """Everything the page is working on right now."""

    def __init__(self):
        self.lock = threading.Lock()
        self.tvdb: tr.TVDB | None = None
        self.root: Path | None = None
        self.parsed: list[tr.ParsedFile] = []
        self.groups: dict[str, list[tr.ParsedFile]] = {}
        self.plans: dict[str, dict] = {}
        self.last_log: Path | None = None
        # movies
        self.movies: dict[str, mr.Movie] = {}
        self.movie_state: dict[str, dict] = {}
        self.match_cache: dict = {}
        # clean-up
        self.extras: dict[str, Path] = {}

    # -- TheTVDB -------------------------------------------------------------- #
    def client(self) -> tr.TVDB:
        if self.tvdb is None:
            cfg = tr.load_config()
            key = os.environ.get("TVDB_API_KEY") or cfg.get("api_key")
            if not key:
                raise ApiError("Add your TheTVDB API key first.", code="need_key")
            self.tvdb = tr.TVDB(key, os.environ.get("TVDB_PIN") or cfg.get("pin"))
            try:
                self.tvdb.login()
            except tr.TVDBError as e:
                self.tvdb = None
                raise ApiError(str(e), code="need_key")
        return self.tvdb


class ApiError(Exception):
    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


SESSION = Session()


# --------------------------------------------------------------------------- #
# API actions
# --------------------------------------------------------------------------- #
def api_status(_):
    cfg = tr.load_config()
    return {
        "has_key": bool(os.environ.get("TVDB_API_KEY") or cfg.get("api_key")),
        "can_browse": platform.system() == "Darwin",
        "version": tr.__version__,
    }


def api_save_key(body):
    key = (body.get("api_key") or "").strip()
    pin = (body.get("pin") or "").strip() or None
    if not key:
        raise ApiError("Please paste your API key.")
    test = tr.TVDB(key, pin)
    try:
        test.login()
    except tr.TVDBError as e:
        raise ApiError(f"TheTVDB didn't accept that key: {e}")
    cfg = tr.load_config()
    cfg["api_key"] = key
    if pin:
        cfg["pin"] = pin
    else:
        cfg.pop("pin", None)
    tr.save_config(cfg)
    SESSION.tvdb = test
    return {"ok": True}


def api_browse(_):
    """Open the Mac's own 'choose folder' window and return the picked path."""
    if platform.system() != "Darwin":
        raise ApiError("Folder picker is only available on a Mac. Paste the path instead.")
    script = 'POSIX path of (choose folder with prompt "Choose a TV show folder")'
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True,
                             timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ApiError(f"Couldn't open the folder picker: {e}")
    path = out.stdout.strip()
    if out.returncode != 0 or not path:
        return {"path": None}  # cancelled
    return {"path": path.rstrip("/") or "/"}


def _resolve_root(body) -> Path:
    raw = (body.get("path") or "").strip().strip('"').strip("'")
    if not raw:
        raise ApiError("Choose a folder first.")
    root = Path(raw.replace("\\ ", " ")).expanduser()
    if not root.exists():
        raise ApiError(f"That folder can't be found: {root}. Is the network drive connected?")
    return root


def api_scan(body):
    root = _resolve_root(body)
    if body.get("mode") == "movies":
        return movie_scan(root, body)
    files = tr.find_files(root, recursive=True, include_subs=not body.get("no_subtitles"))
    parsed, unparsed = [], []
    for f in files:
        pf = tr.parse_file(f)
        (parsed if pf else unparsed).append(pf or f)
    groups: dict[str, list[tr.ParsedFile]] = {}
    for pf in parsed:
        groups.setdefault(pf.show_guess.lower(), []).append(pf)
    with SESSION.lock:
        SESSION.root = root
        SESSION.parsed = parsed
        SESSION.groups = groups
        SESSION.plans = {}
    return {
        "root": str(root),
        "total": len(files),
        "groups": [{
            "key": key,
            "guess": items[0].show_guess,
            "year": items[0].year_guess,
            "count": len(items),
            "seasons": sorted({pf.season for pf in items}),
            "samples": [pf.path.name for pf in items[:3]],
        } for key, items in groups.items()],
        "unparsed": [str(p.relative_to(root)) if root.is_dir() else p.name for p in unparsed],
    }


def api_search(body):
    query = (body.get("query") or "").strip()
    if not query:
        raise ApiError("Type a show name to search for.")
    tvdb = SESSION.client()
    m = tr.re.fullmatch(r"(?:id|tvdb)?[\s:#-]*(\d{3,})", query, tr.re.IGNORECASE)
    try:
        if m:
            return {"results": [tvdb.get_series(int(m.group(1)))]}
        year = body.get("year")
        results = tvdb.search_series(query, year) if year else []
        if not results:
            results = tvdb.search_series(query)
    except tr.TVDBError as e:
        raise ApiError(str(e))
    return {"results": results[:12]}


def api_plan(body):
    key = body.get("group")
    series = body.get("series") or {}
    opts = body.get("options") or {}
    items = SESSION.groups.get(key)
    if not items or not series.get("id"):
        raise ApiError("Pick a show first.")
    series = {"id": int(series["id"]), "name": series.get("name") or "?",
              "year": tr._to_int(series.get("year"))}
    tvdb = SESSION.client()
    order = opts.get("order") or "default"
    try:
        episodes = tvdb.get_episodes(series["id"], order)
    except tr.TVDBError as e:
        raise ApiError(f"Couldn't load episodes: {e}")

    include_year = not opts.get("no_year")
    rows = []
    for i, pf in enumerate(items):
        plan = tr.plan_file(series, pf, episodes, include_year=include_year,
                            include_titles=not opts.get("no_titles"))
        rows.append({
            "id": f"f{i}",
            "kind": "subtitle" if pf.is_subtitle else "file",
            "folder": _rel(pf.path.parent),
            "old": pf.path.name,
            "new": plan.target.name if plan.target else "",
            "status": plan.status,
            "message": plan.message,
            "season": pf.season,
            "episode": pf.episodes[0],
        })
    rows.sort(key=lambda r: (r["season"], r["episode"], r["kind"] != "file", r["old"].lower()))

    folder_rows, notes, plans = [], [], []
    if not opts.get("no_folders") and SESSION.root and SESSION.root.is_dir():
        label = tr.show_label(series, include_year, bool(opts.get("tvdb_id_folder")))
        plans, notes = tr.plan_folder_renames(items, SESSION.parsed, series, SESSION.root, label,
                                              pad_seasons=not opts.get("no_season_padding"))
        for j, fp in enumerate(plans):
            exists = fp.new.exists() and not tr.same_file(fp.new, fp.old)
            folder_rows.append({
                "id": f"d{j}",
                "kind": fp.kind,
                "folder": _rel(fp.old.parent),
                "old": fp.old.name,
                "new": fp.new.name,
                "status": "exists" if exists else "ok",
                "message": "A folder with that name already exists (not merging)" if exists else "",
            })

    plan_id = secrets.token_hex(6)
    with SESSION.lock:
        SESSION.plans[plan_id] = {
            "items": items,
            "files": {f"f{i}": pf for i, pf in enumerate(items)},
            "folders": {f"d{j}": fp for j, fp in enumerate(plans)},
            "group": key,
        }
    return {"plan_id": plan_id, "series": series, "episodes_loaded": len(episodes),
            "files": rows, "folders": folder_rows, "notes": notes}


def _rel(path: Path) -> str:
    root = SESSION.root
    try:
        rel = path.relative_to(root) if root and root.is_dir() else Path(path.name)
    except ValueError:
        return f"in {path.name}"  # outside the scanned folder: just show its name
    return str(rel) if str(rel) != "." else ""


def api_apply(body):
    plan = SESSION.plans.get(body.get("plan_id"))
    if not plan:
        raise ApiError("That preview has expired. Please scan the folder again.")
    choices = {c["id"]: (c.get("new") or "").strip() for c in body.get("rows") or []}
    root = SESSION.root
    log_path = (root if root.is_dir() else root.parent) / \
        f"tvrename-log-{tr._dt.datetime.now():%Y%m%d-%H%M%S}.jsonl"
    results = []

    # 1. files
    for rid, pf in plan["files"].items():
        if rid not in choices or not choices[rid]:
            continue
        target = pf.path.with_name(tr.safe_filename(choices[rid]))
        target = tr.keep_original_extension(target, pf)
        if target.suffix != pf.path.suffix:
            results.append(_result(rid, pf.path.name, target.name, False, "Extension would change"))
            continue
        if target == pf.path:
            continue
        if target.exists() and not tr.same_file(target, pf.path):
            results.append(_result(rid, pf.path.name, target.name, False, "Name already in use"))
            continue
        try:
            tr.move_path(pf.path, target)
        except OSError as e:
            results.append(_result(rid, pf.path.name, target.name, False, str(e)))
            continue
        tr.write_log(log_path, pf.path, target)
        results.append(_result(rid, pf.path.name, target.name, True))

    # 2. folders - season folders first, then the show folder (plan order)
    new_root = root
    for rid, fp in plan["folders"].items():
        if rid not in choices or not choices[rid]:
            continue
        new = fp.old.with_name(tr.safe_filename(choices[rid]))
        if new == fp.old:
            continue
        if new.exists() and not tr.same_file(new, fp.old):
            results.append(_result(rid, fp.old.name, new.name, False, "A folder with that name exists"))
            continue
        try:
            fp.old.rename(new)
        except OSError as e:
            results.append(_result(rid, fp.old.name, new.name, False,
                                   f"{e}. Is a file in it open (for example, in Plex)?"))
            continue
        if fp.old == log_path.parent or fp.old in log_path.parents:
            log_path = new / log_path.relative_to(fp.old)
        if fp.old == new_root or fp.old in new_root.parents:
            new_root = new / new_root.relative_to(fp.old)
        tr.write_log(log_path, fp.old, new, fp.kind)
        results.append(_result(rid, fp.old.name, new.name, True))

    done = sum(r["ok"] for r in results)
    with SESSION.lock:
        SESSION.plans.pop(body.get("plan_id"), None)
        SESSION.root = new_root
        if done:
            SESSION.last_log = log_path
    return {"results": results, "renamed": done,
            "failed": sum(not r["ok"] for r in results),
            "log": str(log_path) if done else None, "root": str(new_root)}


def _result(rid, old, new, ok, message=""):
    return {"id": rid, "old": old, "new": new, "ok": ok, "message": message}


def api_logs(body):
    folder = Path(body.get("path") or (SESSION.root or "")).expanduser()
    logs = []
    if folder.is_dir():
        logs = sorted(folder.glob("tvrename-log-*.jsonl"), reverse=True)
    if SESSION.last_log and SESSION.last_log.exists() and SESSION.last_log not in logs:
        logs.insert(0, SESSION.last_log)
    out = []
    for p in logs[:20]:
        try:
            n = sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            continue
        out.append({"path": str(p), "name": p.name, "entries": n})
    return {"logs": out}


def api_undo(body):
    log = Path(body.get("log") or "")
    if not log.is_file():
        raise ApiError("That undo log can't be found.")
    # Read the log first: undoing may rename the folder the log itself lives in.
    try:
        entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
    except (OSError, ValueError) as e:
        raise ApiError(f"Couldn't read that undo log: {e}")
    lines: list[str] = []
    restored = tr.undo_log(log, out=lines.append)
    # The folder we were looking at may have been renamed back.
    root = SESSION.root
    for e in reversed(entries):
        new, old = Path(e.get("new", "")), Path(e.get("old", ""))
        if root and (root == new or new in root.parents) and old.exists():
            root = old / root.relative_to(new)
    SESSION.root = root
    return {"restored": restored, "lines": lines, "root": str(root) if root else None}


# --------------------------------------------------------------------------- #
# Movies
# --------------------------------------------------------------------------- #
def movie_scan(root: Path, body) -> dict:
    if not root.is_dir():
        raise ApiError("Choose a folder (not a single file) for movies.")
    movies = mr.scan_movies(root, include_subs=not body.get("no_subtitles"))
    with SESSION.lock:
        SESSION.root = root
        SESSION.movies = {m.id: m for m in movies}
        SESSION.movie_state = {}
    return {"root": str(root), "mode": "movies",
            "movies": [{"id": m.id, "title": m.title, "year": m.year,
                        "folder": _movie_where(m),
                        "files": [p.name for p in m.files]} for m in movies]}


def _movie_row(mid: str) -> dict:
    movie = SESSION.movies[mid]
    st = SESSION.movie_state.get(mid, {})
    match = st.get("match")
    row = {"id": mid, "title": movie.title, "year": movie.year,
           "folder": _movie_where(movie),
           "own_folder": movie.own_folder, "candidates": st.get("candidates", []),
           "match": match, "confidence": st.get("confidence", "none")}
    if not match and not st.get("label"):
        row.update(status="nomatch", message="No match found on TheTVDB", label="", changes=[])
        return row
    label = st.get("label") or mr.movie_label(match["name"], match.get("year"))
    plan = mr.plan_movie(movie, label, own_folders=st.get("own_folders", True))
    st["plan"] = plan
    root = SESSION.root
    changes = []
    if plan.folder_action == "rename":
        changes.append({"kind": "folder", "old": movie.folder.name, "new": plan.dest_dir.name})
    elif plan.folder_action == "create":
        changes.append({"kind": "newfolder", "old": "", "new": plan.dest_dir.name})
    for src, dst in plan.moves:
        inside = src.relative_to(movie.folder) if movie.own_folder else Path(src.name)
        changes.append({"kind": "subtitle" if src.suffix.lower() in tr.SUBTITLE_EXTS else "file",
                        "old": str(inside), "new": dst.name})
    row.update(status=plan.status, message=plan.message, label=plan.label, changes=changes)
    return row


def _movie_where(movie: mr.Movie) -> str:
    """What to show as 'this is the movie': its own folder, or the file for loose movies."""
    if movie.own_folder:
        return _rel(movie.folder) or movie.folder.name
    return _rel_file(movie.parts[0][0])


def _rel_file(p: Path) -> str:
    root = SESSION.root
    try:
        return str(p.relative_to(root))
    except (ValueError, TypeError):
        return p.name


def api_movie_match(body):
    mid = body.get("id")
    movie = SESSION.movies.get(mid)
    if not movie:
        raise ApiError("That movie isn't in the current scan. Please scan again.")
    tvdb = SESSION.client()
    try:
        candidates = mr.find_matches(tvdb, movie, SESSION.match_cache)
    except tr.TVDBError as e:
        raise ApiError(str(e))
    best = candidates[0] if candidates else None
    SESSION.movie_state[mid] = {"candidates": candidates, "match": best,
                                "confidence": best["confidence"] if best else "none",
                                "own_folders": body.get("own_folders", True)}
    return _movie_row(mid)


def api_movie_search(body):
    query = (body.get("query") or "").strip()
    if not query:
        raise ApiError("Type a movie name to search for.")
    tvdb = SESSION.client()
    m = tr.re.fullmatch(r"(?:id|tvdb)?[\s:#-]*(\d{3,})", query, tr.re.IGNORECASE)
    try:
        if m:
            return {"results": [tvdb.get_movie(int(m.group(1)))]}
        title, year, _ = mr.parse_title(query)
        results = tvdb.search_movies(title, year) if year else []
        if not results:
            results = tvdb.search_movies(title or query)
    except tr.TVDBError as e:
        raise ApiError(str(e))
    return {"results": mr.rank_matches(title, year, results)[:8]}


def api_movie_set(body):
    """Pick a different match, or type the name by hand."""
    mid = body.get("id")
    if mid not in SESSION.movies:
        raise ApiError("That movie isn't in the current scan. Please scan again.")
    st = SESSION.movie_state.setdefault(mid, {"candidates": []})
    if body.get("match"):
        match = body["match"]
        st["match"] = {"id": match.get("id"), "name": match.get("name") or "?",
                       "year": tr._to_int(match.get("year")), "image": match.get("image") or "",
                       "network": match.get("network") or ""}
        st["confidence"] = "chosen"
        st.pop("label", None)
    if "label" in body:
        label = tr.safe_filename(body.get("label") or "")
        # A typed extension ("My Movie (2010).mkv") is dropped: each file keeps its own.
        while Path(label).suffix.lower() in tr.VIDEO_EXTS | tr.SUBTITLE_EXTS:
            label = label[: -len(Path(label).suffix)].rstrip(" .")
        if label:
            st["label"] = label
            st["confidence"] = "chosen"
    if "own_folders" in body:
        st["own_folders"] = bool(body["own_folders"])
    return _movie_row(mid)


def api_movie_apply(body):
    ids = [r for r in body.get("ids") or [] if r in SESSION.movies]
    if not ids:
        raise ApiError("Nothing selected.")
    root = SESSION.root
    log_path = root / f"tvrename-log-{tr._dt.datetime.now():%Y%m%d-%H%M%S}.jsonl"
    results, renamed, failed = [], 0, 0
    for mid in ids:
        st = SESSION.movie_state.get(mid, {})
        row = _movie_row(mid)  # re-plan against what's on disk right now
        plan = st.get("plan")
        if not plan or row["status"] != "ok":
            results.append({"id": mid, "ok": False, "title": row.get("label") or row["title"],
                            "message": row.get("message") or "Nothing to do"})
            failed += row["status"] not in ("same",)
            continue
        steps, log_path, root = mr.apply_movie(plan, log_path, root)
        ok = all(s["ok"] for s in steps)
        renamed += ok
        failed += not ok
        bad = [s for s in steps if not s["ok"]]
        results.append({"id": mid, "ok": ok, "title": plan.label,
                        "message": "; ".join(f'{b["old"]}: {b["message"]}' for b in bad)})
    with SESSION.lock:
        SESSION.root = root
        SESSION.movie_state = {}
        if log_path.exists():
            SESSION.last_log = log_path
    return {"results": results, "renamed": renamed, "failed": failed,
            "log": str(log_path) if log_path.exists() else None, "root": str(root)}


# --------------------------------------------------------------------------- #
# Clean-up of extra files
# --------------------------------------------------------------------------- #
def api_extras(body):
    root = SESSION.root
    if body.get("path"):
        root = _resolve_root(body)
    if not root or not root.is_dir():
        return {"items": [], "total_size": 0}
    found = tr.find_extras(root)
    with SESSION.lock:
        SESSION.extras = {f"x{i}": item["path"] for i, item in enumerate(found)}
    return {"root": str(root), "total_size": sum(i["size"] for i in found),
            "items": [{"id": f"x{i}", "rel": item["rel"], "size": item["size"],
                       "reason": item["reason"], "default": item["default"]}
                      for i, item in enumerate(found)]}


def api_clean(body):
    ids = body.get("ids") or []
    paths = [SESSION.extras[i] for i in ids if i in SESSION.extras]
    if not paths:
        raise ApiError("Nothing selected to delete.")
    root = SESSION.root
    log_path = root / f"tvrename-cleanup-{tr._dt.datetime.now():%Y%m%d-%H%M%S}.jsonl"
    results = tr.delete_extras(paths, root, log_path)
    files = [r for r in results if not r.get("folder")]
    with SESSION.lock:
        SESSION.extras = {}
    return {"deleted": sum(r["ok"] for r in files),
            "freed": sum(r.get("size", 0) for r in files if r["ok"]),
            "folders": sum(1 for r in results if r.get("folder")),
            "failed": [{"name": Path(r["path"]).name, "message": r["message"]}
                       for r in files if not r["ok"]]}


def api_quit(_):
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return {"ok": True}


ROUTES = {
    "/api/status": api_status,
    "/api/key": api_save_key,
    "/api/browse": api_browse,
    "/api/scan": api_scan,
    "/api/search": api_search,
    "/api/plan": api_plan,
    "/api/apply": api_apply,
    "/api/logs": api_logs,
    "/api/undo": api_undo,
    "/api/quit": api_quit,
    "/api/movie_match": api_movie_match,
    "/api/movie_search": api_movie_search,
    "/api/movie_set": api_movie_set,
    "/api/movie_apply": api_movie_apply,
    "/api/extras": api_extras,
    "/api/clean": api_clean,
}


# --------------------------------------------------------------------------- #
# Web server
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "tvrename"

    def log_message(self, *args):  # keep the Terminal window quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path in {"/", "/index.html"}:
            if f"t={TOKEN}" not in query:
                self._send(403, b"Open TV Renamer from the link shown in the Terminal window.",
                           "text/plain; charset=utf-8")
                return
            html = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(404, b"Not found", "text/plain")

    def do_POST(self):
        if self.headers.get("X-Token") != TOKEN:
            self._send(403, b'{"error":"forbidden"}', "application/json")
            return
        action = ROUTES.get(self.path)
        if not action:
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            result, code = action(body), 200
        except ApiError as e:
            result, code = {"error": str(e), "code": e.code}, 400
        except Exception as e:  # show unexpected problems in the page, not just the Terminal
            result, code = {"error": f"Something went wrong: {e}", "code": "crash"}, 500
        self._send(code, json.dumps(result).encode("utf-8"), "application/json")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    port = int(argv[0]) if argv and argv[0].isdigit() else 0
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/?t={TOKEN}"
    print("TV Renamer is running.")
    print(f"If your browser didn't open, paste this link into it:\n  {url}")
    print("To stop it, click Quit in the page or close this window.")
    if "--no-browser" not in argv:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
