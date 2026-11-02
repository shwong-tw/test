# zoom_slides.py — slides from a live Zoom meeting

`zoom_slides.py` watches the shared screen of a Zoom meeting or webinar and
saves one screenshot per slide. It uses the same slide-change detection as
`browser_download.py` (`slide_detect.py`). It has two modes:

| Mode | How you join | What the script does |
|---|---|---|
| **Browser** (default, sections 2, 4, 5) | In the Firefox window the script opens | Finds Zoom's share canvas in the page |
| **Screen** (`--screen`, section 3) | The normal way: your own browser or the Zoom app | Screenshots a screen area; no browser involved |

Many meetings refuse the script's Firefox with **"automated bots aren't allowed
to join this meeting"**. That is the host's/Zoom's choice and the script does not
try to get around it — use `--screen` for those.

You always join the meeting yourself; the script never clicks anything in Zoom.
Only capture meetings whose host or organiser allows it.

---

## 1. Setup

Same as for `browser_download.py`; nothing new to install:

```bash
pip install playwright pillow numpy
```

```bash
playwright install firefox
```

For `--screen` also:

```bash
pip install mss
```

`--screen` needs an **X11 session** on Linux. Check with:

```bash
echo $XDG_SESSION_TYPE
```

`x11` is fine. If it says `wayland`, screenshots taken by scripts are usually
black: log out, click the gear icon on the login screen and choose
**"Ubuntu on Xorg"**.

---

## 2. Capturing a meeting, step by step

**1. Start the script with the meeting link** (from the invitation; `/j/` links work):

```bash
python zoom_slides.py "https://us02web.zoom.us/j/81234567890?pwd=abc123"
```

Put the link in quotes (it contains `?`). A Firefox window opens. Meeting links
like `…/j/98569677492?pwd=…` are opened as Zoom's browser-join page
(`…/wc/join/98569677492?pwd=…`), so the "open the Zoom app / join from your
browser" chooser is skipped. (`--app-page` opens the link as given.)

**2. Join in that window by hand**, as in a normal browser:

- If Zoom still shows the chooser, click **"Join from your browser"** (if you
  only see "Launch Meeting", click it once and cancel the app dialog — the
  browser link then appears). The script never clicks anything in Zoom; until
  you join, it just waits.
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
`--region` or `--selector` (section 5).

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

## 3. Screen mode (`--screen`): when the browser is refused

Use this when Zoom says "automated bots aren't allowed to join this meeting",
or whenever you prefer the Zoom app. No browser is started; the script only
takes a screenshot of one area of your screen every second.

**1. Join the meeting the normal way** — your own Firefox/Chrome, or the Zoom
app. Put the Zoom window where it will stay, and make the shared slides large
(View → Speaker, or full screen).

**2. Find the area to capture.** See your monitors:

```bash
python zoom_slides.py --screen --list-monitors
```

```
  --monitor 0: 3840x1080 at (0,0)  all monitors together
  --monitor 1: 1920x1080 at (0,0)  monitor 1
  --monitor 2: 1920x1080 at (1920,0)  monitor 2
```

Either capture a whole monitor (`--monitor 2`), or a rectangle in the same
pixel coordinates with `--region X,Y,WIDTH,HEIGHT`. A rectangle around just the
slides works best (no taskbar, clock or chat). To read coordinates, take a
screenshot with the Screenshot tool (or `gnome-screenshot -a`) and note the
corners, or start with a whole monitor and narrow down.

**3. Check the area with a test shot** (saves one image and exits):

```bash
python zoom_slides.py --screen --region 0,80,1920,960 --test-shot --prefix Keynote
```

Open `zoom_slides/Keynote/test_shot.png`: it should show the slides and little
else. Black means the wrong area or a Wayland session (see Setup).

**4. Start capturing:**

```bash
python zoom_slides.py --screen --region 0,80,1920,960 --prefix Keynote --verbose
```

Press Enter when the slides are showing. Slides are saved as
`zoom_slides/Keynote/Keynote_00h01m12s.png`, as in browser mode.

