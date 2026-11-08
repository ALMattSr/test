#!/usr/bin/env python3
"""
tvrename.py - Rename TV episode files into Plex-friendly names using TheTVDB.

For every video (and subtitle) file it finds, the tool:
  1. Works out the show name and season/episode number from the file name
     (or the folder it lives in).
  2. Looks the show up on TheTVDB (you confirm which show is the right one).
  3. Pulls the episode title from TheTVDB.
  4. Shows you the new name and asks before renaming, one file at a time.

Result looks like Plex's recommended convention:
    The Office (2005) - S02E05 - Halloween.mkv

Only uses the Python standard library - no extra installs needed.
Run `python3 tvrename.py --help` for all options.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

__version__ = "1.0.0"

API_BASE = "https://api4.thetvdb.com/v4"
CONFIG_PATH = Path.home() / ".tvrename.json"

VIDEO_EXTS = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".mpg", ".mpeg",
    ".ts", ".m2ts", ".webm", ".flv", ".divx", ".xvid", ".3gp",
}
SUBTITLE_EXTS = {".srt", ".sub", ".idx", ".ass", ".ssa", ".vtt", ".smi"}
# Tags kept on subtitle files so Plex knows the language, e.g. "Show - S01E01.en.forced.srt"
SUBTITLE_TAGS = {
    "en", "eng", "english", "es", "spa", "fr", "fre", "fra", "de", "ger", "deu", "it", "ita",
    "pt", "por", "nl", "dut", "nld", "sv", "swe", "no", "nor", "da", "dan", "fi", "fin",
    "pl", "pol", "ru", "rus", "ja", "jpn", "zh", "chi", "zho", "ko", "kor", "ar", "ara",
    "he", "heb", "hi", "hin", "tr", "tur", "el", "gre", "ell", "cs", "cze", "ces", "hu", "hun",
    "ro", "rum", "ron", "uk", "ukr", "vi", "vie", "th", "tha", "id", "ind",
    "forced", "sdh", "cc", "hi", "default",
}

# Folder names that are NOT the show name (e.g. ".../The Office/Season 2/").
SEASON_DIR_RE = re.compile(
    r"^(season|series|staffel|saison|s)[ ._-]*\d{1,3}$|^specials?$", re.IGNORECASE
)

# Season/episode patterns, tried in order. Each must expose groups:
#   1 = season, 2 = first episode, 3 = optional extra-episode tail
EPISODE_PATTERNS = [
    # S01E02, s01e02e03, S01E02-E03, S01 E02, S01.E02
    re.compile(
        r"(?<![a-z0-9])s(\d{1,3})[ ._-]*e(\d{1,4})((?:[ ._-]*-?[ ._-]*e\d{1,4})*)(?!\d)",
        re.IGNORECASE,
    ),
    # 1x02, 01x02-03, 1x02x03
    re.compile(r"(?<![a-z0-9])(\d{1,2})x(\d{2,3})((?:[-x]\d{2,3})*)(?!\d)", re.IGNORECASE),
    # "Season 1 Episode 2"
    re.compile(r"season[ ._-]*(\d{1,3})[ ._-]*episode[ ._-]*(\d{1,4})()", re.IGNORECASE),
]

YEAR_RE = re.compile(r"[\(\[]?((?:19|20)\d{2})[\)\]]?\s*$")
WINDOWS_BAD_CHARS = re.compile(r'[<>"/\\|?*\x00-\x1f]')


# --------------------------------------------------------------------------- #
# TheTVDB API client
# --------------------------------------------------------------------------- #
class TVDBError(Exception):
    pass


class TVDB:
    """Minimal TheTVDB v4 API client (https://thetvdb.github.io/v4-api/)."""

    def __init__(self, api_key: str, pin: str | None = None, language: str = "eng"):
        self.api_key = api_key
        self.pin = pin or None
        self.language = language
        self.token: str | None = None
        self._episode_cache: dict[tuple[int, str], dict] = {}

    # -- low level ---------------------------------------------------------- #
    def _request(self, method: str, path: str, params: dict | None = None,
                 body: dict | None = None, auth: bool = True, _retry: bool = True):
        url = path if path.startswith("http") else API_BASE + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", f"tvrename/{__version__}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if auth:
            if not self.token:
                self.login()
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401 and auth and _retry:
                self.token = None  # token expired - log in again once
                return self._request(method, path, params, body, auth, _retry=False)
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8")).get("message", "")
            except Exception:
                pass
            raise TVDBError(f"TheTVDB returned HTTP {e.code} for {path} {detail}".strip()) from e
        except urllib.error.URLError as e:
            raise TVDBError(f"Could not reach TheTVDB: {e.reason}") from e

    def login(self) -> None:
        body = {"apikey": self.api_key}
        if self.pin:
            body["pin"] = self.pin
        resp = self._request("POST", "/login", body=body, auth=False)
        token = (resp.get("data") or {}).get("token")
        if not token:
            raise TVDBError("Login to TheTVDB failed - check your API key (and PIN).")
        self.token = token

    # -- high level --------------------------------------------------------- #
    def search_series(self, query: str, year: int | None = None) -> list[dict]:
        params = {"query": query, "type": "series"}
        if year:
            params["year"] = str(year)
        resp = self._request("GET", "/search", params=params)
        results = []
        for item in resp.get("data") or []:
            translations = item.get("translations") or {}
            name = translations.get(self.language) or item.get("name") or "?"
            overviews = item.get("overviews") or {}
            raw_id = item.get("tvdb_id") or item.get("id") or ""
            digits = re.sub(r"\D", "", str(raw_id))
            if not digits:
                continue
            results.append({
                "id": int(digits),
                "name": name,
                "year": _to_int(item.get("year")),
                "network": item.get("network") or "",
                "overview": overviews.get(self.language) or item.get("overview") or "",
                "image": item.get("thumbnail") or item.get("image_url") or "",
            })
        return results

    def get_series(self, series_id: int) -> dict:
        data = self._request("GET", f"/series/{series_id}").get("data") or {}
        name = data.get("name") or "?"
        try:
            tr = self._request("GET", f"/series/{series_id}/translations/{self.language}")
            name = (tr.get("data") or {}).get("name") or name
        except TVDBError:
            pass
        return {
            "id": series_id,
            "name": name,
            "year": _to_int(data.get("year")) or _to_int(str(data.get("firstAired") or "")[:4]),
            "network": "",
            "overview": data.get("overview") or "",
            "image": data.get("image") or "",
        }

    def get_episodes(self, series_id: int, order: str = "default") -> dict[tuple[int, int], dict]:
        """Return {(season, episode): {"name": ..., "aired": ...}} for a series."""
        key = (series_id, order)
        if key in self._episode_cache:
            return self._episode_cache[key]

        def fetch(path_suffix: str) -> list[dict]:
            eps: list[dict] = []
            page = 0
            while page < 200:  # safety cap
                resp = self._request("GET", f"/series/{series_id}/episodes/{path_suffix}",
                                     params={"page": page})
                batch = (resp.get("data") or {}).get("episodes") or []
                eps.extend(batch)
                if not batch or not (resp.get("links") or {}).get("next"):
                    break
                page += 1
            return eps

        try:
            raw = fetch(f"{order}/{self.language}")  # translated titles
        except TVDBError:
            raw = []
        if not raw:
            raw = fetch(order)

        episodes: dict[tuple[int, int], dict] = {}
        for ep in raw:
            s, n = ep.get("seasonNumber"), ep.get("number")
            if s is None or n is None:
                continue
            episodes[(int(s), int(n))] = {"name": (ep.get("name") or "").strip(),
                                          "aired": ep.get("aired") or ""}
        self._episode_cache[key] = episodes
        return episodes


def _to_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# File name parsing
# --------------------------------------------------------------------------- #
@dataclass
class ParsedFile:
    path: Path
    show_guess: str
    year_guess: int | None
    season: int
    episodes: list[int]
    ext: str              # full suffix to keep, e.g. ".mkv" or ".en.forced.srt"
    is_subtitle: bool = False
    extra: dict = field(default_factory=dict)


def clean_name(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]", " ", text)           # [release group] tags
    text = re.sub(r"[._]+", " ", text)                 # dots/underscores -> spaces
    text = re.sub(r"\s*-\s*$", "", text.strip())       # trailing dash
    text = re.sub(r"^\s*-\s*", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" -")


def split_year(name: str) -> tuple[str, int | None]:
    m = YEAR_RE.search(name)
    if m and m.start() > 0:
        return name[: m.start()].strip(" -("), int(m.group(1))
    return name, None


def file_suffix(path: Path) -> tuple[str, bool]:
    """Return the suffix to keep. Subtitles keep language tags (.en.srt, .eng.forced.srt)."""
    ext = path.suffix.lower()
    if ext in SUBTITLE_EXTS:
        parts = path.name.split(".")
        tags = []
        # walk back over short tags like "en", "eng", "forced", "sdh", "cc"
        for part in reversed(parts[1:-1]):
            if part.lower() in SUBTITLE_TAGS or re.fullmatch(r"[a-z]{2}-[A-Z]{2}", part):
                tags.insert(0, part)
            else:
                break
        return ("." + ".".join(tags) if tags else "") + path.suffix, True
    return path.suffix, False


def show_from_folders(path: Path) -> str:
    for parent in path.parents:
        name = parent.name
        if not name:
            break
        if SEASON_DIR_RE.match(clean_name(name)):
            continue
        return clean_name(name)
    return ""


def parse_file(path: Path) -> ParsedFile | None:
    stem = path.name[: -len(path.suffix)] if path.suffix else path.name
    for pattern in EPISODE_PATTERNS:
        m = pattern.search(stem)
        if not m:
            continue
        season = int(m.group(1))
        episodes = [int(m.group(2))]
        for extra in re.findall(r"\d+", m.group(3) or ""):
            n = int(extra)
            if n not in episodes:
                episodes.append(n)
        # "S01E01-E03" means 1, 2, 3
        if len(episodes) == 2 and "-" in (m.group(3) or "") and episodes[1] > episodes[0] + 1:
            episodes = list(range(episodes[0], episodes[1] + 1))

        show_part = clean_name(stem[: m.start()])
        if len(show_part) < 2:
            show_part = show_from_folders(path)
        show, year = split_year(show_part)
        ext, is_sub = file_suffix(path)
        return ParsedFile(path, show, year, season, episodes, ext, is_sub)
    return None


# --------------------------------------------------------------------------- #
# Building the new name
# --------------------------------------------------------------------------- #
def safe_filename(text: str) -> str:
    """Make a name safe for Windows/SMB/NAS shares."""
    text = text.replace(": ", " - ").replace(":", "-").replace("/", "-").replace("\\", "-")
    text = WINDOWS_BAD_CHARS.sub("", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip().rstrip(".")


def show_label(series: dict, include_year: bool = True, include_tvdb_id: bool = False) -> str:
    name = series["name"]
    year = series.get("year")
    if include_year and year and not re.search(rf"\(\s*{year}\s*\)", name):
        name = f"{name} ({year})"
    if include_tvdb_id:
        name = f"{name} {{tvdb-{series['id']}}}"
    return safe_filename(name)


def episode_code(season: int, episodes: list[int]) -> str:
    code = f"S{season:02d}E{episodes[0]:02d}"
    if len(episodes) > 1:
        code += f"-E{episodes[-1]:02d}"
    return code


def build_new_name(series: dict, pf: ParsedFile, titles: list[str],
                   include_year: bool = True, include_titles: bool = True) -> str:
    parts = [show_label(series, include_year), episode_code(pf.season, pf.episodes)]
    if include_titles:
        unique = []
        for t in titles:
            if t and t not in unique:
                unique.append(t)
        title = " & ".join(unique)
        if len(title) > 120:
            title = title[:117].rstrip() + "..."
        if title:
            parts.append(title)
    return safe_filename(" - ".join(parts)) + pf.ext


def keep_original_extension(target: Path, pf: ParsedFile) -> Path:
    """Make sure the new name ends with the original file's exact extension.

    .mkv stays .mkv, .AVI stays .AVI, and subtitles keep their language tags
    (.en.srt). If a typed name ends in a different media extension (for example
    'Name.mp4' for an .mkv file), that wrong extension is replaced, not stacked.
    """
    original = pf.path.suffix
    name = target.name
    if name.endswith(pf.ext) or (not pf.is_subtitle and name.endswith(original)):
        return target
    lowered = name.lower()
    for ext in sorted(VIDEO_EXTS | SUBTITLE_EXTS, key=len, reverse=True):
        if lowered.endswith(ext):
            name = name[: -len(ext)]
            break
    if pf.is_subtitle and pf.ext != original and name.lower().endswith(pf.ext[: -len(original)].lower()):
        name = name[: -(len(pf.ext) - len(original))]  # drop language tags typed by hand
    return target.with_name(name.rstrip(". ") + pf.ext)


# --------------------------------------------------------------------------- #
# Config / API key
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def get_credentials(args) -> tuple[str, str | None]:
    cfg = load_config()
    key = args.api_key or os.environ.get("TVDB_API_KEY") or cfg.get("api_key")
    pin = args.pin or os.environ.get("TVDB_PIN") or cfg.get("pin")
    if not key:
        print("No TheTVDB API key found.")
        print("Get one (free account) at: https://thetvdb.com/api-information")
        key = input("Paste your TheTVDB API key: ").strip()
        if not key:
            sys.exit("An API key is required.")
        pin = input("Subscriber PIN (press Enter if your key doesn't need one): ").strip() or None
        if ask(f"Save these to {CONFIG_PATH} so you don't have to enter them again?", "y") == "y":
            save_config({**cfg, "api_key": key, **({"pin": pin} if pin else {})})
    return key, pin


# --------------------------------------------------------------------------- #
# Interaction helpers
# --------------------------------------------------------------------------- #
def ask(prompt: str, default: str = "") -> str:
    try:
        answer = input(f"{prompt} ").strip().lower()
    except EOFError:
        answer = ""
    return answer or default


def choose_series(tvdb: TVDB, guess: str, year: int | None, sample_files: list[Path],
                  auto_first: bool = False) -> dict | None:
    query = guess
    while True:
        print()
        print("=" * 70)
        print(f"Show detected: \"{guess}\"" + (f" ({year})" if year else ""))
        for p in sample_files[:3]:
            print(f"   e.g. {p.name}")
        if not query:
            query = input("Type the show name to search for (or Enter to skip): ").strip()
            if not query:
                return None
        print(f"Searching TheTVDB for \"{query}\"...")
        try:
            results = tvdb.search_series(query, year) or (tvdb.search_series(query) if year else [])
        except TVDBError as e:
            print(f"  ! {e}")
            results = []
        year = None  # only use the year hint on the first search

        if results and auto_first:
            r = results[0]
            print(f"  Using: {r['name']} ({r['year'] or '?'}) [tvdb {r['id']}]")
            return r

        if not results:
            print("  No matches.")
        for i, r in enumerate(results[:10], 1):
            ov = (r["overview"] or "").replace("\n", " ")
            ov = (ov[:70] + "...") if len(ov) > 70 else ov
            net = f" - {r['network']}" if r["network"] else ""
            print(f"  {i:>2}) {r['name']} ({r['year'] or '?'}){net}  [tvdb {r['id']}]")
            if ov:
                print(f"      {ov}")
        print("  Enter a number to pick, type a different name to search again,")
        print("  'id 12345' to use a TheTVDB id directly, or 's' to skip this show.")
        answer = input(f"  Choice [{'1' if results else 's'}]: ").strip()
        if not answer:
            answer = "1" if results else "s"
        if answer.lower() in {"s", "skip"}:
            return None
        if answer.isdigit() and 1 <= int(answer) <= min(len(results), 10):
            return results[int(answer) - 1]
        m = re.fullmatch(r"(?:id|tvdb)[\s:#-]*(\d+)", answer, re.IGNORECASE)
        if m:
            try:
                return tvdb.get_series(int(m.group(1)))
            except TVDBError as e:
                print(f"  ! {e}")
                continue
        query = answer


# --------------------------------------------------------------------------- #
# Main workflow
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Folder renaming (show folder + season folders)
# --------------------------------------------------------------------------- #
SUBTITLE_DIR_NAMES = {"subs", "subtitles", "subtitle", "sub"}
SEASON_TOKEN_RE = re.compile(r"(?<![a-z0-9])(?:season|series|s)[ ._-]*(\d{1,3})(?!\d)", re.IGNORECASE)


def season_folder_name(season: int, pad: bool = True) -> str:
    if season == 0:
        return "Specials"
    return f"Season {season:02d}" if pad else f"Season {season}"


def _norm(text: str) -> str:
    text = split_year(clean_name(text))[0]
    text = re.sub(r"\([^)]*\)", "", text)
    return re.sub(r"[^a-z0-9]", "", text.lower())


def has_episode_number(name: str) -> bool:
    return any(p.search(name) for p in EPISODE_PATTERNS)


def folder_matches_show(folder_name: str, show_names: list[str]) -> bool:
    """Does this folder name look like it belongs to the show? (Guards against renaming 'TV'.)"""
    folder = _norm(folder_name)
    if len(folder) < 3:
        return False
    for name in show_names:
        name = _norm(name)
        if len(name) < 3:
            continue
        if name in folder or (folder in name and len(folder) >= 4):
            return True
        if difflib.SequenceMatcher(None, folder, name).ratio() >= 0.8:
            return True
    return False


def looks_like_season_folder(name: str, season: int) -> bool:
    if has_episode_number(name):
        return False  # e.g. a per-episode folder "Show.S01E01.1080p"
    cleaned = clean_name(name)
    if season == 0 and re.fullmatch(r"specials?|season[ ._-]*0+", cleaned, re.IGNORECASE):
        return True
    m = SEASON_TOKEN_RE.search(cleaned)
    return bool(m and int(m.group(1)) == season)


@dataclass
class FolderRename:
    old: Path
    new: Path
    kind: str  # "season" or "show"


def plan_folder_renames(items: list[ParsedFile], all_parsed: list[ParsedFile], series: dict,
                        root: Path, show_label_text: str, pad_seasons: bool = True,
                        ) -> tuple[list[FolderRename], list[str]]:
    """Work out which season folders and show folder to rename for one show.

    Returns (renames, notes). Season folders come first so their parent path
    stays valid until the show folder itself is renamed.
    """
    mine = {id(pf) for pf in items}
    show_names = [series["name"], items[0].show_guess]
    notes: list[str] = []

    def files_under(d: Path) -> list[ParsedFile]:
        return [pf for pf in all_parsed if d in pf.path.parents]

    def only_this_show(d: Path) -> bool:
        return all(id(pf) in mine for pf in files_under(d))

    dirs = set()
    for pf in items:
        d = pf.path.parent
        if d.name.lower() in SUBTITLE_DIR_NAMES:
            d = d.parent
        dirs.add(d)

    season_dirs: dict[Path, int] = {}
    show_candidates: set[Path] = set()
    for d in dirs:
        seasons = {pf.season for pf in files_under(d)}
        if len(seasons) == 1 and only_this_show(d) and looks_like_season_folder(d.name, next(iter(seasons))):
            season_dirs[d] = next(iter(seasons))
            show_candidates.add(d.parent)
        else:
            show_candidates.add(d)

    def eligible_show_dir(d: Path) -> bool:
        return (
            (d == root or root in d.parents)
            and not os.path.ismount(d)
            and not has_episode_number(d.name)
            and folder_matches_show(d.name, show_names)
            and only_this_show(d)
        )

    renames: list[FolderRename] = []
    for d, season in sorted(season_dirs.items(), key=lambda kv: str(kv[0])):
        if not folder_matches_show(d.parent.name, show_names):
            notes.append(f"Left \"{d.name}\" alone: its parent folder \"{d.parent.name}\" "
                         f"isn't the show's folder (use --organize to build one).")
            continue
        new = d.with_name(season_folder_name(season, pad_seasons))
        if new.name != d.name:
            renames.append(FolderRename(d, new, "season"))

    for d in sorted(show_candidates, key=str):
        if d in season_dirs:
            continue
        if not eligible_show_dir(d):
            if d == root or root in d.parents:
                notes.append(f"Left folder \"{d.name}\" alone: it doesn't look like it holds only "
                             f"this show.")
            continue
        new = d.with_name(show_label_text)
        if new.name != d.name:
            renames.append(FolderRename(d, new, "show"))
    return renames, notes


def find_files(root: Path, recursive: bool, include_subs: bool) -> list[Path]:
    exts = VIDEO_EXTS | (SUBTITLE_EXTS if include_subs else set())
    if root.is_file():
        return [root]
    it = root.rglob("*") if recursive else root.glob("*")
    files = []
    for p in it:
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        if p.is_file() and p.suffix.lower() in exts and "sample" not in p.name.lower():
            files.append(p)
    return sorted(files, key=lambda p: str(p).lower())


def same_file(a: Path, b: Path) -> bool:
    """True if a and b are the same file (e.g. a case-only rename on a Mac or NAS)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def write_log(log_path: Path, old: Path, new: Path, kind: str = "file") -> None:
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"old": str(old), "new": str(new), "kind": kind,
                            "time": _dt.datetime.now().isoformat(timespec="seconds")}) + "\n")


def undo_log(log_path: Path, dry_run: bool = False, out=print) -> int:
    """Reverse the renames recorded in log_path. Returns how many were restored."""
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    restored = 0
    for e in reversed(entries):
        old, new = Path(e["old"]), Path(e["new"])
        if not new.exists():
            out(f"missing, skipped: {new}")
            continue
        if old.exists() and not same_file(old, new):
            out(f"original name already in use, skipped: {old}")
            continue
        label = "folder " if e.get("kind") in {"season", "show"} else ""
        out(f"{label}{new.name}  ->  {old.name}")
        if not dry_run:
            old.parent.mkdir(parents=True, exist_ok=True)
            if old.parent == new.parent:
                new.rename(old)
            else:
                shutil.move(str(new), str(old))
            restored += 1
    out(f"Restored {restored} item(s).")
    return restored


def undo(log_path: Path, dry_run: bool) -> int:
    undo_log(log_path, dry_run, out=lambda msg: print(f"  {msg}"))
    return 0


@dataclass
class FilePlan:
    pf: ParsedFile
    target: Path | None
    status: str          # "ok", "same", "missing", "exists"
    message: str = ""


def plan_file(series: dict, pf: ParsedFile, episodes: dict, include_year: bool = True,
              include_titles: bool = True, dest_root: Path | None = None,
              tvdb_id_folder: bool = False) -> FilePlan:
    """Work out the new name for one file, without touching anything."""
    titles, missing = [], []
    for ep in pf.episodes:
        info = episodes.get((pf.season, ep))
        if info is None:
            missing.append(ep)
        else:
            titles.append(info["name"])
    if missing:
        return FilePlan(pf, None, "missing",
                        f"TheTVDB has no S{pf.season:02d}E{missing[0]:02d} for this show "
                        f"(try a different episode order or check the number)")
    new_name = build_new_name(series, pf, titles, include_year=include_year,
                              include_titles=include_titles)
    if dest_root:
        folder = dest_root / show_label(series, include_year, tvdb_id_folder)
        folder = folder / season_folder_name(pf.season)
        target = folder / new_name
    else:
        target = pf.path.with_name(new_name)
    target = keep_original_extension(target, pf)
    if target == pf.path:
        return FilePlan(pf, target, "same", "Already named correctly")
    if target.exists() and not same_file(target, pf.path):
        return FilePlan(pf, target, "exists", "A file with that name already exists")
    return FilePlan(pf, target, "ok")


def move_path(old: Path, new: Path) -> None:
    """Rename a file or folder (moving it if it goes to a different folder)."""
    new.parent.mkdir(parents=True, exist_ok=True)
    if new.parent == old.parent:
        old.rename(new)
    else:
        shutil.move(str(old), str(new))


def run(args) -> int:
    if args.undo:
        return undo(Path(args.undo), args.dry_run)

    root = Path(args.path).expanduser()
    if not root.exists():
        sys.exit(f"Path not found: {root}")

    files = find_files(root, recursive=not args.no_recursive, include_subs=not args.no_subtitles)
    if not files:
        print("No video files found.")
        return 0

    parsed: list[ParsedFile] = []
    unparsed: list[Path] = []
    for f in files:
        pf = parse_file(f)
        if pf:
            if args.show:
                pf.show_guess, pf.year_guess = split_year(args.show)
            parsed.append(pf)
        else:
            unparsed.append(f)

    print(f"Found {len(files)} file(s); {len(parsed)} have a season/episode number.")
    if unparsed:
        print(f"{len(unparsed)} file(s) without a recognizable SxxEyy number will be skipped:")
        for p in unparsed[:10]:
            print(f"   - {p.name}")
        if len(unparsed) > 10:
            print(f"   ... and {len(unparsed) - 10} more")
    if not parsed:
        return 0

    key, pin = get_credentials(args)
    tvdb = TVDB(key, pin, args.language)
    try:
        tvdb.login()
    except TVDBError as e:
        sys.exit(str(e))

    # Group by guessed show so we only ask once per show
    groups: dict[str, list[ParsedFile]] = {}
    for pf in parsed:
        groups.setdefault(pf.show_guess.lower(), []).append(pf)

    log_path = Path(args.log) if args.log else (root if root.is_dir() else root.parent) / \
        f"tvrename-log-{_dt.datetime.now():%Y%m%d-%H%M%S}.jsonl"
    dest_root = Path(args.organize).expanduser() if args.organize else None

    renamed = skipped = folders_renamed = 0
    rename_all = args.yes
    for _, items in groups.items():
        first = items[0]
        series = choose_series(tvdb, first.show_guess, first.year_guess,
                               [p.path for p in items], auto_first=args.auto)
        if not series:
            print("  Skipping this show.")
            skipped += len(items)
            continue
        try:
            episodes = tvdb.get_episodes(series["id"], args.order)
        except TVDBError as e:
            print(f"  ! Could not get episodes: {e}")
            skipped += len(items)
            continue
        print(f"  Loaded {len(episodes)} episodes for {series['name']}.")

        for idx, pf in enumerate(items, 1):
            print()
            print(f"[{idx}/{len(items)}] {pf.path.name}")
            plan = plan_file(series, pf, episodes, include_year=not args.no_year,
                             include_titles=not args.no_titles, dest_root=dest_root,
                             tvdb_id_folder=args.tvdb_id_folder)
            if plan.status == "same":
                print("   Already named correctly.")
                continue
            if plan.status == "missing":
                print(f"   ! {plan.message}. Skipped.")
                skipped += 1
                continue
            target = plan.target
            print(f"   -> {target if dest_root else target.name}")
            if plan.status == "exists":
                print(f"   ! {plan.message}. Skipped.")
                skipped += 1
                continue

            if args.dry_run:
                print("   (dry run - not renamed)")
                continue

            if not rename_all:
                while True:
                    answer = ask("   Rename? [Y]es / [n]o / [a]ll remaining / [e]dit / [q]uit:", "y")
                    if answer in {"y", "n", "a", "e", "q"}:
                        break
                if answer == "q":
                    print("\nStopped early.")
                    return summary(renamed, skipped, log_path, folders_renamed)
                if answer == "n":
                    skipped += 1
                    continue
                if answer == "a":
                    rename_all = True
                if answer == "e":
                    edited = input(f"   New name [{target.name}]: ").strip()
                    if edited:
                        target = target.with_name(safe_filename(edited))
                        target = keep_original_extension(target, pf)
                        if target.exists() and not same_file(target, pf.path):
                            print("   ! That name already exists. Skipped.")
                            skipped += 1
                            continue

            # Final safety net: never let the file's extension change.
            target = keep_original_extension(target, pf)
            if target.suffix != pf.path.suffix:
                print(f"   ! Refusing to rename: extension would change from "
                      f"{pf.path.suffix} to {target.suffix}. Skipped.")
                skipped += 1
                continue

            try:
                move_path(pf.path, target)
            except OSError as e:
                print(f"   ! Rename failed: {e}")
                skipped += 1
                continue
            write_log(log_path, pf.path, target)
            renamed += 1
            print("   Renamed.")

        if dest_root or args.no_folders or not root.is_dir():
            continue
        result = rename_show_folders(args, items, parsed, series, root, log_path, rename_all)
        folders_renamed += result[0]
        log_path, root = result[1], result[2]
        if result[3]:
            print("\nStopped early.")
            return summary(renamed, skipped, log_path, folders_renamed)

    return summary(renamed, skipped, log_path, folders_renamed)


def rename_show_folders(args, items, parsed, series, root, log_path, rename_all):
    """Rename this show's season folders and show folder. Returns (count, log_path, root, quit)."""
    label = show_label(series, not args.no_year, args.tvdb_id_folder)
    plans, notes = plan_folder_renames(items, parsed, series, root, label,
                                       pad_seasons=not args.no_season_padding)
    for note in notes:
        print(f"   Note: {note}")
    count = 0
    for plan in plans:
        what = "Season folder" if plan.kind == "season" else "Show folder"
        print()
        print(f"{what}: {plan.old.name}")
        print(f"   -> {plan.new.name}")
        if plan.new.exists() and not same_file(plan.new, plan.old):
            print("   ! A folder with that name already exists (not merging). Skipped.")
            continue
        if args.dry_run:
            print("   (dry run - not renamed)")
            continue
        if not rename_all:
            while True:
                answer = ask("   Rename folder? [Y]es / [n]o / [q]uit:", "y")
                if answer in {"y", "n", "q"}:
                    break
            if answer == "q":
                return count, log_path, root, True
            if answer == "n":
                continue
        try:
            plan.old.rename(plan.new)
        except OSError as e:
            print(f"   ! Folder rename failed: {e}")
            continue
        # The undo log (and the folder we were pointed at) may live inside this folder.
        if plan.old == log_path.parent or plan.old in log_path.parents:
            log_path = plan.new / log_path.relative_to(plan.old)
        if plan.old == root or plan.old in root.parents:
            root = plan.new / root.relative_to(plan.old)
        write_log(log_path, plan.old, plan.new, plan.kind)
        count += 1
        print("   Renamed.")
    return count, log_path, root, False


def summary(renamed: int, skipped: int, log_path: Path, folders: int = 0) -> int:
    print()
    print(f"Done. Renamed {renamed} file(s) and {folders} folder(s), skipped {skipped}.")
    if renamed or folders:
        print(f"Undo log: {log_path}")
        print(f"To undo: python3 \"{Path(__file__).resolve()}\" --undo \"{log_path}\"")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Rename TV episode files to Plex naming using TheTVDB episode titles.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  python3 tvrename.py "Z:\\TV Shows\\The Office"
  python3 tvrename.py /mnt/nas/tv --dry-run
  python3 tvrename.py ./downloads --show "The Office (2005)"
  python3 tvrename.py ./downloads --organize "Z:\\TV Shows"
  python3 tvrename.py --undo tvrename-log-20261108-101500.jsonl
""")
    p.add_argument("path", nargs="?", default=".", help="folder (or single file) to process")
    p.add_argument("--show", help="use this show name for every file instead of guessing")
    p.add_argument("--dry-run", action="store_true", help="show what would change, rename nothing")
    p.add_argument("--yes", action="store_true", help="don't ask per file (still asks which show)")
    p.add_argument("--auto", action="store_true", help="automatically pick the top search result")
    p.add_argument("--organize", metavar="TV_ROOT",
                   help="also move files into TV_ROOT/Show (Year)/Season XX/")
    p.add_argument("--tvdb-id-folder", action="store_true",
                   help="with --organize, add {tvdb-12345} to show folder names (helps Plex match)")
    p.add_argument("--no-folders", action="store_true",
                   help="don't rename the show folder or season folders")
    p.add_argument("--no-season-padding", action="store_true",
                   help="name season folders 'Season 1' instead of 'Season 01'")
    p.add_argument("--order", default="default", choices=["default", "official", "dvd", "alternate"],
                   help="TheTVDB episode order to use (default: default)")
    p.add_argument("--language", default="eng", help="3-letter language code for titles (default eng)")
    p.add_argument("--no-titles", action="store_true", help="leave the episode title out of the name")
    p.add_argument("--no-year", action="store_true", help="leave the year out of the show name")
    p.add_argument("--no-subtitles", action="store_true", help="don't rename subtitle files")
    p.add_argument("--no-recursive", action="store_true", help="don't look inside subfolders")
    p.add_argument("--api-key", help="TheTVDB API key (or set TVDB_API_KEY)")
    p.add_argument("--pin", help="TheTVDB subscriber PIN, if your key needs one (or TVDB_PIN)")
    p.add_argument("--log", help="where to write the undo log")
    p.add_argument("--undo", metavar="LOG", help="reverse the renames recorded in LOG")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
