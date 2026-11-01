# browser_download.py — tutorial

`browser_download.py` opens each AACR 2026 session page in Firefox (driven by
Playwright) and, for every session:

1. **Captions** — clicks **Transcript** in the Vimeo player and saves the caption
   file (raw WEBVTT) as `output/captions/Sxxx.txt`.
2. **Slides** — plays the video muted at 2× and saves a screenshot whenever the
   slide changes, as `output/slides/Sxxx/Sxxx_00h01m12s.png`.

Sessions come from `AACR2026_Session_URL.txt` (columns `ID`, `Session Title`,
`URL`, …). Everything runs from the terminal; the script opens and controls its
own Firefox window(s).

---

## 1. One-time setup (Ubuntu)

```bash
git clone -b copilot/extract-aacr2026-program-guide https://github.com/shwong-tw/test.git aacr
cd aacr
python3 -m venv venv
source venv/bin/activate
pip install playwright pillow numpy
playwright install firefox
sudo apt install ffmpeg          # video codecs; without them the video may stay black
```

Playwright uses its **own** copy of Firefox. Your normal Firefox and its logins
are not used or changed.

Each time you open a new terminal:

```bash
cd aacr
source venv/bin/activate
```

---

## 2. First run: log in once

```bash
python browser_download.py --only S003 --workers 1 --verbose
```

1. A Firefox window opens on the session page.
2. If a login is needed, the terminal says *"Please log in in the Firefox
   window… Press Enter when you are logged in"*. Log in **in that window**, then
   press **Enter** in the terminal.
3. The script saves the captions, stretches the video to fill the window, starts
   it at 2× and takes screenshots. Leave the window alone until you see
   `[S003] DONE: …`.

Without `--verbose` the terminal shows one line per session (`[S003] DONE: …`).
With `--verbose` it also prints every saved slide (`[S003] slide 12 at
00h05m36s`) and a progress line every 30 s (video time, speed, resolution).

The login is stored in `~/.aacr26_firefox_profile`, so later runs (including
`--headless`) reuse it.

Check the results:

- `output/captions/S003.txt` starts with `WEBVTT`
- `output/slides/S003/` contains the slide images

---

## 3. Choosing sessions

| What | How |
|---|---|
| All sessions | `python browser_download.py` |
| Only some | `--only S003 S015 S020` |
| From an ID onwards | `--start S050` |
| A fixed list (pool) | `--pool Pool.txt` |
| See what would run, without running | `--dry-run` |

### Pool file

A pool file lists the sessions to work on. `--only` and `--start` then choose
from **within** the pool. If `session_ids.txt` exists in the current folder it is
used automatically; otherwise use `--pool FILE`.

Format — spaces, commas or new lines; ranges allowed; `#` starts a comment:

```
# sessions I want
S003, S015
S020-S026
S042
```

```bash
python browser_download.py --pool Pool.txt --dry-run
```

Already-finished sessions are skipped automatically, so you can stop (Ctrl+C) and
rerun the same command at any time. The session that was interrupted starts over.

---

## 4. Running several sessions at once

```bash
python browser_download.py --pool Pool.txt --workers 3
```

Each worker uses its own Firefox window and moves to the next unfinished session
as soon as one is done. Keep the windows open and don't minimise them
(minimised windows may stop playing video). If speed or resolution drops (check
with `--verbose`), use fewer workers.

After you have logged in once, you can hide the windows:

```bash
python browser_download.py --pool Pool.txt --workers 2 --headless
```

