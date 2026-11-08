"""
movierename.py - the movie side of TV Renamer.

Turns messy movie downloads into Plex's movie layout:

    The.Matrix.1999.1080p.BluRay.x264-GRP/
        the.matrix.1999.1080p.mkv
        Subs/English.srt
  becomes
    The Matrix (1999)/
        The Matrix (1999).mkv
        The Matrix (1999).en.srt

The folder and the file share exactly the same "Movie Name (Year)" name, and
every file keeps its original extension.
"""
from __future__ import annotations

import datetime as _dt
import difflib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import tvrename as tr

THIS_YEAR = _dt.date.today().year
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
PART_RE = re.compile(r"(?<![a-z0-9])(?:cd|disc|disk|part|pt)[ ._-]?([1-9])(?!\d)", re.IGNORECASE)
# Technical words that mark the end of a title when there's no year to go by.
JUNK_TOKEN_RE = re.compile(
    r"^(\d{3,4}p|4k|uhd|hdr|hdr10|dv|bluray|blu-ray|brrip|bdrip|bdremux|dvdrip|dvdscr|dvd|webrip|"
    r"web-dl|webdl|hdtv|hdrip|remux|x264|x265|h264|h265|h\.264|h\.265|hevc|avc|xvid|divx|aac|ac3|"
    r"eac3|dts|dts-hd|truehd|atmos|ddp?5\.?1|10bit|8bit|yify|yts|rarbg)$",
    re.IGNORECASE,
)
LANGUAGE_WORDS = {
    "english": "en", "eng": "en", "spanish": "es", "espanol": "es", "french": "fr", "german": "de",
    "italian": "it", "portuguese": "pt", "dutch": "nl", "swedish": "sv", "norwegian": "no",
    "danish": "da", "finnish": "fi", "polish": "pl", "russian": "ru", "japanese": "ja",
    "chinese": "zh", "korean": "ko", "arabic": "ar", "hebrew": "he", "hindi": "hi", "turkish": "tr",
    "greek": "el", "czech": "cs", "hungarian": "hu", "romanian": "ro", "ukrainian": "uk",
    "vietnamese": "vi", "thai": "th", "indonesian": "id",
}


