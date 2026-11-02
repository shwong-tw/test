# zoom_slides.py — slides from a live Zoom meeting

`zoom_slides.py` watches the shared screen of a Zoom meeting or webinar that
you join **in the browser**, and saves one screenshot per slide. It uses the same
slide-change detection as `browser_download.py` (`slide_detect.py`).

You join the meeting yourself; the script never clicks anything in Zoom. Only
capture meetings whose host or organiser allows it.

---

## 1. Setup

Same as for `browser_download.py`; nothing new to install:

```bash
pip install playwright pillow numpy
```

```bash
playwright install firefox
```

---

## 2. Capturing a meeting, step by step

**1. Start the script with the meeting link** (from the invitation; `/j/` links work):

```bash
python zoom_slides.py "https://us02web.zoom.us/j/81234567890?pwd=abc123"
```

Put the link in quotes (it contains `?`). A Firefox window opens on the link.

**2. Join in that window by hand**, as in a normal browser:

- Click **"Join from your browser"** (if you only see "Launch Meeting", click it
  once and cancel the app dialog — the browser link then appears).
- Enter your name and the passcode, and wait in the waiting room if there is one.
- Audio and camera are optional; slides don't need them.
- If the meeting requires a Zoom sign-in, sign in once. The profile
  `~/.zoom_firefox_profile` remembers it next time.

**3. Make the slides big.** When someone shares, choose **View → Speaker** (or
hide the participant gallery) so the shared screen fills most of the window.
Don't minimise the window; it may stop drawing.

**4. Press Enter in the terminal.** The script shows what it will capture:

```
Capturing <canvas> 1280x640 at (0,40) (shared screen). Saving to zoom_slides/Z20261102_1400/  (Ctrl+C to stop)
```

"(shared screen)" means it found Zoom's share area. If what it names is clearly
not the slides (e.g. a small webcam tile), press Ctrl+C and rerun with
`--region` or `--selector` (section 4).

**5. Let it run.** Every second it checks the picture. Each new slide is saved as:

```
zoom_slides/Z20261102_1400/Z20261102_1400_00h00m05s.png
zoom_slides/Z20261102_1400/Z20261102_1400_00h03m41s.png
...
```

The time is counted from when you pressed Enter. Webcam tiles or a speaker inset
that keep moving are ignored after a few seconds.

**6. It stops** when:

| Stop | Message |
|---|---|
| You press Ctrl+C | `Stopped (Ctrl+C)` |
| The page says the meeting has ended | `Stopped (the meeting has ended)` |
| The shared screen has been gone for `--idle` s (default 60) | `Stopped (no shared screen for 60 s)` |
| `--duration` has passed | `Stopped (--duration ... reached)` |
| You close the window | `Stopped (window closed)` |

It then prints how many slides were saved. If the presenter stops sharing and a
new share starts within `--idle` seconds, capture just continues. Once a share
has been captured, the script does **not** switch to the speaker video when the
share stops.

---

## 3. Common variations

```bash
# Name the output yourself and stop after 90 minutes
python zoom_slides.py "https://zoom.us/j/81234567890" --prefix Keynote --duration 1h30m

# Print each saved slide and a status line every 30 s
python zoom_slides.py "https://zoom.us/j/81234567890" --verbose

# Missed small changes (e.g. one new bullet)? More sensitive, check twice a second
python zoom_slides.py "https://zoom.us/j/81234567890" --threshold 0.2 --sample 0.5

# Too many near-duplicates? Less sensitive
python zoom_slides.py "https://zoom.us/j/81234567890" --threshold 1

# Breaks between talks longer than a minute? Wait longer before stopping
python zoom_slides.py "https://zoom.us/j/81234567890" --idle 600

# Already have the link open elsewhere? Start with an empty window and join there
python zoom_slides.py
```

---

## 4. When it captures the wrong thing

