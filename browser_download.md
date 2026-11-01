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

What you will see in the window for each session: the page loads once, the
Transcript panel opens and closes (captions saved), the video is stretched to
fill the window (two quick jumps), the player controls flash while playback is
started, then the video plays muted at 2×. If playback needs a click, the
script clicks the video itself.

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

A `--pool` file that doesn't exist is an error; a missing `session_ids.txt`
simply means "all sessions". The script prints how many IDs it read and warns
about IDs that are not in `AACR2026_Session_URL.txt`.

Already-finished sessions are skipped automatically, so you can stop (Ctrl+C) and
rerun the same command at any time. The session that was interrupted starts over.

---

## 4. Running several sessions at once

```bash
python browser_download.py --pool Pool.txt --workers 3
```

Each worker uses its own Firefox window.

**Workers start one after another.** Worker 2 opens its window only after
worker 1's first session has shown its video. Worker 3 waits until two sessions
have, and so on. So if the site is already blocking, it gets one page request
at a time, not `--workers` at once, and the run can stop after the first two
blocked sessions (section 5). If sessions fail before showing their video,
the remaining workers never start.

**After a video ends**, the worker marks
the session done, adds a row to the log, waits 2 s and loads the next unfinished
session **in the same window**. A failed session never pauses the run — the
worker just moves on. When the list is used up, each window closes and a summary
is printed. Keep the windows open and don't minimise them
(minimised windows may stop playing video). If speed or resolution drops (check
with `--verbose`), use fewer workers. Many parallel sessions (e.g. `--workers 7`)
also make verification checks more likely; 2–3 is a safer choice.

After you have logged in once, you can hide the windows:

```bash
python browser_download.py --pool Pool.txt --workers 2 --headless
```

