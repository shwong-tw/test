#!/usr/bin/env python3
"""
Save a screenshot of each new slide shared in a live Zoom meeting or webinar.

The script opens Firefox (driven by Playwright) on the meeting link. You join
by hand in that window ("Join from your browser", name, passcode, waiting
room); the script doesn't click anything in Zoom. When the shared slides are
on screen, press Enter in the terminal. From then on the shared screen is
checked every --sample seconds and each new slide is saved as
    <outdir>/<prefix>/<prefix>_00h01m12s.png
(time since you pressed Enter). Slide changes are detected with the same code
as browser_download.py (slide_detect.py); webcam tiles that keep moving are
ignored.

It stops on Ctrl+C, when the meeting ends, when the shared screen has been
gone for --idle seconds, after --duration, or when you close the window.

What is captured: by default the largest visible <canvas>/<video> in the page
(the shared screen once someone shares; Zoom draws it on a canvas). Override
with --selector CSS, or capture a fixed rectangle with --region X,Y,W,H.

Only capture meetings whose host or organiser allows it.

Screen mode (--screen): when Zoom refuses the automated browser ("automated
bots aren't allowed to join"), join the normal way yourself -- in your own
browser or the Zoom app -- and run with --screen. No browser is started; the
script only takes screenshots of a screen area (--monitor N or --region
X,Y,W,H in screen pixels) and keeps the new slides. Needs an X11 session on
Linux (on Wayland screenshots are usually black): check with
echo $XDG_SESSION_TYPE.

Setup (once, same as browser_download.py):
    pip install playwright pillow numpy
    playwright install firefox
    pip install mss                            # for --screen

Usage:
    python zoom_slides.py https://zoom.us/j/81234567890?pwd=abc123
    python zoom_slides.py https://zoom.us/j/81234567890 --prefix Keynote --duration 1h30m
    python zoom_slides.py https://zoom.us/j/81234567890 --region 0,40,1280,640
    python zoom_slides.py                      # open an empty window, join yourself
    python zoom_slides.py --screen --list-monitors
    python zoom_slides.py --screen --region 0,80,1920,1000 --test-shot
    python zoom_slides.py --screen --region 0,80,1920,1000 --prefix Keynote
"""

import argparse
import asyncio
import os
import re
import sys
import threading
import time
from pathlib import Path

from browser_download import FIREFOX_PREFS, parse_duration, parse_size
from slide_detect import SlideDetector, fmt_time, is_blank, thumbnail

DEFAULT_PROFILE = os.path.expanduser("~/.zoom_firefox_profile")
DEFAULT_OUTDIR = "zoom_slides"
DONE_MARKER = ".done"
ENDED_PHRASES = ("meeting has been ended", "host has ended this meeting", "this meeting has ended",
                 "webinar has ended", "you have been removed", "the meeting has ended")
REFIND_EVERY = 10         # seconds: look again for the largest canvas (share may start/stop)

# Walk the document including open shadow roots (newer Zoom clients put the
# video inside web components).
WALK_JS = """
function zsAll(root, out) {
  root.querySelectorAll('*').forEach(el => {
    if (el.tagName === 'CANVAS' || el.tagName === 'VIDEO') out.push(el);
    if (el.shadowRoot) zsAll(el.shadowRoot, out);
  });
  return out;
}
function zsDesc(el) {
  const cls = typeof el.className === 'string' ? el.className.trim() : '';
  return el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (cls ? '.' + cls.split(/\\s+/).join('.') : '');
}
function zsShare(el) {
  for (let e = el, n = 0; e && n < 10; n++) {
    const cls = typeof e.className === 'string' ? e.className : '';
    if (/share/i.test((e.id || '') + ' ' + cls + ' ' + e.tagName)) return true;
    e = e.parentElement || (e.getRootNode && e.getRootNode().host) || null;
  }
  return false;
}
"""