By default the script captures the largest visible `<canvas>`/`<video>` inside
an element whose id, class or tag contains "share" (Zoom's share area), or else
the largest one on the page. It also looks inside Zoom's web components (shadow
DOM) and in every tab of the window: if "Join from your browser" opens the
meeting in a new tab, it prints `Watching tab: …` and follows it. It checks
again every 10 s, so it picks up a share that starts later.

**Capture a fixed rectangle of the window** (`x,y,width,height` in pixels of
the page; the default page size is 1280×720):

```bash
python zoom_slides.py "https://zoom.us/j/81234567890" --region 0,40,1280,640
```

**Name the element**: in the Zoom window, right-click the shared slides →
*Inspect*, find the `<canvas>` and its container, and pass a CSS selector:

```bash
python zoom_slides.py "https://zoom.us/j/81234567890" --selector "#sharee-container canvas"
```

---

## 5. All options

| Option | Default | What it does |
|---|---|---|
| `URL` | (none) | Meeting link to open; without it you get an empty window |
| `--prefix NAME` | `Z<date>_<time>` | Folder and file name prefix |
| `--outdir DIR` | `zoom_slides` | Where the folder goes |
| `--profile DIR` | `~/.zoom_firefox_profile` | Firefox profile (keeps your Zoom sign-in) |
| `--sample S` | `1` | Seconds between checked frames |
| `--threshold P` | `0.5` | % of pixels that must change to count as a new slide |
| `--selector CSS` | | Capture this element |
| `--region X,Y,W,H` | | Capture this rectangle of the page |
| `--size WxH` | `1280x720` | Page size; larger gives bigger screenshots |
| `--duration T` | | Stop after `T` (`90m`, `1h30m`, …) |
| `--idle S` | `60` | Stop when the shared screen has been gone this long |
| `--verbose` | | Print each slide and a status every 30 s |
| `--no-wait` | | Start capturing right away, without waiting for Enter |

---

## 6. Afterwards: transcript by slide

If you get a transcript (`.vtt`) of the same meeting, `VTT_merge.py` accepts the
screenshot folder directly:

```bash
python VTT_merge.py transcript.vtt zoom_slides/Keynote -o Keynote_by_slide.txt
```

The screenshot times count from when you pressed Enter, while a transcript
usually counts from the start of the recording. Press Enter close to when the
recording starts, or the slides and text will be shifted.

---

## 7. Troubleshooting

**"No shared screen (canvas/video) found yet; waiting for one ..."**
The script keeps waiting (until Ctrl+C or `--duration`). After 30 s, and every
30 s after that, it writes what it sees to `zoom_slides/<prefix>/debug.txt` and
`debug.png`: every tab, frame and canvas/video with size and visibility.
- Nobody is sharing yet, or you're still in the waiting room: just wait. Capture
  starts by itself (`found <canvas> …`).
- `debug.png` doesn't show the meeting: the meeting is in another window or
  in the Zoom app. Join in the window the script opened.
- `debug.txt` lists the slides' canvas but it was not picked (e.g. too small or
  hidden): use `--selector` with its id/class, or `--region` (both always work).
  Send `debug.txt` to have the automatic choice adjusted.

**Black or empty screenshots** — the window was minimised or hidden behind
another full-screen app on some desktops; keep it visible. Also try
`sudo apt install ffmpeg`.

**Only one screenshot, though slides changed** — probably a webcam tile or a
blank area was captured. Use `--verbose` to see which element is captured, then
`--region` or `--selector`.

**Stops during a break** — raise `--idle` (e.g. `--idle 900`).

**"Join from your browser" doesn't appear** — click "Launch Meeting", cancel the
dialog that tries to open the Zoom app, and the link appears at the bottom. Some
meetings disable joining from the browser; then this script can't be used.

---

## Quick reference

```bash
python zoom_slides.py "MEETING_LINK"                                 # join, press Enter, Ctrl+C to stop
python zoom_slides.py "MEETING_LINK" --prefix Talk1 --duration 1h --verbose
python zoom_slides.py "MEETING_LINK" --region 0,40,1280,640          # fixed area
```