With `--headless` nobody can see or answer a "verify you are human" check (see
[Troubleshooting](#8-troubleshooting)).

---

## 5. Human-verification blocks and automatic retry

### Stopping on verification checks

If the site starts showing "verify you are human" (on the page or inside the
Vimeo player), the affected sessions are logged as `CLOUDFLARE`. After
**2 such sessions in a row** (`--max-blocked N` to change), the script stops
starting new sessions. Sessions already running in other windows are allowed
to finish; then the round ends. Without `--retry` the script exits and tells
you how many sessions are left.

### Automatic retry

By default the script runs once. With `--retry`, it waits and runs the
unfinished sessions again:

```bash
python browser_download.py --pool Pool.txt --workers 2 --headless --retry 30m
```

- Wait time: `30m`, `90s`, `1h`, `1h30m`, or a plain number = minutes.
- A round attempts every unfinished session once (unless it is stopped early by
  verification checks, see above); the wait happens between rounds.
- It stops when all sessions are done, when a round finishes **no** session for
  another reason, or after `--max-rounds` (default 10). A round stopped by
  verification checks always waits and retries, up to `--max-rounds`.
- `--overwrite` only applies to the first round.

For long unattended runs:

```bash
nohup python browser_download.py --pool Pool.txt --workers 2 --headless --retry 30m > run.log 2>&1 &
```

---

## 6. Slide screenshots: tuning

| Option | Default | Effect |
|---|---|---|
| `--mode change` / `interval` | `change` | Screenshot on slide change, or every `--interval` seconds of video |
| `--interval` | `10` | Seconds of video between shots in `interval` mode |
| `--threshold` | `0.5` | % of the picture that must change to count as a new slide. Lower (e.g. `0.2`) = more shots, catches small changes such as a new bullet |
| `--sample` | `0.5` | Seconds of video between checked frames. Lower (e.g. `0.25`) = catches slides shown only briefly |
| `--speed` | `2` | Playback speed. Lower = slides stay on screen longer, sessions take longer |
| `--quality` | `720p` | Quality requested from the Vimeo player (`1080p`, `auto`, …) |
| `--size` | `1280x720` | Browser page size; the video is stretched to fill it, so this is the screenshot size |
| `--no-captions` / `--no-screenshots` | | Do only the other part |

How slide detection works: frames are compared at 256×144 by the share of pixels
that changed noticeably; a frame is saved once the picture has settled (not
mid-fade). Areas that keep changing, such as a speaker's webcam inset, are
learned and ignored. Two slides within the same second get a `_2` suffix.

**Missed slides?** Note the time of a missed slide, delete that session's folder
and redo it with lower values:

```bash
rm -rf output/slides/S003
python browser_download.py --only S003 --workers 1 --sample 0.25 --threshold 0.2
```

(The real gap between checks can be longer than `--sample` when screenshots are
slow, e.g. with several workers on a busy CPU.)

---

## 7. Output and log

```
output/
├── captions/S003.txt              # raw WEBVTT
├── slides/S003/
│   ├── S003_00h00m01s.png
│   ├── S003_00h00m36s.png
│   ├── ...
│   └── .done                      # marks the slides as complete
└── download_log.tsv               # one row per processed session
```

A session counts as finished (and is skipped next time) when its captions file
exists **and** its slides folder has `.done`. `--overwrite` redoes finished
sessions, but does not delete old screenshots first — delete the folder yourself
if you want a clean result.

View the log:

```bash
cut -f1-4 output/download_log.tsv | column -t -s $'\t'
```

| Status | Meaning |
|---|---|
| `OK` / `DONE:n` | Captions saved / video finished with *n* slides |
| `NO_VIDEO` | No video on the page (no recording, or the page didn't load) |
| `NO_CAPTION` | Video found but no caption file |
| `NO_PLAY` | Video wouldn't start — see `Sxxx_debug.txt` / `.png` in the slides folder |
| `STALLED` | Video stopped progressing for 90 s; slides so far are kept |
| `CLOUDFLARE` | A "verify you are human" page was shown |
| `ERROR: …` | Unexpected error, with the message |

At the end of a run the script prints the IDs that are not complete, ready for
`--only`.

---

## 8. Troubleshooting

**"Verify you are human" / sessions fail with `CLOUDFLARE`**
The script stops after 2 such sessions in a row (see section 5). The site has
decided it doesn't want this much automated access. The script does
not try to get around this. Options: wait and try later (`--retry`), run fewer
sessions with `--workers 1` and without `--headless`, ask AACR whether
recordings/transcripts are available to attendees, or save the remaining
captions by hand (DevTools → Network → filter "caption" → click Transcript).

**Video stays black or never starts**
Install codecs: `sudo apt install ffmpeg`. If it still fails, look at
`output/slides/Sxxx/Sxxx_debug.txt` (video state, what is under the centre of
the player) and `Sxxx_debug.png`.

**Transcript panel appears in screenshots**
Add `--reload-after-captions` (reloads the page after saving the captions
instead of closing the panel).

**Too many near-duplicate screenshots**
Raise `--threshold` (e.g. `1`).

**Login expired**
Run once without `--headless`; the script asks you to log in again.

---

## 9. Next step: transcript by slide

`VTT_merge.py` regroups a session's captions by slide, using the screenshot
times:

```bash
python VTT_merge.py output/captions/S003.txt output/slides/S003 --time
```

This writes `output/captions/S003_merged.txt` and checks that every caption line
made it into the output. For all sessions:

```bash
for f in output/captions/S*.txt; do s=$(basename "$f" .txt); [ -d "output/slides/$s" ] && python VTT_merge.py "$f" "output/slides/$s" --time; done
```

---

## Quick reference

```bash
python browser_download.py --only S003 --workers 1 --verbose        # first run / login
python browser_download.py --pool Pool.txt --dry-run                 # what would run
python browser_download.py --pool Pool.txt --workers 2               # run the pool
python browser_download.py --pool Pool.txt --workers 2 --headless --retry 30m
python browser_download.py --no-screenshots                          # captions only
python browser_download.py --help                                    # all options
```
