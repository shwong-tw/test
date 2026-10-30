#!/usr/bin/env python3
"""
Download caption files and slide screenshots for AACR 2026 session videos.

For each row of AACR2026_Session_URL.txt (columns: ID, Session Title, URL, ...)
this opens the session page in Firefox (driven by Playwright) and:

1. Captions: clicks "Transcript" on the embedded Vimeo player and saves the
   https://captions.vimeo.com/captions/<id>.vtt?... response (raw WEBVTT) as
   <outdir>/captions/Sxxx.txt -- the same request you see in DevTools > Network.
2. Slides: plays the video muted at --speed (default 2x) and screenshots the
   video whenever the picture changes (slide change), or every --interval
   seconds of video time with --mode interval. Saved as
   <outdir>/slides/Sxxx/Sxxx_0001_00h01m12s.png.

The browser profile is kept in --profile, so you log in by hand on the first
run and later runs reuse the session. Finished sessions are skipped, so the
script can be stopped and restarted. Results are appended to
<outdir>/download_log.tsv.

Setup (once):
    pip install playwright pillow
    playwright install firefox
If videos stay black or never start (H.264 codec missing): sudo apt install ffmpeg

Usage:
    python browser_download.py --only S001 --workers 1     # first run / login
    python browser_download.py --workers 3                 # everything
    python browser_download.py --no-screenshots            # captions only
    python browser_download.py --dry-run                   # list what would run
"""

import argparse
import asyncio
import csv
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

DEFAULT_INPUT = "AACR2026_Session_URL.txt"
DEFAULT_OUTDIR = "output"
DEFAULT_PROFILE = os.path.expanduser("~/.aacr26_firefox_profile")
CAPTION_RE = re.compile(r"captions\.vimeo\.com/captions/.*\.vtt|/texttrack/.*\.vtt")
PLANNING_PATH = "/event/aacr2026/planning/"
DONE_MARKER = ".done"

FIREFOX_PREFS = {
    # Allow muted autoplay and keep decoding video in windows that are not focused.
    "media.autoplay.default": 0,
    "media.autoplay.blocking_policy": 0,
    "media.suspend-bkgnd-video.enabled": False,
    "media.block-autoplay-until-in-foreground": False,
}

# Vimeo player overlays (title, controls, side buttons) that would otherwise
# show up in screenshots.
HIDE_PLAYER_UI_CSS = """
.vp-controls-wrapper, .vp-controls, .vp-title, .vp-sidedock, .vp-overlay-wrapper,
.vp-target, .vp-badge, .vp-menu, .vp-unmute, .vp-preview, .vp-spin,
[class*="ControlBar"], [class*="TitleBar"], [class*="SideDock"],
[data-control-bar], [data-title-bar], [data-sidedock] { display: none !important; }
"""


# --------------------------------------------------------------------------
# Session list
# --------------------------------------------------------------------------

def read_sessions(path, only=None, start=None):
    """Return [{'ID', 'Session Title', 'URL', ...}] filtered by --only/--start."""
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    if not rows or "ID" not in rows[0] or "URL" not in rows[0]:
        sys.exit(f"{path}: expected tab-separated columns 'ID' and 'URL' "
                 "(regenerate it with extract_url.py)")
    if only:
        wanted = {s.upper() for s in only}
        rows = [r for r in rows if r["ID"].upper() in wanted]
    if start:
        rows = [r for r in rows if r["ID"].upper() >= start.upper()]
    return rows


def caption_path(outdir, sid):
    return Path(outdir) / "captions" / f"{sid}.txt"


def slides_dir(outdir, sid):
    return Path(outdir) / "slides" / sid


def is_done(outdir, sid, want_captions, want_slides):
    if want_captions and not caption_path(outdir, sid).exists():
        return False
    if want_slides and not (slides_dir(outdir, sid) / DONE_MARKER).exists():
        return False
    return True


def parse_size(text):
    w, h = text.lower().split("x")
    return int(w), int(h)


def fmt_time(seconds):
    s = int(seconds)
    return f"{s // 3600:02d}h{s % 3600 // 60:02d}m{s % 60:02d}s"