**5. Keep the area uncovered.** The script captures exactly what is on screen
there: another window, a notification or the screen saver ends up in the
images. Turn off the screen lock/blanking for long meetings
(Settings → Power → Screen Blank: Never). If the area turns black, the script
prints a warning.

**6. Stopping:** Ctrl+C, or `--duration 1h30m`. The script can't see the
meeting itself, so it doesn't know when it ends; `--idle 10m` stops it when the
picture hasn't changed for 10 minutes (slides can stay up for a long time
during discussion, so don't make it too short).

---

## 4. Common variations

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

## 5. When it captures the wrong thing

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

## 6. All options

| Option | Default | What it does |
|---|---|---|
| `URL` | (none) | Meeting link to open; without it you get an empty window |
| `--prefix NAME` | `Z<date>_<time>` | Folder and file name prefix |
| `--outdir DIR` | `zoom_slides` | Where the folder goes |
| `--profile DIR` | `~/.zoom_firefox_profile` | Firefox profile (keeps your Zoom sign-in) |
| `--sample S` | `1` | Seconds between checked frames |
| `--threshold P` | `0.5` | % of pixels that must change to count as a new slide |
| `--selector CSS` | | Capture this element |
| `--region X,Y,W,H` | | Capture this rectangle of the page (with `--screen`: of the screen) |
| `--size WxH` | `1280x720` | Page size; larger gives bigger screenshots |
| `--duration T` | | Stop after `T` (`90m`, `1h30m`, …) |
| `--idle T` | `60` s / never | Browser: stop when the shared screen has been gone this long. `--screen`: stop when the picture hasn't changed this long (off unless given). Seconds, or `10m` |
| `--verbose` | | Print each slide and a status every 30 s |
| `--no-wait` | | Start capturing right away, without waiting for Enter |
| `--app-page` | | Open the link as given (Zoom's app/browser chooser) instead of the browser-join page |
| `--screen` | | Screen mode: capture a screen area, no browser (section 3) |
| `--monitor N` | `1` | `--screen`: monitor to capture (0 = all together) |
| `--list-monitors` | | `--screen`: print monitors and their coordinates, then exit |
| `--test-shot` | | `--screen`: save one `test_shot.png` of the area, then exit |

`URL`, `--selector`, `--profile`, `--size` and `--app-page` apply to browser mode only.

---

## 7. Afterwards: transcript by slide

If you get a transcript (`.vtt`) of the same meeting, `VTT_merge.py` accepts the
screenshot folder directly:

```bash
python VTT_merge.py transcript.vtt zoom_slides/Keynote -o Keynote_by_slide.txt
```

The screenshot times count from when you pressed Enter, while a transcript
usually counts from the start of the recording. Press Enter close to when the
recording starts, or the slides and text will be shifted.

---

## 8. Troubleshooting

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

**Stuck on the "open Zoom app / join from browser" page** — the script waits
there until you choose "Join from your browser". `/j/` links skip this page
automatically; for other links, click it yourself.

**"Automated bots aren't allowed to join this meeting"** — Zoom refuses the
script's browser for this meeting. Join normally and use `--screen` (section 3).

**`--screen` images are black** — Wayland session (choose "Ubuntu on Xorg" at
login), the area is wrong (`--list-monitors`, `--test-shot`), or the screen
blanked/locked.

**`--screen` saved a chat popup / notification as a slide** — capture a tighter
`--region` around the slides, close the chat panel, and turn on Do Not Disturb.

**"Join from your browser" doesn't appear** — click "Launch Meeting", cancel the
dialog that tries to open the Zoom app, and the link appears at the bottom. Some
meetings disable joining from the browser; then this script can't be used.

---

## Quick reference

```bash
python zoom_slides.py "MEETING_LINK"                                 # join, press Enter, Ctrl+C to stop
python zoom_slides.py "MEETING_LINK" --prefix Talk1 --duration 1h --verbose
python zoom_slides.py "MEETING_LINK" --region 0,40,1280,640          # fixed area
python zoom_slides.py --screen --list-monitors                       # screen mode: find the area
python zoom_slides.py --screen --region 0,80,1920,960 --test-shot    # check it
python zoom_slides.py --screen --region 0,80,1920,960 --prefix Talk1 # join yourself, Enter, Ctrl+C
```