# --------------------------------------------------------------------------- #
# Reading movie names
# --------------------------------------------------------------------------- #
def parse_title(text: str) -> tuple[str, int | None, int | None]:
    """'The.Matrix.1999.1080p.BluRay' -> ('The Matrix', 1999, None)."""
    t = re.sub(r"\[[^\]]*\]", " ", text)
    t = re.sub(r"\{[^}]*\}", " ", t)
    t = re.sub(r"[._]+", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip()

    part = None
    m = PART_RE.search(t)
    if m and m.start() > 0:
        part = int(m.group(1))

    years = [y for y in YEAR_RE.finditer(t) if y.start() > 0 and int(y.group(1)) <= THIS_YEAR + 1]
    if years:
        y = years[-1]  # "Blade Runner 2049 2017" -> 2017;  "1917 2019" -> 2019
        title, year = t[: y.start()], int(y.group(1))
    else:
        title, year = t, None
    # Cut at the first technical word (1080p, BluRay, x264...) or a part marker
    words = title.split(" ")
    for i, w in enumerate(words):
        if i > 0 and (JUNK_TOKEN_RE.match(w.strip("()[]-")) or PART_RE.fullmatch(w)):
            words = words[:i]
            break
    title = " ".join(words)
    title = re.sub(r"[\s\-(\[]+$", "", title).strip(" -")
    return title, year, part


def looks_obfuscated(stem: str) -> bool:
    """Random download names like 'a8f7e6d5c4b3' or 'xyz123abc456def'."""
    s = stem.strip()
    return len(s) < 2 or (" " not in s and "." not in s and "_" not in s and "-" not in s
                          and len(s) >= 12 and any(c.isdigit() for c in s))


@dataclass
class Movie:
    id: str
    title: str
    year: int | None
    folder: Path                    # the folder the movie lives in
    parts: list[tuple[Path, int | None]] = field(default_factory=list)  # (video, part number)
    subs: list[tuple[Path, str]] = field(default_factory=list)          # (subtitle, suffix to keep)
    own_folder: bool = False        # True if the folder holds only this movie

    @property
    def files(self) -> list[Path]:
        return [p for p, _ in self.parts] + [p for p, _ in self.subs]


def movie_dir_for(path: Path) -> Path:
    d = path.parent
    if d.name.lower() in tr.SUBTITLE_DIR_NAMES:
        d = d.parent
    return d


def guess_movie(path: Path, root: Path) -> tuple[str, int | None, int | None]:
    stem = path.name[: -len(path.suffix)] if path.suffix else path.name
    t_file = parse_title(stem)
    folder = movie_dir_for(path)
    # A folder that holds many movies (like "Movies") says nothing about this one,
    # so only lean on the scanned folder's own name when the file name is gibberish.
    t_dir = parse_title(folder.name) if (folder != root or looks_obfuscated(stem)) else ("", None, None)
    # Prefer whichever name has a year; the file name wins a tie unless it's gibberish.
    if t_file[1] and not looks_obfuscated(stem):
        return t_file
    if t_dir[1] and t_dir[0]:
        return t_dir[0], t_dir[1], t_file[2]
    if looks_obfuscated(stem) and t_dir[0]:
        return t_dir[0], t_dir[1], t_file[2]
    return t_file


def subtitle_suffix(path: Path) -> str:
    """Keep language tags; also understand 'English.srt' or '2_English.srt' from Subs folders."""
    suffix, _ = tr.file_suffix(path)
    if suffix != path.suffix:
        return suffix
    stem = path.name[: -len(path.suffix)].lower()
    words = re.split(r"[^a-z]+", stem)
    for w in words:
        if w in LANGUAGE_WORDS:
            extra = ".forced" if "forced" in words else (".sdh" if "sdh" in words else "")
            return f".{LANGUAGE_WORDS[w]}{extra}{path.suffix}"
    return path.suffix


def scan_movies(root: Path, include_subs: bool = True) -> list[Movie]:
    files = tr.find_files(root, recursive=True, include_subs=include_subs)
    videos = [f for f in files if f.suffix.lower() in tr.VIDEO_EXTS]
    subs = [f for f in files if f.suffix.lower() in tr.SUBTITLE_EXTS]

    movies: dict[tuple, Movie] = {}
    for v in videos:
        title, year, part = guess_movie(v, root)
        folder = movie_dir_for(v)
        key = (folder, _norm(title), year)
        if key not in movies:
            movies[key] = Movie(id=f"m{len(movies)}", title=title or v.stem, year=year, folder=folder)
        movies[key].parts.append((v, part))

    by_folder: dict[Path, list[Movie]] = {}
    for m in movies.values():
        by_folder.setdefault(m.folder, []).append(m)

    for s in subs:
        folder = movie_dir_for(s)
        candidates = by_folder.get(folder, [])
        target = None
        if len(candidates) == 1:
            target = candidates[0]
        elif candidates:
            title, year, _ = parse_title(s.name[: -len(s.suffix)])
            for m in candidates:
                if _norm(m.title) and _norm(m.title) == _norm(title) and (not year or year == m.year):
                    target = m
                    break
        if target:
            target.subs.append((s, subtitle_suffix(s)))

    all_videos = set(videos)
    for m in movies.values():
        mine = {p for p, _ in m.parts}
        inside = {v for v in all_videos if m.folder == v.parent or m.folder in v.parents}
        m.own_folder = (
            inside == mine
            and (m.folder == root or root in m.folder.parents)
            and not os.path.ismount(m.folder)
        )
        m.parts.sort(key=lambda t: (t[1] or 0, str(t[0]).lower()))
    return sorted(movies.values(), key=lambda m: (m.title.lower(), m.year or 0))


# --------------------------------------------------------------------------- #
# Matching on TheTVDB
# --------------------------------------------------------------------------- #
def _norm(text: str) -> str:
    t = (text or "").lower().replace("&", "and")
    t = re.sub(r"\([^)]*\)", "", t)
    t = re.sub(r"^(the|a|an) ", "", t.strip())
    return re.sub(r"[^a-z0-9]", "", t)


def rank_matches(title: str, year: int | None, results: list[dict]) -> list[dict]:
    """Best match first, each with a 'confidence' of 'sure' or 'check'."""
    scored = []
    for r in results:
        ratio = difflib.SequenceMatcher(None, _norm(title), _norm(r.get("name", ""))).ratio()
        ry = r.get("year")
        bonus = 0.5 if (year and ry == year) else (0.2 if (year and ry and abs(ry - year) == 1) else 0)
        scored.append((ratio + bonus, ratio, r))
    scored.sort(key=lambda t: t[0], reverse=True)
    ranked = []
    for i, (_, ratio, r) in enumerate(scored):
        sure = i == 0 and year is not None and r.get("year") == year and ratio >= 0.92
        ranked.append({**r, "confidence": "sure" if sure else "check"})
    return ranked


def find_matches(tvdb, movie: Movie, cache: dict | None = None) -> list[dict]:
    cache = {} if cache is None else cache
    key = (_norm(movie.title), movie.year)
    if key not in cache:
        results = tvdb.search_movies(movie.title, movie.year) if movie.year else []
        if not results:
            results = tvdb.search_movies(movie.title)
        cache[key] = rank_matches(movie.title, movie.year, results)[:8]
    return cache[key]


# --------------------------------------------------------------------------- #
# Planning and applying
# --------------------------------------------------------------------------- #
def movie_label(name: str, year: int | None) -> str:
    return tr.show_label({"id": 0, "name": name, "year": year}, include_year=True)


@dataclass
class MoviePlan:
    movie: Movie
    label: str
    dest_dir: Path
    folder_action: str | None            # "rename", "create" or None
    moves: list[tuple[Path, Path]]       # (current file, final path)
    status: str = "ok"                   # "ok", "same", "exists"
    message: str = ""


def plan_movie(movie: Movie, label: str, own_folders: bool = True) -> MoviePlan:
    label = tr.safe_filename(label)
    if movie.own_folder:
        dest_dir = movie.folder.with_name(label)
        folder_action = "rename" if dest_dir.name != movie.folder.name else None
    elif own_folders:
        dest_dir = movie.folder / label
        folder_action = "create"
    else:
        dest_dir = movie.folder
        folder_action = None

    multi = len(movie.parts) > 1
    moves = []
    for i, (path, part) in enumerate(movie.parts, 1):
        suffix = f" - pt{part or i}" if multi else ""
        moves.append((path, dest_dir / f"{label}{suffix}{path.suffix}"))
    used = {t.name.lower() for _, t in moves}
    for path, suffix in movie.subs:
        name = f"{label}{suffix}"
        n = 2
        while name.lower() in used:  # two English subtitles -> keep both, numbered
            name = f"{label}.{n}{suffix}"
            n += 1
        used.add(name.lower())
        moves.append((path, dest_dir / name))

    plan = MoviePlan(movie, label, dest_dir, folder_action, moves)
    if folder_action == "rename" and dest_dir.exists() and not tr.same_file(dest_dir, movie.folder):
        plan.status, plan.message = "exists", f'A folder named "{label}" already exists here'
    elif not folder_action and all(src == dst for src, dst in moves):
        plan.status, plan.message = "same", "Already named correctly"
    else:
        staging = movie.folder if folder_action == "rename" else dest_dir
        for src, dst in moves:
            target = staging / dst.name
            if target != src and target.exists() and not tr.same_file(target, src):
                plan.status, plan.message = "exists", f'"{dst.name}" already exists'
                break
    return plan


def apply_movie(plan: MoviePlan, log_path: Path, root: Path) -> tuple[list[dict], Path, Path]:
    """Do the renames for one movie. Returns (results, log_path, root) - both may move."""
    results = []
    movie = plan.movie

    def log(old, new, kind="file"):
        tr.write_log(log_path, old, new, kind)

    if plan.folder_action == "create":
        created = not plan.dest_dir.exists()
        plan.dest_dir.mkdir(parents=True, exist_ok=True)
        if created:
            log(Path(""), plan.dest_dir, "mkdir")

    # 1. move/rename files into place (inside the current folder if it gets renamed later)
    staging = movie.folder if plan.folder_action == "rename" else plan.dest_dir
    left_dirs = set()
    for src, dst in plan.moves:
        target = staging / dst.name
        if target == src:
            continue
        if target.exists() and not tr.same_file(target, src):
            results.append({"old": src.name, "new": dst.name, "ok": False, "message": "Name already in use"})
            continue
        if target.suffix != src.suffix:
            results.append({"old": src.name, "new": dst.name, "ok": False, "message": "Extension would change"})
            continue
        try:
            tr.move_path(src, target)
        except OSError as e:
            results.append({"old": src.name, "new": dst.name, "ok": False, "message": str(e)})
            continue
        log(src, target)
        results.append({"old": src.name, "new": dst.name, "ok": True})
        if src.parent != staging:
            left_dirs.add(src.parent)

    # 2. tidy folders that are now empty (e.g. an emptied 'Subs' folder)
    for d in left_dirs:
        if d != staging and staging in d.parents:
            tr.prune_empty_dirs(d, staging, log_path)

    # 3. rename the movie's own folder last
    if plan.folder_action == "rename":
        try:
            movie.folder.rename(plan.dest_dir)
        except OSError as e:
            results.append({"old": movie.folder.name, "new": plan.dest_dir.name, "ok": False,
                            "message": f"{e}. Is a file in it open (for example, in Plex)?"})
            return results, log_path, root
        if movie.folder == log_path.parent or movie.folder in log_path.parents:
            log_path = plan.dest_dir / log_path.relative_to(movie.folder)
        if movie.folder == root or movie.folder in root.parents:
            root = plan.dest_dir / root.relative_to(movie.folder)
        log(movie.folder, plan.dest_dir, "movie")
        results.append({"old": movie.folder.name, "new": plan.dest_dir.name, "ok": True, "folder": True})
    return results, log_path, root