# --------------------------------------------------------------------------
# Slide-change detection (pure Pillow, no browser needed)
# --------------------------------------------------------------------------

def thumbnail(png_bytes, size=(64, 36)):
    from PIL import Image
    return Image.open(io.BytesIO(png_bytes)).convert("L").resize(size)


def image_diff(a, b):
    """Mean absolute pixel difference (0-255) between two thumbnails."""
    from PIL import ImageChops, ImageStat
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


def is_blank(thumb, max_mean=16.0, min_stddev=2.0):
    """True for dark, (nearly) uniform frames, e.g. black frames while loading."""
    from PIL import ImageStat
    stat = ImageStat.Stat(thumb)
    return stat.mean[0] < max_mean and stat.stddev[0] < min_stddev


class SlideDetector:
    """
    Decide which sampled frames to keep.

    A frame is kept when it differs from the last kept frame by more than
    `threshold` AND is stable (differs from the previous sample by at most
    `stable`), so frames captured mid-transition or mid-animation are skipped
    and the settled slide is saved instead. Blank frames are never kept.
    """

    def __init__(self, threshold=10.0, stable=None):
        self.threshold = threshold
        self.stable = threshold / 2 if stable is None else stable
        self.last_saved = None
        self.prev = None

    def feed(self, thumb):
        prev, self.prev = self.prev, thumb
        if is_blank(thumb):
            return False
        if self.last_saved is None:
            if prev is not None and image_diff(thumb, prev) > self.stable:
                return False          # still changing (e.g. fading in)
            self.last_saved = thumb
            return True
        if image_diff(thumb, self.last_saved) <= self.threshold:
            return False
        if prev is None or image_diff(thumb, prev) > self.stable:
            return False
        self.last_saved = thumb
        return True


# --------------------------------------------------------------------------
# Browser helpers
# --------------------------------------------------------------------------

async def is_cloudflare_challenge(page):
    """True if Cloudflare's "verify you are human" page is shown."""
    try:
        title = (await page.title()).lower()
    except Exception:
        title = ""
    if "just a moment" in title or "attention required" in title:
        return True
    return any("challenges.cloudflare.com" in (f.url or "") for f in page.frames)


async def dismiss_cookie_banner(page):
    """Best effort: click an 'Accept'/'Allow all' cookie button if one is shown."""
    pattern = re.compile(r"^(accept( all)?( cookies)?|allow all( cookies)?|i agree|agree|got it)$", re.I)
    for frame in page.frames:
        try:
            btn = frame.get_by_role("button", name=pattern)
            if await btn.count():
                await btn.first.click(timeout=2000)
                await page.wait_for_timeout(500)
                return True
        except Exception:
            pass
    return False