With `--headless` nobody can see or answer a "verify you are human" check (see
[Troubleshooting](#9-troubleshooting)).

---

## 5. Human-verification blocks and automatic retry

### Stopping on verification checks

A session counts as **blocked** when either:

- `CLOUDFLARE`: a verification check is recognised. This means the page title is
  "Just a moment…" or "Attention required", or a `challenges.cloudflare.com`
  frame is present, or the page or any frame contains "verify you are human",
  "verifying you are human", "verify the security of your connection" or
  "checking your browser".
- `NO_VIDEO`: the page never showed a video player within `--timeout` seconds.
  Every session page has a video, so this usually means a check the text
  matching didn't recognise. A screenshot and the frame list are saved as
  `output/debug/Sxxx_no_video.png` / `.txt` so you can see what was shown.

Blocks are counted in the order sessions finish, and any session that gets
past the check (shows its video) resets the count. After **2 blocked sessions
in a row** (`--max-blocked N` to change), the script prints

```
2 sessions in a row hit a human-verification check (or showed no video); not starting new sessions this round (sessions already running will finish first).
```

and stops starting new sessions. Workers still waiting for their turn never
open a window. Sessions already running in other windows are allowed to finish;
then the round ends. Without `--retry` the script exits and tells
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
├── debug/                         # only if a page showed no video
│   ├── S005_no_video.png          #   screenshot of what was shown
│   └── S005_no_video.txt          #   page title and frame URLs
└── download_log.tsv               # one row per processed session
```

### How "finished" is decided

| Part | Done when this exists | Written when |
|---|---|---|
| Captions | `output/captions/Sxxx.txt` | the caption file was downloaded |
| Slides | `output/slides/Sxxx/.done` | the video played **to the end** |

- Both exist → the session is skipped (its page is not even opened).
- One is missing → only the missing part is redone (e.g. captions exist but
  slides were interrupted → only the video is captured again).
- With `--no-captions` / `--no-screenshots` that part is ignored.
- Interrupted (Ctrl+C), `STALLED`, `NO_PLAY`, `CLOUDFLARE`, `NO_VIDEO`, errors → no `.done`,
  so the slides are redone next time. `NO_CAPTION` → no captions file, retried
  every run.
- Only existence is checked, inside `--outdir`.

To force a redo: delete the captions file and/or the slides folder, or use
`--overwrite` with `--only`/`--pool`. Note that `--overwrite` (and an
interrupted capture) does **not** delete old screenshots first — delete
`output/slides/Sxxx/` yourself for a clean set.

View the log:

```bash
cut -f1-4 output/download_log.tsv | column -t -s $'\t'
```

| Status | Meaning |
|---|---|
| `OK` / `DONE:n` | Captions saved / video finished with *n* slides |
| `NO_VIDEO` | No video player on the page within `--timeout` — usually an unrecognised verification check; counts as blocked. See `output/debug/Sxxx_no_video.png` |
| `NO_CAPTION` | Video found but no caption file |
| `NO_PLAY` | Video wouldn't start — see `Sxxx_debug.txt` / `.png` in the slides folder |
| `STALLED` | Video stopped progressing for 90 s; slides so far are kept |
| `CLOUDFLARE` | A "verify you are human" check was recognised; counts as blocked |
| `ERROR: …` | Unexpected error, with the message |

At the end of a run the script prints the IDs that are not complete, ready for
`--only`.

---

## 8. All options

| Option | Default | Meaning |
|---|---|---|
| `--input FILE` | `AACR2026_Session_URL.txt` | Session list (TSV with `ID`, `URL`) |
| `--outdir DIR` | `output` | Output folder |
| `--profile DIR` | `~/.aacr26_firefox_profile` | Firefox profile that keeps the login |
| `--pool FILE` | `session_ids.txt` if present | Session IDs to work on |
| `--only S… S…` | | Only these IDs (within the pool) |
| `--start SXXX` | | From this ID on (within the pool) |
| `--workers N` | `3` | Sessions in parallel, one window each |
| `--no-captions` / `--no-screenshots` | | Skip that part |
| `--mode change\|interval` | `change` | Screenshot on slide change / every `--interval` s |
| `--interval S` | `10` | Seconds of video between shots (interval mode) |
| `--threshold P` | `0.5` | % of pixels that must change for a new slide |
| `--sample S` | `0.5` | Seconds of video between checked frames |
| `--speed X` | `2` | Playback speed |
| `--quality Q` | `720p` | Requested quality (`1080p`, `auto`, …) |
| `--size WxH` | `1280x720` | Page size = screenshot size |
| `--timeout S` | `30` | Seconds to wait for the video / captions to appear |
| `--reload-after-captions` | | Reload the page instead of closing the transcript panel |
| `--overwrite` | | Redo sessions that are already done |
| `--headless` | | No visible windows (log in once without it first) |
| `--verbose` | | Print each saved slide and a progress line every 30 s |
| `--retry WAIT` | off | Wait and rerun unfinished sessions (`30m`, `1h`, …) |
| `--max-rounds N` | `10` | Maximum rounds with `--retry` |
| `--max-blocked N` | `2` | Stop the round after N blocked sessions (`CLOUDFLARE`/`NO_VIDEO`) in a row |
| `--dry-run` | | List what would be processed, then exit |

Option names can be shortened as long as they stay unique (e.g. `--worker 2`).

---

## 9. Troubleshooting

**"Verify you are human" / sessions fail with `CLOUDFLARE` or `NO_VIDEO`**
The script stops after 2 such sessions in a row (see section 5). For
`NO_VIDEO`, open `output/debug/Sxxx_no_video.png` to see what the page showed
instead of the video. If it is a check with different wording, its phrase can
be added to the recognised list. The site has
decided it doesn't want this much automated access. The script does
not try to get around this. Options: wait and try later (`--retry`), run fewer
sessions with `--workers 1` and without `--headless`, ask AACR whether
recordings/transcripts are available to attendees, or save the remaining
captions by hand (DevTools → Network → filter "caption" → click Transcript).

**The run didn't stop although many sessions failed**
Look at the statuses in `download_log.tsv`. Only `CLOUDFLARE` and `NO_VIDEO`
count as blocked; `NO_CAPTION`, `NO_PLAY`, `STALLED` and errors don't (the page
did show its video). Any session that shows its video resets the count.

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

## 10. Next step: transcript by slide (`VTT_merge.py`)

`VTT_merge.py` regroups a session's captions by slide. Slide times come from the
screenshot folder (`Sxxx_00h01m12s.png` names) or from a list file with one
time per line (`Sxxx_Interval.txt`):

```bash
python VTT_merge.py output/captions/S003.txt output/slides/S003 --time
python VTT_merge.py S003.txt S003_Interval.txt -o S003_slides.txt
```

Output (`<transcript>_merged.txt` by default):

```
001
00:00:00 --> 00:00:01          <- only with --time
Great. Welcome everybody.

002
00:00:01 --> 00:00:36
This session is AACR, The
Next Generation of Extracellular Vesicles
...
```

How it works:

- Slide times t1, t2, … give intervals `0–t1`, `t1–t2`, …, `tn–end of
  transcript`, numbered `01`, `02`, … (3 digits when there are more than 99).
- Each caption cue goes **whole** to the interval it overlaps most; on a tie, to
  the earlier one. Text is never split or duplicated.
- An interval with no speech keeps its number with empty text — this happens
  when slide changes come faster than the cues (e.g. an animation detected as
  many slides).

Options:

| Option | Meaning |
|---|---|
| `--time` | Print each interval's time range |
| `-o FILE` | Output file |
| `--start 01h00m00s` | Only cues starting at/after this time (and the slides from then on) |
| `--end 01h30m00s` | Only cues starting before this time |

`--start`/`--end` also accept `01:00:00`. Blocks keep the same numbers as in a
full run, so a windowed output can be compared with the full one.

**Built-in check.** After writing, the script re-reads the transcript
independently and compares it with the output: every caption line must appear
exactly once, in order (within `--start`/`--end` if given). It prints
`Check OK: …` or `Check FAILED` with the missing / extra lines and unreadable
timing lines, and exits with status 1 on failure.

All sessions at once:

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
python VTT_merge.py output/captions/S003.txt output/slides/S003 --time
```