# Visible <canvas>/<video> elements in a frame, largest first, each tagged with
# a data-zs attribute so it can be found again:
# [id, area, x, y, w, h, tag, share] where `share` means the element or a
# parent has "share" in its id/class/tag (Zoom's shared-screen container).
CANDIDATES_JS = "() => {" + WALK_JS + """
  const out = [];
  const all = zsAll(document, []);
  all.forEach(el => el.removeAttribute('data-zs'));
  all.forEach((el, i) => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 160 || r.height < 90 || st.visibility === 'hidden' || st.display === 'none'
        || parseFloat(st.opacity) === 0) return;
    const w = Math.min(r.right, innerWidth) - Math.max(r.left, 0);
    const h = Math.min(r.bottom, innerHeight) - Math.max(r.top, 0);
    if (w <= 0 || h <= 0) return;
    el.setAttribute('data-zs', String(i));
    out.push([String(i), w * h, Math.round(r.left), Math.round(r.top), Math.round(r.width),
              Math.round(r.height), el.tagName.toLowerCase(), zsShare(el)]);
  });
  return out.sort((a, b) => b[1] - a[1]);
}"""

# Every <canvas>/<video> (visible or not), for the debug file.
DEBUG_JS = "() => {" + WALK_JS + """
  return zsAll(document, []).map(el => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return `${zsDesc(el)}  ${Math.round(r.width)}x${Math.round(r.height)} at (${Math.round(r.left)},${Math.round(r.top)})`
      + `  display=${st.display} visibility=${st.visibility} opacity=${st.opacity}`
      + (zsShare(el) ? '  [share]' : '') + (el.getRootNode() !== document ? '  [in shadow DOM]' : '');
  });
}"""

EVAL_TIMEOUT = 3          # seconds; a busy or stuck frame must not block the loop
ZOOM_PAGE_RE = re.compile(r"zoom\.(us|com)/(wc|j|w|s)/|app\.zoom\.(us|com)")


async def evaluate(frame, js):
    """frame.evaluate with a time limit (raises on timeout)."""
    return await asyncio.wait_for(frame.evaluate(js), EVAL_TIMEOUT)


def pick_page(context):
    """The tab to watch: the meeting tab if there is one, else the newest tab.
    ("Join from your browser" may open the meeting in a new tab.)"""
    pages = [pg for pg in context.pages if not pg.is_closed()]
    if not pages:
        return None
    for pg in reversed(pages):
        if "/wc/" in (pg.url or ""):
            return pg
    for pg in reversed(pages):
        if ZOOM_PAGE_RE.search(pg.url or ""):
            return pg
    return pages[-1]


def browser_join_url(url):
    """Rewrite a Zoom meeting link (https://<host>/j/<id>?pwd=...) to the
    browser-client link (https://<host>/wc/join/<id>?pwd=...), which skips the
    "open the Zoom app / join from your browser" page. Other links are kept."""
    m = re.match(r"(https?://[^/]*zoom\.(?:us|com))/j/(\d+)(\?.*)?$", url or "")
    return f"{m.group(1)}/wc/join/{m.group(2)}{m.group(3) or ''}" if m else url