async def find_video_frame(page, timeout=30):
    """Return the frame (main page or iframe) containing a <video>, or None."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Prefer Vimeo frames (AACR embeds vimeo.com/event/<id>/embed, which
        # nests the player), then any frame with a video.
        frames = sorted(page.frames, key=lambda f: "vimeo.com" not in (f.url or ""))
        for frame in frames:
            try:
                if await frame.locator("video").count():
                    return frame
            except Exception:
                pass                  # frame detached / navigating
        await page.wait_for_timeout(1000)
    return None


VIDEO_STATE_JS = """() => {
  const v = document.querySelector('video');
  if (!v) return null;
  return {t: v.currentTime, d: v.duration, ended: v.ended, paused: v.paused,
          rate: v.playbackRate, w: v.videoWidth, h: v.videoHeight};
}"""


async def video_state(frame):
    return await frame.evaluate(VIDEO_STATE_JS)


async def set_player_ui_hidden(frame, hidden):
    """Toggle the CSS that hides the Vimeo player UI (it must be visible to click play)."""
    try:
        await frame.evaluate("""([css, hidden]) => {
          let st = document.getElementById('bd-hide-ui');
          if (!st) { st = document.createElement('style'); st.id = 'bd-hide-ui';
                     st.textContent = css; document.head.appendChild(st); }
          st.disabled = !hidden;
        }""", [HIDE_PLAYER_UI_CSS, hidden])
    except Exception:
        pass


async def is_advancing(page, frame, wait_ms=1500):
    """True if the video is playing and its time moves forward."""
    s1 = await video_state(frame)
    await page.wait_for_timeout(wait_ms)
    s2 = await video_state(frame)
    return bool(s1 and s2 and not s2["paused"] and s2["t"] > s1["t"] + 0.2)


async def ensure_playing(page, frame, speed, log=print, label=""):
    """
    Make the video play (muted, at `speed`). Returns True if it is playing.

    Players such as Vimeo's own the play state and may pause a video started
    from script, so this falls back to clicking the player the way a person
    does: a real mouse click on the centre of the video.
    """
    set_rate_js = ("(s) => { const v = document.querySelector('video');"
                   " v.muted = true; v.playbackRate = s; }")

    async def set_rate():
        await vimeo_api(frame, "setPlaybackRate", speed)
        await frame.evaluate(set_rate_js, speed)

    await set_rate()
    if await is_advancing(page, frame, 1000):
        return True

    async def paused():
        st = await video_state(frame)
        return bool(st and st["paused"])

    attempts = [
        ("player API", lambda: api_play(frame, speed)),
        ("play()", lambda: frame.evaluate(
            "() => document.querySelector('video').play().catch(() => {})")),
        ("click on video", lambda: click_center(page, frame)),
        ("play button", lambda: click_play_button(frame)),
        ("keyboard", lambda: press_play_key(page, frame)),
    ]
    await set_player_ui_hidden(frame, False)
    try:
        for name, action in attempts:
            if not await paused() and await is_advancing(page, frame, 1000):
                break
            if await paused():
                try:
                    await action()
                except Exception:
                    continue
            await set_rate()
            if await is_advancing(page, frame):
                if name not in ("player API", "play()"):
                    log(f"  [{label}] started playback via {name}")
                break
        else:
            return False
    finally:
        await page.mouse.move(0, 0)   # move away so hover UI fades out
        await set_player_ui_hidden(frame, True)
    await set_rate()
    return True


async def click_center(page, frame):
    box = await frame.locator("video").first.bounding_box()
    if box:
        await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


async def click_play_button(frame):
    for sel in ("button.vp-big-play-button", "button[aria-label*='Play' i]",
                "[data-play-button]", "button.play"):
        loc = frame.locator(sel)
        if await loc.count():
            await loc.first.click(timeout=3000)
            return


async def press_play_key(page, frame):
    await frame.locator("video").first.focus()
    await page.keyboard.press("Space")


async def start_playback(page, frame, speed, log=print, label=""):
    """Play the video muted at `speed`; returns the video state, or None if it never starts."""
    if not await ensure_playing(page, frame, speed, log, label):
        return None
    # Wait for a finite duration (metadata loaded).
    for _ in range(30):
        state = await video_state(frame)
        if state and state["d"] and state["d"] != float("inf"):
            return state
        await page.wait_for_timeout(1000)
    return await video_state(frame)


EXPAND_IFRAME_JS = """el => {
  el.style.cssText = 'position:fixed !important; top:0 !important; left:0 !important;'
    + 'width:100vw !important; height:100vh !important; max-width:none !important;'
    + 'max-height:none !important; margin:0 !important; border:0 !important;'
    + 'z-index:2147483647 !important; background:#000 !important;';
  document.documentElement.style.overflow = 'hidden';
}"""


async def expand_player(page, frame, size=(1280, 720)):
    """
    Make the video fill the browser window: resize the window's page area to
    `size` and stretch every iframe between the page and the player (AACR page
    -> vimeo.com/event embed -> player.vimeo.com) to the full window.
    """
    if size and page.viewport_size != {"width": size[0], "height": size[1]}:
        await page.set_viewport_size({"width": size[0], "height": size[1]})
    f = frame
    while f.parent_frame is not None:
        try:
            el = await f.frame_element()
            await el.evaluate(EXPAND_IFRAME_JS)
        except Exception:
            pass
        f = f.parent_frame
    await page.wait_for_timeout(1000)


async def vimeo_api(frame, method, value=None):
    """
    Send a command to the Vimeo player through its embed API (postMessage
    from the frame that embeds the player), e.g. "play", "setQuality".
    """
    if frame.parent_frame is None:
        return
    msg = {"method": method} if value is None else {"method": method, "value": value}
    try:
        await frame.parent_frame.evaluate(
            "(msg) => { for (const f of document.querySelectorAll('iframe'))"
            " { try { f.contentWindow.postMessage(msg, '*'); } catch (e) {} } }",
            json.dumps(msg))
    except Exception:
        pass


async def request_quality(frame, quality):
    """
    Ask the Vimeo player for a quality (e.g. "720p"). Has no effect if the
    video doesn't offer a quality choice; the player then picks one itself,
    usually matching the player size.
    """
    if quality:
        await vimeo_api(frame, "setQuality", quality)


async def api_play(frame, speed):
    await vimeo_api(frame, "setVolume", 0)
    await vimeo_api(frame, "setPlaybackRate", speed)
    await vimeo_api(frame, "play")


async def save_debug(page, frame, out_dir, prefix):
    """Save a screenshot and a description of the player, for when playback fails."""
    out_dir = Path(out_dir)
    await set_player_ui_hidden(frame, False)    # report what a click would really hit
    lines = ["Frames:"] + [f"  {f.url}" for f in page.frames]
    try:
        info = await frame.evaluate("""() => {
          const v = document.querySelector('video');
          const desc = el => el ? (el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')
              + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).join('.') : '')
              + (el.getAttribute('aria-label') ? ` [aria-label="${el.getAttribute('aria-label')}"]` : '')) : null;
          const at = desc(document.elementFromPoint(innerWidth / 2, innerHeight / 2));
          const buttons = [...document.querySelectorAll('button, [role=button]')].slice(0, 60)
              .map(b => desc(b) + ' "' + (b.innerText || '').trim().slice(0, 40) + '"');
          return {video: v && {paused: v.paused, t: v.currentTime, d: v.duration, ready: v.readyState,
                               network: v.networkState, error: v.error && v.error.code,
                               muted: v.muted, rate: v.playbackRate, w: v.videoWidth, h: v.videoHeight},
                  elementAtCenter: at, buttons};
        }""")
        lines += [f"Video frame: {frame.url}", f"Video: {info['video']}",
                  f"Element at centre of player: {info['elementAtCenter']}", "Buttons:"]
        lines += [f"  {b}" for b in info["buttons"]]
    except Exception as e:
        lines.append(f"(could not inspect player: {e})")
    (out_dir / f"{prefix}_debug.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        await page.screenshot(path=str(out_dir / f"{prefix}_debug.png"))
    except Exception:
        pass


async def capture_slides(page, frame, out_dir, prefix, mode="change", interval=10.0,
                         threshold=10.0, speed=2.0, sample=0.5, max_seconds=None,
                         stall_timeout=90, size=(1280, 720), quality="720p", verbose=False,
                         log=print):
    """
    Play the video in `frame` and save screenshots to `out_dir`.

    The player is first stretched to fill a `size` window and asked for
    `quality`, so screenshots come out at that size.

    Returns a dict: status (DONE/STALLED/NO_PLAY), saved, duration,
    video_seconds, wall_seconds, rate (measured video-seconds per wall-second).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    await expand_player(page, frame, size)
    await request_quality(frame, quality)
    state = await start_playback(page, frame, speed, log, prefix)
    await request_quality(frame, quality)    # again, once the player is active
    if not state:
        log(f"  [{prefix}] could not start the video; saved {prefix}_debug.txt/.png in {out_dir}")
        await save_debug(page, frame, out_dir, prefix)
        return {"status": "NO_PLAY", "saved": 0, "duration": 0,
                "video_seconds": 0, "wall_seconds": 0, "rate": 0}

    duration = state["d"] or 0
    start_t, start_wall = state["t"], time.monotonic()
    end_t = duration - 0.5 if duration else float("inf")
    if max_seconds:
        end_t = min(end_t, start_t + max_seconds)

    video = frame.locator("video").first
    detector = SlideDetector(threshold)
    next_interval_t = start_t
    saved, status = 0, "DONE"
    last_t, last_progress = state["t"], time.monotonic()
    last_report = time.monotonic()

    while True:
        state = await video_state(frame)
        if state is None:
            status = "NO_VIDEO"
            break
        t = state["t"]
        if state["ended"] or t >= end_t:
            break

        # Keep it playing at the requested speed (Vimeo autopause, buffering, etc.).
        if state["paused"]:
            log(f"  [{prefix}] video paused at {fmt_time(t)}, resuming")
            await ensure_playing(page, frame, speed, log, prefix)
        elif abs(state["rate"] - speed) > 0.01:
            await frame.evaluate(
                "(s) => { document.querySelector('video').playbackRate = s; }", speed)

        if t > last_t + 0.01:
            last_t, last_progress = t, time.monotonic()
        if verbose and time.monotonic() - last_report >= 30:
            wall = time.monotonic() - start_wall
            log(f"  [{prefix}] at {fmt_time(t)} / {fmt_time(duration)}, "
                f"speed {(t - start_t) / wall if wall else 0:.2f}x, "
                f"video {state['w']}x{state['h']}, {saved} slides")
            last_report = time.monotonic()
        elif time.monotonic() - last_progress > stall_timeout:
            status = "STALLED"
            break

        png = await video.screenshot(type="png")
        # Stamp the frame with the time right after the screenshot: the first
        # screenshot on a page can take a while, so `t` may lag the picture.
        t = (await frame.evaluate("() => document.querySelector('video').currentTime")) or t
        if mode == "interval":
            keep = t >= next_interval_t
            if keep:
                next_interval_t += interval
        else:
            keep = detector.feed(thumbnail(png))

        if keep:
            saved += 1
            name = f"{prefix}_{saved:04d}_{fmt_time(t)}.png"
            (out_dir / name).write_bytes(png)
            log(f"  [{prefix}] slide {saved} at {fmt_time(t)}")

        await page.wait_for_timeout(int(sample * 1000))

    wall = time.monotonic() - start_wall
    video_seconds = last_t - start_t
    return {"status": status, "saved": saved, "duration": duration,
            "video_seconds": video_seconds, "wall_seconds": wall,
            "rate": video_seconds / wall if wall else 0}


