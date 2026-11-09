# CLAUDE.md - Media Renamer

Context for picking this project back up. Read this first, then `README.md`.

## Who this is for

- **Owner:** Aaron Matthews. Not a professional developer, but tech-savvy. Explain things in plain
  language, give exact copy-paste commands, and expect him to run things himself on his Mac.
- **His setup:** a Mac, with Python 3.15 from Homebrew (`/opt/homebrew/...`). His media is on a network
  drive (NAS) mounted under `/Volumes/data/...`. Example TV path:
  `/Volumes/data/08-usenet/complete/tv/American Restoration (2010)`. Downloads come from Usenet, so
  file names are often messy or scrambled, with junk files (`.nfo`, samples, `.rar`) mixed in.
- **Media server:** Plex. Every naming decision follows Plex's naming guides.
- **How he likes to work:**
  - Ask questions one at a time.
  - Be warm but direct, and never present a guess as a fact.
  - End answers with an **"Uncertainty Map"** section: what you're least sure of, what may be
    oversimplified, and what would change your opinion.
  - No emojis.
- **Keep scope in check.** He's said he tends to chase new ideas. Prefer finishing and polishing one
  thing over starting several, and say so kindly when a request is drifting.

## What the project is

A tool that renames TV episodes and movies into Plex-friendly names using **TheTVDB** (v4 API), and
cleans out junk files. There are two ways to use it:

1. **Browser app (main way he uses it):** double-click `Media Renamer.command`. It starts a local
   Python web server and opens `tvrename_gui.html` in his browser. Everything runs on his Mac; only
   TheTVDB lookups go out to the internet.
2. **Command line (TV only):** `python3 tvrename.py "<folder>" [--dry-run] [...]`. See `--help`.

Everything uses only the Python standard library. **Don't add pip dependencies** unless he agrees: no
installs is part of why it's easy for him to run.

## Files

| File | What it does |
| --- | --- |
| `tvrename.py` | Core and CLI. TheTVDB client (`TVDB`: series, episodes, movie search), TV file-name reading, Plex naming, extension safety, show/season folder planning, undo log, junk-file clean-up helpers. |
| `movierename.py` | Movie side: reads title/year from messy names, groups parts and subtitles, ranks TheTVDB matches, plans and applies renames. |
| `tvrename_gui.py` | Local web server for the browser app (`127.0.0.1`, random port, secret token per launch). The `/api/*` endpoints wrap the core. |
| `tvrename_gui.html` | The whole browser UI in one file: plain JS, no libraries, light and dark mode. |
| `Media Renamer.command` | Mac double-click launcher. Adds Homebrew to `PATH`, then runs `tvrename_gui.py`. Must stay executable (git mode 100755). |
| `test_tvrename.py` | 46 unittest tests using fake TheTVDB clients. Run with `python3 -m unittest -v`. |
| `README.md` | User-facing instructions. |

The Python file names still say "tvrename" from before movies were added. The visible app name is
**Media Renamer**. He was offered renaming the Python files and hasn't asked for it.

## Naming rules he chose (don't change without asking)

- **TV files:** `Show Name (Year) - S01E01 - Episode Title.ext`, with **uppercase** `S01E01`.
  Multi-episode files use `S01E01-E02`.
- **TV folders:** `Show Name (Year)/Season 01/`, with two-digit seasons by default
  (`--no-season-padding` gives `Season 1`). Season 0 is `Specials`.
- **Movies:** folder **and** file are both exactly `Movie Name (Year)`, as in
  `The Matrix (1999)/The Matrix (1999).mkv`. Loose movies get their own folder (optional).
  - Agreed exceptions: subtitles keep language tags (`.en.srt`), multi-part movies get `- pt1` and
    `- pt2`, and subtitles in a `Subs` folder move next to the movie.
- **File extensions must never change.** `.mkv` stays `.mkv` and `.avi` stays `.avi`, including
  capitalization. This is enforced in several places (`keep_original_extension`, a final check before
  every rename, and typed extensions being dropped in the movie name box). He raised this as a problem
  once, so it's important to him.

## Safety rules built in (keep them)