def parse_region(text):
    try:
        x, y, w, h = (int(v) for v in text.replace(" ", "").split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("use X,Y,WIDTH,HEIGHT, e.g. 0,40,1280,640")
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("width and height must be positive")
    return {"x": x, "y": y, "width": w, "height": h}


def parse_seconds(text):
    """'60' (seconds), '90s', '10m', '1h30m' -> seconds."""
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        return parse_duration(text)


class SlideSaver:
    """Feed screenshots to the slide detector and save the kept ones as
    <prefix>_00h01m12s.png (time = seconds since capture started)."""

    def __init__(self, out_dir, prefix, threshold, verbose):
        self.out_dir, self.prefix, self.verbose = out_dir, prefix, verbose
        self.detector = SlideDetector(threshold=threshold)
        self.saved, self.last_name, self.dup = 0, None, 0

    def feed(self, png, elapsed, thumb=None):
        if not self.detector.feed(thumbnail(png) if thumb is None else thumb):
            return False
        name = f"{self.prefix}_{fmt_time(elapsed)}"
        self.dup = self.dup + 1 if name == self.last_name else 0
        self.last_name = name
        path = self.out_dir / (f"{name}_{self.dup}.png" if self.dup else f"{name}.png")
        path.write_bytes(png)
        self.saved += 1
        if self.verbose:
            print(f"[{fmt_time(elapsed)}] slide {self.saved}: {path.name}")
        return True


class Target:
    """What to screenshot: a fixed region, a CSS selector, or the largest canvas/video."""

    def __init__(self, context, selector=None, region=None):
        self.context, self.selector, self.region = context, selector, region
        self.page = pick_page(context)
        self.locator, self.desc, self.key = None, "", None
        self.first_area, self.first_share = None, False

    async def find(self):
        """(Re)locate the element. Returns True if something can be captured."""
        page = pick_page(self.context)
        if page is not self.page and page is not None:
            print(f"Watching tab: {page.url[:100]}")
        self.page = page
        if page is None:
            self.locator = None
            return False
        if self.region:
            r = self.region
            self.desc = f"region {r['width']}x{r['height']} at ({r['x']},{r['y']})"
            return True
        best, cands = None, []
        for frame in self.page.frames:
            try:
                if self.selector:
                    loc = frame.locator(self.selector)
                    if await asyncio.wait_for(loc.count(), EVAL_TIMEOUT):
                        box = await asyncio.wait_for(loc.first.bounding_box(), EVAL_TIMEOUT)
                        if box and box["width"] > 0 and box["height"] > 0:
                            best = (loc.first, f"'{self.selector}' {box['width']:.0f}x{box['height']:.0f}",
                                    (frame.url, self.selector))
                            break
                else:
                    for c in await evaluate(frame, CANDIDATES_JS):
                        cands.append((frame, *c))
            except Exception:
                continue              # frame navigating or detached
        if not self.selector and cands:
            best = self._pick(cands)
        if best is None:
            self.locator = None
            return False
        self.locator, self.desc, self.key = best[0], best[1], best[2]
        return True

    def _pick(self, cands):
        """Choose among the visible canvases/videos.

        Elements inside a "share" container win over the largest one. Once a
        share element has been captured, only share elements are accepted
        (when the share stops, the speaker video is not captured instead).
        Otherwise the element must be at least half the size of the first one.
        """
        shares = [c for c in cands if c[8]]
        if shares:
            pool = shares
        elif self.first_share:
            return None
        else:
            pool = [c for c in cands if not self.first_area or c[2] >= 0.5 * self.first_area]
        if not pool:
            return None
        frame, i, area, x, y, w, h, tag, share = max(pool, key=lambda c: c[2])
        if self.first_area is None:
            self.first_area = area
        self.first_share = self.first_share or share
        return (frame.locator(f'[data-zs="{i}"]'),
                f"<{tag}> {w}x{h} at ({x},{y})" + (" (shared screen)" if share else ""),
                (frame.url, i, w, h))

    async def debug_dump(self, path):
        """Write every tab, frame and canvas/video found, to see why nothing matched."""
        lines = []
        for n, pg in enumerate(self.context.pages, 1):
            if pg.is_closed():
                continue
            mark = "  <- watched" if pg is self.page else ""
            lines.append(f"Tab {n}: {pg.url}{mark}")
            for frame in pg.frames:
                lines.append(f"  frame: {frame.url}")
                try:
                    found = await evaluate(frame, DEBUG_JS)
                    lines += [f"    {d}" for d in found] or ["    (no canvas/video)"]
                except Exception as e:
                    lines.append(f"    (could not inspect: {type(e).__name__})")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if self.page is not None and not self.page.is_closed():
            try:
                await self.page.screenshot(path=str(path.with_suffix(".png")), timeout=5000)
            except Exception:
                pass

    async def screenshot(self):
        if self.region:
            if self.page is None or self.page.is_closed():
                return None
            return await self.page.screenshot(clip=self.region, type="png", timeout=5000)
        if self.locator is None:
            return None
        return await self.locator.screenshot(type="png", timeout=5000, animations="disabled")


async def wait_for_enter(prompt):
    """input() in a daemon thread, so Ctrl+C while waiting exits at once."""
    loop = asyncio.get_running_loop()
    done = loop.create_future()

    def read():
        try:
            input(prompt)
        except EOFError:
            pass
        loop.call_soon_threadsafe(lambda: done.done() or done.set_result(None))

    threading.Thread(target=read, daemon=True).start()
    await done


async def meeting_ended(page):
    for frame in page.frames:
        try:
            text = (await evaluate(
                frame, "() => document.body ? document.body.innerText.slice(0, 5000) : ''")).lower()
        except Exception:
            continue
        if any(p in text for p in ENDED_PHRASES):
            return True
    return False


async def capture(context, args, out_dir, stats):
    target = Target(context, args.selector, args.region)
    found = await target.find()
    if found:
        print(f"Capturing {target.desc}. Saving to {out_dir}/  (Ctrl+C to stop)")
    else:
        print("No shared screen (canvas/video) found yet; waiting for one (Ctrl+C to stop) ...")
    ever_found = found
    debug_written = False
    idle = 60 if args.idle is None else args.idle

    saver = SlideSaver(out_dir, args.prefix, args.threshold, args.verbose)
    start = stats["start"] = time.monotonic()
    k = 0
    missing_since, last_find, last_status = None, start, start
    reason = "stopped"
    while True:
        now = time.monotonic()
        elapsed = now - start
        if args.duration and elapsed >= args.duration:
            reason = f"--duration {fmt_time(args.duration)} reached"
            break
        if target.page is None or (target.page.is_closed() and pick_page(context) is None):
            reason = "window closed"
            break

        # Look again now and then: the share may start, stop or move.
        if not args.region and (target.locator is None or now - last_find >= REFIND_EVERY):
            old = target.key
            if await target.find() and target.key != old:
                print(f"[{fmt_time(elapsed)}] {'now capturing' if ever_found else 'found'} {target.desc}")
                ever_found = True
            last_find = now

        png = None
        try:
            png = await target.screenshot()
        except Exception:
            target.locator = None     # element gone (share stopped / layout changed)
        if png is None:
            missing_since = missing_since or now
            if ever_found and now - missing_since >= idle:
                reason = f"no shared screen for {idle:g} s"
                break
            if not ever_found and now - last_status >= 30:
                # Nothing found since the start: say so, and save what is on the page.
                last_status = now
                dbg = out_dir / "debug.txt"
                await target.debug_dump(dbg)
                if not debug_written:
                    print(f"[{fmt_time(elapsed)}] still no shared screen. Is someone sharing, and is the "
                          f"meeting in this Firefox window?\n  What the script sees is in {dbg} "
                          f"(and debug.png). Workaround: rerun with --region X,Y,W,H.")
                    debug_written = True
                else:
                    print(f"[{fmt_time(elapsed)}] still waiting for a shared screen ({dbg} updated)")
        else:
            if missing_since and stats["captured"] and args.verbose:
                print(f"[{fmt_time(elapsed)}] shared screen back")
            missing_since = None
            stats["captured"] = True
            if saver.feed(png, elapsed):
                stats["saved"] = saver.saved

        if (k % max(1, int(10 / args.sample)) == 0 and target.page is not None
                and not target.page.is_closed() and await meeting_ended(target.page)):
            reason = "the meeting has ended"
            break
        if args.verbose and ever_found and now - last_status >= 30:
            print(f"[{fmt_time(elapsed)}] {saver.saved} slides so far"
                  + ("" if missing_since is None else " (shared screen not visible)"))
            last_status = now

        # Fixed schedule, so a slow screenshot doesn't stretch the gap.
        k += 1
        delay = start + k * args.sample - time.monotonic()
        if delay < 0:
            k = int((time.monotonic() - start) / args.sample) + 1
            delay = start + k * args.sample - time.monotonic()
        await asyncio.sleep(delay)
    stats["reason"] = reason


# --------------------------------------------------------------------------
# --screen: screenshots of a screen area (no browser)
# --------------------------------------------------------------------------

def open_screen():
    import mss
    return (getattr(mss, "MSS", None) or mss.mss)()   # mss >= 10 renamed the class


def screen_area(sct, args):
    """The area to grab, as mss expects it."""
    mons = sct.monitors
    if args.region:
        r = args.region
        area = {"left": r["x"], "top": r["y"], "width": r["width"], "height": r["height"]}
        whole = mons[0]
        if (area["left"] < whole["left"] or area["top"] < whole["top"]
                or area["left"] + area["width"] > whole["left"] + whole["width"]
                or area["top"] + area["height"] > whole["top"] + whole["height"]):
            sys.exit(f"--region {r['x']},{r['y']},{r['width']},{r['height']} is outside the screen "
                     f"({whole['width']}x{whole['height']} at ({whole['left']},{whole['top']})). "
                     "See --list-monitors.")
        return area
    if not 0 <= args.monitor < len(mons):
        sys.exit(f"--monitor {args.monitor} doesn't exist; see --list-monitors.")
    m = mons[args.monitor]
    return {"left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}


def grab_png(sct, area):
    import mss.tools
    shot = sct.grab(area)
    return mss.tools.to_png(shot.rgb, shot.size)


def list_monitors():
    with open_screen() as sct:
        for i, m in enumerate(sct.monitors):
            what = "all monitors together" if i == 0 else f"monitor {i}"
            print(f"  --monitor {i}: {m['width']}x{m['height']} at ({m['left']},{m['top']})  {what}")
    print("--region X,Y,W,H uses the same pixel coordinates (0,0 = top-left of the whole desktop).")


def screen_warnings():
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        print("Note: this is a Wayland session; screenshots from scripts are usually black there.\n"
              "  If the test shot is black, log out and choose \"Ubuntu on Xorg\" on the login screen.")


def change_percent(a, b, pixel_delta=25):
    import numpy as np
    return 100.0 * (np.abs(a - b) > pixel_delta).mean()


def capture_screen(args, out_dir):
    """Take a screenshot of the area every --sample s and keep the new slides."""
    screen_warnings()
    stats = {"saved": 0, "start": None, "reason": "stopped"}
    try:
        with open_screen() as sct:
            area = screen_area(sct, args)
            desc = f"screen area {area['width']}x{area['height']} at ({area['left']},{area['top']})"
            if args.test_shot:
                png = grab_png(sct, area)
                path = out_dir / "test_shot.png"
                path.write_bytes(png)
                print(f"Saved {desc} to {path}. Open it and check it shows just the slides.")
                if is_blank(thumbnail(png)):
                    print("It is black/empty: is the area right, and is this an X11 session?")
                return None
            if not args.no_wait:
                print("Join the meeting the normal way (your own browser or the Zoom app), put the\n"
                      f"shared slides in the {desc},\n"
                      "and keep that area uncovered. Then come back here.")
                input("Press Enter to start capturing... ")
            print(f"Capturing {desc}. Saving to {out_dir}/  (Ctrl+C to stop)")
            saver = SlideSaver(out_dir, args.prefix, args.threshold, args.verbose)
            start = stats["start"] = time.monotonic()
            k, last_status, warned_blank = 0, start, False
            ref, last_change = None, start
            while True:
                now = time.monotonic()
                elapsed = now - start
                if args.duration and elapsed >= args.duration:
                    stats["reason"] = f"--duration {fmt_time(args.duration)} reached"
                    break
                png = grab_png(sct, area)
                thumb = thumbnail(png)
                if is_blank(thumb):
                    if not warned_blank and elapsed >= 5:
                        print(f"[{fmt_time(elapsed)}] the area is black. Is the meeting window there and "
                              "uncovered? (On Wayland, use an Xorg session.)")
                        warned_blank = True
                else:
                    warned_blank = False
                if saver.feed(png, elapsed, thumb):
                    stats["saved"] = saver.saved
                # --idle: stop when the picture hasn't changed for that long.
                if ref is None or change_percent(thumb, ref) > args.threshold:
                    ref, last_change = thumb, now
                elif args.idle and now - last_change >= args.idle:
                    stats["reason"] = f"screen unchanged for {fmt_time(args.idle)}"
                    break
                if args.verbose and now - last_status >= 30:
                    print(f"[{fmt_time(elapsed)}] {saver.saved} slides so far")
                    last_status = now
                k += 1
                delay = start + k * args.sample - time.monotonic()
                if delay < 0:
                    k = int((time.monotonic() - start) / args.sample) + 1
                    delay = start + k * args.sample - time.monotonic()
                time.sleep(delay)
    except KeyboardInterrupt:
        stats["reason"] = "Ctrl+C"
    if stats["start"] is None:
        if not args.test_shot:
            print("\nStopped before capturing started.")
        return None
    elapsed = time.monotonic() - stats["start"]
    (out_dir / DONE_MARKER).write_text("")
    print(f"\nStopped ({stats['reason']}) after {fmt_time(elapsed)}: {stats['saved']} slides in {out_dir}/")
    return stats["saved"]


async def run(args):
    from playwright.async_api import async_playwright

    out_dir = Path(args.outdir) / args.prefix
    out_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        context = await p.firefox.launch_persistent_context(
            args.profile, headless=False, firefox_user_prefs=FIREFOX_PREFS,
            viewport={"width": args.size[0], "height": args.size[1]})
        stats = {"saved": 0, "start": None, "reason": "stopped", "captured": False}
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            if args.url:
                print(f"Opening {args.url}")
                try:
                    await page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
                except Exception as e:
                    print(f"Could not open the link ({str(e).splitlines()[0]}); open it in the window yourself.")
            if not args.no_wait:
                print("Join the meeting in the Firefox window (if Zoom asks how to join, click\n"
                      "\"Join from your browser\"; enter name/passcode), make the shared screen\n"
                      "large (View > Speaker), then come back here.")
                await wait_for_enter("Press Enter to start capturing... ")
            await capture(context, args, out_dir, stats)
        except asyncio.CancelledError:
            stats["reason"] = "Ctrl+C"
        finally:
            try:
                await context.close()
            except Exception:
                pass
    if stats["start"] is None:
        print("\nStopped before capturing started.")
        return 0
    elapsed = time.monotonic() - stats["start"]
    (out_dir / DONE_MARKER).write_text("")
    print(f"\nStopped ({stats['reason']}) after {fmt_time(elapsed)}: {stats['saved']} slides in {out_dir}/")
    return stats["saved"]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", nargs="?", help="Meeting link (optional; you can also join in the window)")
    parser.add_argument("--app-page", action="store_true",
                        help="Open the link as given (with Zoom's app/browser chooser) instead of "
                             "the browser-join page")
    parser.add_argument("--prefix", default=time.strftime("Z%Y%m%d_%H%M"),
                        help="Name for the folder and images (default Z<date>_<time>)")
    parser.add_argument("--outdir", default=DEFAULT_OUTDIR, help=f"Output folder (default {DEFAULT_OUTDIR})")
    parser.add_argument("--profile", default=DEFAULT_PROFILE,
                        help="Firefox profile folder (keeps your Zoom sign-in)")
    parser.add_argument("--sample", type=float, default=1.0, help="Seconds between checked frames (default 1)")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="Slide-change sensitivity: %% of pixels that must change (default 0.5)")
    parser.add_argument("--selector", help="CSS selector of the element to capture")
    parser.add_argument("--region", type=parse_region,
                        help="Capture a fixed rectangle X,Y,WIDTH,HEIGHT (page pixels; with --screen, "
                             "screen pixels)")
    parser.add_argument("--size", type=parse_size, default=(1280, 720), help="Page size (default 1280x720)")
    parser.add_argument("--duration", type=parse_duration, help="Stop after this long, e.g. 1h30m")
    parser.add_argument("--idle", type=parse_seconds,
                        help="Browser mode: stop when the shared screen has been gone this long "
                             "(default 60 s). --screen: stop when the picture hasn't changed for "
                             "this long (default: never). Seconds, or e.g. 10m")
    scr = parser.add_argument_group("screen mode (no browser; you join Zoom yourself)")
    scr.add_argument("--screen", action="store_true",
                     help="Capture a screen area instead of driving a browser")
    scr.add_argument("--monitor", type=int, default=1,
                     help="Monitor to capture (1 = first; 0 = all together). Default 1")
    scr.add_argument("--list-monitors", action="store_true", help="Print the monitors and exit")
    scr.add_argument("--test-shot", action="store_true",
                     help="Save one screenshot of the area as test_shot.png and exit")
    parser.add_argument("--verbose", action="store_true", help="Print each saved slide and a status every 30 s")
    parser.add_argument("--no-wait", action="store_true", help="Start capturing right away (don't wait for Enter)")
    args = parser.parse_args()
    if args.sample <= 0:
        parser.error("--sample must be positive")
    if args.list_monitors or args.test_shot:
        args.screen = True
    if args.screen:
        if args.url or args.selector:
            parser.error("--screen captures the screen; it takes no meeting link or --selector "
                         "(join Zoom yourself first)")
        try:
            import mss  # noqa: F401
            import numpy  # noqa: F401
            import PIL  # noqa: F401
        except ImportError as e:
            sys.exit(f"Missing dependency ({e.name}). Run: pip install mss pillow numpy")
        if args.list_monitors:
            list_monitors()
            return
        out_dir = Path(args.outdir) / args.prefix
        out_dir.mkdir(parents=True, exist_ok=True)
        capture_screen(args, out_dir)
        return
    if args.url and not args.app_page:
        args.url = browser_join_url(args.url)
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
        import playwright  # noqa: F401
    except ImportError as e:
        sys.exit(f"Missing dependency ({e.name}). Run: pip install playwright pillow numpy && playwright install firefox")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