async def click_transcript(page, video_frame=None, timeout=5000):
    """
    Click a 'Transcript' button on the page or inside any frame.

    On AACR session pages the button is part of the Vimeo player's control
    bar, which only appears while the mouse is over the video, so hover first.
    """
    if video_frame is not None:
        try:
            await video_frame.locator("video").first.hover(timeout=timeout)
            await page.wait_for_timeout(500)
        except Exception:
            pass
    for frame in [page.main_frame] + [f for f in page.frames if f != page.main_frame]:
        for loc in (frame.get_by_role("button", name=re.compile("transcript", re.I)),
                    frame.get_by_text(re.compile(r"^\s*transcript\s*$", re.I))):
            try:
                if await loc.count():
                    await loc.first.click(timeout=timeout)
                    return True
            except Exception:
                pass
    return False


async def caption_urls_from_config(frame):
    """Caption URLs listed in the Vimeo player's config (fallback)."""
    try:
        urls = await frame.evaluate("""() => {
          const c = window.playerConfig || (window.vimeo && window.vimeo.config);
          const tracks = (c && c.request && c.request.text_tracks) || [];
          return tracks.map(t => t.url).filter(Boolean);
        }""")
    except Exception:
        return []
    return [urljoin(frame.url, u) for u in urls]


# --------------------------------------------------------------------------
# Per-session work
# --------------------------------------------------------------------------