- **Preview before changing.** Nothing is renamed until he reviews and confirms (the app's review
  list, or the CLI's per-file prompts and `--dry-run`).
- **Undo:** every run writes `tvrename-log-<timestamp>.jsonl` in the scanned folder. Undo reverses
  file moves and folder renames, removes folders it created, and recreates folders it removed. It also
  handles case-only renames on case-insensitive Mac/NAS drives (`same_file` / `os.path.samefile`).
- **Folder renaming guardrails:**
  - Only rename a show folder whose name already resembles the show and that holds only that show.
  - Only rename a season folder whose files are all from one season.
  - Never merge two folders, and never rename a mount point.
- **Clean-up deletes permanently.** Network drives usually have no Trash, and Undo can't restore
  deleted files.
  - Archives (`.rar`, `.zip`) and Plex artwork (`poster.jpg`, `fanart.jpg`) start **unticked**.
  - Videos and subtitles can never be deleted, except samples. The server re-checks every file
    before deleting.
  - Hidden and system files are skipped.
  - Emptied folders are removed, and a `tvrename-cleanup-<timestamp>.jsonl` record is written.
- **Local server security:** the server binds to `127.0.0.1` only. The page needs `?t=<token>`, and
  every API call needs the `X-Token` header.
- **Where settings live:** the API key (and optional PIN) is saved in `~/.tvrename.json`
  (permissions 600), or comes from `TVDB_API_KEY` / `TVDB_PIN`.

## How to test changes

- `python3 -m unittest -v` must pass. Add tests for new behavior.
- **Cloud sessions can't reach `api4.thetvdb.com`** (blocked by the network proxy), and there's no
  real API key. So everything has been tested with fake TheTVDB clients, never against the live API.
  Tell him that plainly, and ask him to do a real `--dry-run` or app run.
- For UI changes, the pattern that worked:
  1. Write a small `serve.py` in the scratchpad that swaps `tr.TVDB` for a fake, sets
     `tvrename_gui.TOKEN = "testtoken"`, and calls `main(["<port>", "--no-browser"])`.
  2. Build a fake folder tree.
  3. Drive the page with Playwright (preinstalled) and check both the screenshots and the files
     on disk.
- Check both light and dark mode.

## History (in order)

1. Built the TV renamer CLI (TheTVDB lookup, per-file confirmation, dry run, undo, subtitles).
2. Helped him run it on his Mac. Problems he hit:
   - `can't open file`: he ran it from the show folder. Use the script's full path.
   - He only had the `main` branch. The work lives on branch `claude/blissful-sagan-b6tdra`.
3. Changed episode codes to uppercase `S01E01`.
4. Hardened extension safety after he reported extensions changing. The cause was never reproduced;
   the best guess was Finder hiding extensions or an edited name.
5. Added show and season folder renaming.
6. Built the browser app and Mac launcher. He said it "looks and works fantastically."
7. Added Movies mode and junk-file clean-up for both TV and movies.
8. Renamed the app and launcher from "TV Renamer" to "Media Renamer."
9. Discussed adding documentaries, music, epubs and audiobooks. Recommendations given (see below).
   He hadn't picked one yet.

## Open ideas and next steps (not started)

- **Documentaries:** Plex treats them as movies or TV shows, so they should already work in the
  existing modes. Next step is for him to test a few. TheTVDB coverage of smaller documentaries is
  unknown.
- **Audiobooks / ebooks:** recommended **Audiobookshelf** (audiobooks and epubs) and/or **Calibre**
  (ebooks) rather than Plex. A possible future "Audiobooks" mode would set up the `Author/Book Title/`
  folder layout for Audiobookshelf.
- **Music:** recommended **MusicBrainz Picard** instead of building it here, because Plex relies
  mostly on the information stored inside music files, not file names.
- **Possible movie improvements:**
  - TMDB as a second lookup source if TheTVDB's movie coverage is thin (needs its own free key).
  - Plex edition tags (`{edition-Director's Cut}`), which were left out to keep names exactly
    `Movie Name (Year)`.
  - Handling Plex extras folders (`Featurettes`, `Behind The Scenes`).
  - Absolute or anime episode numbering for TV.
- He was asked, and hadn't answered yet:
  - Do his movies have editions he wants kept side by side?
  - Are some TV shows loose in the `tv` folder instead of in their own show folders?

## Git

- Work on branch `claude/blissful-sagan-b6tdra` of `ALMattSr/test`. Commit with clear messages and
  push there.
- No pull request has been opened. Don't open one unless he asks.
