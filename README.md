# tvrename - Plex-friendly TV episode renamer (powered by TheTVDB)

Point it at a folder of TV episodes. For each file it:

1. Reads the season/episode number from the file name (`S02E05`, `2x05`, `Season 2 Episode 5`, multi-episode `S01E01E02` / `S01E01-E03`).
2. Figures out the show from the file name, or from the folder name if the file name has none (e.g. `Breaking Bad/Season 2/S02E03.mkv`).
3. Searches TheTVDB and asks you to confirm the right show (once per show, not once per file).
4. Gets the real episode title and shows you the new name.
5. Asks **Yes / No / All remaining / Edit / Quit** before renaming each file.

Result, following [Plex's naming guide](https://support.plex.tv/articles/naming-and-organizing-your-tv-show-files/):

```
The.Office.S02E05.720p.HDTV.x264.mkv   ->   The Office (US) (2005) - S02E05 - Halloween.mkv
The.Office.S02E05.720p.HDTV.en.srt     ->   The Office (US) (2005) - S02E05 - Halloween.en.srt
```

Subtitles keep their language tag (`.en`, `.forced`, etc.) so Plex still recognizes them.
File extensions are never changed: `.mkv` stays `.mkv`, `.avi` stays `.avi`.

### Folders get renamed too

After a show's episodes are done, the tool renames its folders (asking first, like it does for files):

```
Ray Donovan/                    ->   Ray Donovan (2013)/
    Season 1/                   ->       Season 01/
    Season 2/                   ->       Season 02/
    Specials/                   ->       Specials/
```

To stay safe on a shared drive, it only renames:
- a **show folder** whose name already resembles the show and that holds only that show's episodes
  (so a folder like `TV` or `Downloads` full of different shows is never renamed), and
- a **season folder** whose name mentions the season (`Season 1`, `S1`, `Ray.Donovan.S01.1080p`)
  and whose episodes are all from that season.

It never merges two folders. If `Season 02` already exists next to `Season 2`, it leaves both alone and tells you.
Use `--no-folders` to rename files only, or `--no-season-padding` for `Season 1` instead of `Season 01`.

## Setup (one time)

1. Install **Python 3.8 or newer** ([python.org](https://www.python.org/downloads/)). Nothing else to install.
2. Get a TheTVDB API key: create an account, then request a key at <https://thetvdb.com/api-information>.
   Some keys also need a subscriber PIN; the tool will ask for it.
3. The first time you run the tool it asks for the key and offers to save it to `~/.tvrename.json`.
   (You can also use `--api-key` or the `TVDB_API_KEY` environment variable.)

## Everyday use

Always try a **dry run** first. It shows every new name without changing anything:

```
python3 tvrename.py "Z:\TV Shows\The Office" --dry-run
```

Then do it for real, file by file:

```
python3 tvrename.py "Z:\TV Shows\The Office"
```

On Windows, use `python` or `py` instead of `python3` if that's how Python is installed.
On a Mac, a network share usually lives under `/Volumes/<ShareName>/...`.

### Handy options

| Option | What it does |
| --- | --- |
| `--dry-run` | Preview only, rename nothing |
| `--show "The Office (2005)"` | Use this show for every file instead of guessing |
| `--yes` | Don't ask per file (still asks which show) |
| `--auto` | Automatically pick the top TheTVDB search result |
| `--organize "Z:\TV Shows"` | Also move files into `Show (Year)/Season 02/` folders |
| `--tvdb-id-folder` | With `--organize`, name folders like `Show (2005) {tvdb-73244}` so Plex matches exactly |
| `--order dvd` | Use DVD episode order instead of aired order |
| `--no-titles` | Leave out episode titles (`Show (2005) - S02E05.mkv`) |
| `--no-subtitles` | Leave subtitle files alone |
| `--no-folders` | Don't rename the show folder or season folders |
| `--no-season-padding` | Name season folders `Season 1` instead of `Season 01` |
| `--no-recursive` | Don't look inside subfolders |
| `--undo LOGFILE` | Put everything back the way it was |

### Undo

Every run writes a log (`tvrename-log-<date>.jsonl`) in the folder you processed (if that folder gets
renamed, the log moves with it). Undo reverses file *and* folder renames. To reverse a run:

```
python3 tvrename.py --undo "Z:\TV Shows\The Office\tvrename-log-20261108-101500.jsonl"
```

### Tips when choosing the show

At the show prompt you can:
- press **Enter** to take the top result,
- type a **number** to pick a different one,
- type a **different name** to search again, or
- type **`id 73244`** to use the exact TheTVDB id (it's in the show's URL on thetvdb.com).

## Running the tests

```
python3 -m unittest -v
```