async def process_session(page, row, args):
    sid, url = row["ID"], row["URL"]
    result = {"ID": sid, "Caption": "", "Slides": "", "Duration": "",
              "Caption URL": "", "Title": row.get("Session Title", "")}
    want_captions = not args.no_captions and (args.overwrite or not caption_path(args.outdir, sid).exists())
    want_slides = not args.no_screenshots and (
        args.overwrite or not (slides_dir(args.outdir, sid) / DONE_MARKER).exists())

    captured = []

    def on_response(resp):
        if CAPTION_RE.search(resp.url):
            captured.append(resp)

    page.on("response", on_response)
    try:
        if page.url != url:           # the login check may have opened it already
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1500)
        if await is_cloudflare_challenge(page):
            print(f"[{sid}] blocked by a Cloudflare check")
            result["Caption"] = result["Slides"] = "CLOUDFLARE"
            return result
        await dismiss_cookie_banner(page)
        frame = await find_video_frame(page, args.timeout)
        if frame is None:
            result["Caption"] = result["Slides"] = "NO_VIDEO"
            return result

        if want_captions:
            clicked = False
            if not captured:
                clicked = await click_transcript(page, frame)
            for _ in range(args.timeout * 2):
                if captured:
                    break
                await page.wait_for_timeout(500)
            text, cap_url = None, ""
            for resp in captured:
                try:
                    text, cap_url = await resp.text(), resp.url
                    break
                except Exception:
                    continue
            if text is None:
                for u in await caption_urls_from_config(frame):
                    r = await page.context.request.get(u)
                    if r.ok:
                        text, cap_url = await r.text(), u
                        break
            if text is None:
                result["Caption"] = "NO_CAPTION"
            else:
                path = caption_path(args.outdir, sid)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                result["Caption"] = "OK" if text.lstrip("﻿").startswith("WEBVTT") else "NOT_VTT"
                result["Caption URL"] = cap_url
                print(f"[{sid}] captions saved ({len(text)} chars)")
            if clicked and want_slides:
                # Start the video from a clean page so the transcript panel
                # doesn't cover it.
                await page.reload(wait_until="domcontentloaded")
                frame = await find_video_frame(page, args.timeout)
                if frame is None:
                    result["Slides"] = "NO_VIDEO"
                    return result
        else:
            result["Caption"] = "SKIPPED"

        if want_slides:
            out = slides_dir(args.outdir, sid)
            stats = await capture_slides(
                page, frame, out, sid, mode=args.mode, interval=args.interval,
                threshold=args.threshold, speed=args.speed, size=args.size,
                quality=None if args.quality == "auto" else args.quality, verbose=args.verbose)
            result["Slides"] = f"{stats['status']}:{stats['saved']}"
            result["Duration"] = fmt_time(stats["duration"])
            print(f"[{sid}] {stats['status']}: {stats['saved']} slides, "
                  f"video {fmt_time(stats['duration'])}, rate {stats['rate']:.2f}x")
            if stats["status"] == "DONE":
                (out / DONE_MARKER).write_text("")
        else:
            result["Slides"] = "SKIPPED"
    except Exception as e:
        result["Slides"] = result["Slides"] or f"ERROR: {type(e).__name__}: {e}".replace("\t", " ")[:200]
        print(f"[{sid}] ERROR {type(e).__name__}: {e}")
    finally:
        page.remove_listener("response", on_response)
    return result


