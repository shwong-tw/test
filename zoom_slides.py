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

Setup (once, same as browser_download.py):
    pip install playwright pillow numpy
    playwright install firefox

Usage:
    python zoom_slides.py https://zoom.us/j/81234567890?pwd=abc123
    python zoom_slides.py https://zoom.us/j/81234567890 --prefix Keynote --duration 1h30m
    python zoom_slides.py https://zoom.us/j/81234567890 --region 0,40,1280,640
    python zoom_slides.py                      # open an empty window, join yourself
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
from slide_detect import SlideDetector, fmt_time, thumbnail

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


def parse_region(text):
    try:
        x, y, w, h = (int(v) for v in text.replace(" ", "").split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("use X,Y,WIDTH,HEIGHT, e.g. 0,40,1280,640")
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("width and height must be positive")
    return {"x": x, "y": y, "width": w, "height": h}


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

    detector = SlideDetector(threshold=args.threshold)
    start = stats["start"] = time.monotonic()
    k, saved, last_name, dup = 0, 0, None, 0
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
            if ever_found and now - missing_since >= args.idle:
                reason = f"no shared screen for {args.idle:g} s"
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
            if detector.feed(thumbnail(png)):
                name = f"{args.prefix}_{fmt_time(elapsed)}"
                dup = dup + 1 if name == last_name else 0
                last_name = name
                path = out_dir / (f"{name}_{dup}.png" if dup else f"{name}.png")
                path.write_bytes(png)
                saved += 1
                stats["saved"] = saved
                if args.verbose:
                    print(f"[{fmt_time(elapsed)}] slide {saved}: {path.name}")

        if (k % max(1, int(10 / args.sample)) == 0 and target.page is not None
                and not target.page.is_closed() and await meeting_ended(target.page)):
            reason = "the meeting has ended"
            break
        if args.verbose and ever_found and now - last_status >= 30:
            print(f"[{fmt_time(elapsed)}] {saved} slides so far"
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
                print("Join the meeting in the Firefox window (\"Join from your browser\"),\n"
                      "make the shared screen large (View > Speaker), then come back here.")
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
    parser.add_argument("--prefix", default=time.strftime("Z%Y%m%d_%H%M"),
                        help="Name for the folder and images (default Z<date>_<time>)")
    parser.add_argument("--outdir", default=DEFAULT_OUTDIR, help=f"Output folder (default {DEFAULT_OUTDIR})")
    parser.add_argument("--profile", default=DEFAULT_PROFILE,
                        help="Firefox profile folder (keeps your Zoom sign-in)")
    parser.add_argument("--sample", type=float, default=1.0, help="Seconds between checked frames (default 1)")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="Slide-change sensitivity: %% of pixels that must change (default 0.5)")
    parser.add_argument("--selector", help="CSS selector of the element to capture")
    parser.add_argument("--region", type=parse_region, help="Capture a fixed rectangle X,Y,WIDTH,HEIGHT")
    parser.add_argument("--size", type=parse_size, default=(1280, 720), help="Page size (default 1280x720)")
    parser.add_argument("--duration", type=parse_duration, help="Stop after this long, e.g. 1h30m")
    parser.add_argument("--idle", type=float, default=60,
                        help="Stop when the shared screen has been gone this many seconds (default 60)")
    parser.add_argument("--verbose", action="store_true", help="Print each saved slide and a status every 30 s")
    parser.add_argument("--no-wait", action="store_true", help="Start capturing right away (don't wait for Enter)")
    args = parser.parse_args()
    if args.sample <= 0:
        parser.error("--sample must be positive")
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