LOG_FIELDS = ["ID", "Caption", "Slides", "Duration", "Caption URL", "Title"]


def append_log(outdir, result):
    path = Path(outdir) / "download_log.tsv"
    new = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, delimiter="\t")
        if new:
            w.writeheader()
        w.writerow(result)


async def ensure_logged_in(page, url):
    """Open `url` in `page`; if a login is needed, wait for the user to log in."""
    for attempt in range(3):
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        if await is_cloudflare_challenge(page):
            print("Cloudflare is blocking the automated browser on this site; stopping.")
            return False
        on_session = PLANNING_PATH in page.url
        has_login = await page.locator("input[type='password']").count() > 0
        if on_session and not has_login:
            return True
        print("\nPlease log in in the Firefox window (the session page should open).")
        await asyncio.to_thread(input, "Press Enter when you are logged in... ")
    return False


async def run(args, rows):
    from playwright.async_api import async_playwright

    Path(args.outdir).mkdir(parents=True, exist_ok=True)
    queue = asyncio.Queue()
    for r in rows:
        queue.put_nowait(r)
    results = []

    async with async_playwright() as p:
        context = await p.firefox.launch_persistent_context(
            args.profile, headless=args.headless, firefox_user_prefs=FIREFOX_PREFS,
            viewport={"width": args.size[0], "height": args.size[1]})
        try:
            # Reuse the window Firefox opens at start (instead of leaving it blank).
            first_page = context.pages[0] if context.pages else await context.new_page()
            if not await ensure_logged_in(first_page, rows[0]["URL"]):
                sys.exit("Not logged in; stopping.")

            async def worker(n):
                page = first_page if n == 1 else await context.new_page()
                while True:
                    try:
                        row = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    print(f"[{row['ID']}] worker {n}: {row['URL']}")
                    res = await process_session(page, row, args)
                    append_log(args.outdir, res)
                    results.append(res)
                    await page.wait_for_timeout(2000)
                await page.close()

            await asyncio.gather(*(worker(i + 1) for i in range(args.workers)))
        finally:
            await context.close()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Session list (TSV with ID, URL)")
    parser.add_argument("--outdir", default=DEFAULT_OUTDIR, help="Output folder")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="Firefox profile folder (keeps login)")
    parser.add_argument("--only", nargs="+", metavar="SXXX", help="Only these session IDs")
    parser.add_argument("--start", metavar="SXXX", help="Start from this session ID")
    parser.add_argument("--workers", type=int, default=3, help="Sessions processed in parallel")
    parser.add_argument("--no-captions", action="store_true", help="Skip caption download")
    parser.add_argument("--no-screenshots", action="store_true", help="Skip slide screenshots")
    parser.add_argument("--mode", choices=["change", "interval"], default="change",
                        help="Screenshot on slide change, or every --interval seconds")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds of video between shots (interval mode)")
    parser.add_argument("--threshold", type=float, default=10.0,
                        help="Slide-change sensitivity: mean pixel difference 0-255 (lower = more shots)")
    parser.add_argument("--speed", type=float, default=2.0, help="Playback speed")
    parser.add_argument("--quality", default="720p", help="Video quality to request, e.g. 720p, 1080p, auto")
    parser.add_argument("--size", default="1280x720", type=parse_size,
                        help="Browser page size the video is expanded to (WIDTHxHEIGHT)")
    parser.add_argument("--timeout", type=int, default=30, help="Seconds to wait for video/captions")
    parser.add_argument("--overwrite", action="store_true", help="Redo sessions that are already done")
    parser.add_argument("--headless", action="store_true", help="Hide the browser (after you have logged in)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print a progress line (video time, speed, resolution) every 30 s")
    parser.add_argument("--dry-run", action="store_true", help="List the sessions that would be processed")
    args = parser.parse_args()

    rows = read_sessions(args.input, args.only, args.start)
    todo = [r for r in rows if args.overwrite or not is_done(
        args.outdir, r["ID"], not args.no_captions, not args.no_screenshots)]
    print(f"{len(rows)} sessions selected, {len(rows) - len(todo)} already done, {len(todo)} to process")

    if args.dry_run:
        for r in todo:
            print(f"  {r['ID']}\t{r['URL']}\t{r.get('Session Title', '')}")
        if not args.no_screenshots and todo:
            print(f"Screenshots play each video at {args.speed:g}x with {args.workers} worker(s): "
                  f"about {len(todo)} x video length / {args.speed * args.workers:g} "
                  f"(e.g. 90-min videos: ~{len(todo) * 90 / (args.speed * args.workers) / 60:.0f} h)")
        return
    if not todo:
        return

    try:
        import PIL  # noqa: F401
        import playwright  # noqa: F401
    except ImportError as e:
        sys.exit(f"Missing dependency ({e.name}). Run: pip install playwright pillow && playwright install firefox")

    results = asyncio.run(run(args, todo))
    ok_cap = sum(r["Caption"] == "OK" for r in results)
    ok_sl = sum(r["Slides"].startswith("DONE") for r in results)
    print(f"\nFinished {len(results)} sessions: captions OK {ok_cap}, slides done {ok_sl}. "
          f"See {Path(args.outdir) / 'download_log.tsv'}")
    failed = [r["ID"] for r in results
              if r["Caption"] not in ("OK", "SKIPPED") or not r["Slides"].startswith(("DONE", "SKIPPED"))]
    if failed:
        print("Not complete (rerun with --only): " + " ".join(failed))


if __name__ == "__main__":
    main()
