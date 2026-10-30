#!/usr/bin/env python3
"""
Try the slide-screenshot code from browser_download.py on one video.

By default it uses a public Vimeo video in its embedded player
(player.vimeo.com, as on the AACR pages; the vimeo.com page itself is behind
a Cloudflare check), so you can check
that playback at 2x, slide-change detection and screenshots work on your
machine before running browser_download.py on the AACR pages. It calls the
same capture_slides() function that browser_download.py uses.

Setup (once):
    pip install playwright pillow
    playwright install firefox
If the video stays black or never starts (H.264 codec missing): sudo apt install ffmpeg

Usage:
    python test_vimeo_screenshot.py                                  # 2 min of the public video
    python test_vimeo_screenshot.py --mode interval --interval 10 --seconds 60
    python test_vimeo_screenshot.py --captions                       # also try the caption download
    # One real AACR session, reusing the login saved by browser_download.py:
    python test_vimeo_screenshot.py https://connect.aacr26.org/event/aacr2026/planning/... \\
        --profile ~/.aacr26_firefox_profile --captions
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

from browser_download import (CAPTION_RE, FIREFOX_PREFS, capture_slides, click_transcript,
                              dismiss_cookie_banner, find_video_frame, fmt_time, parse_size,
                              is_cloudflare_challenge)

# The embedded player (what the AACR pages use) rather than the vimeo.com
# page, which is behind a Cloudflare check.
DEFAULT_URL = "https://player.vimeo.com/video/228314742"


def to_embed_url(url):
    """Rewrite https://vimeo.com/<id> to the embeddable player URL."""
    m = re.match(r"https?://(?:www\.)?vimeo\.com/(\d+)(?:[/?#].*)?$", url)
    return f"https://player.vimeo.com/video/{m.group(1)}" if m else url


async def close(context, args):
    """Keep the browser open until Enter, so you can see what happened."""
    if not args.headless and not args.no_wait:
        await asyncio.to_thread(input, "\nBrowser left open. Press Enter to close it... ")
    await context.close()


async def run(args):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        if args.profile:
            context = await p.firefox.launch_persistent_context(
                args.profile, headless=args.headless, firefox_user_prefs=FIREFOX_PREFS,
                viewport={"width": 1600, "height": 1000})
        else:
            browser = await p.firefox.launch(headless=args.headless, firefox_user_prefs=FIREFOX_PREFS)
            context = await browser.new_context(viewport={"width": 1600, "height": 1000})
        page = await context.new_page()

        captured = []
        page.on("response", lambda r: captured.append(r) if CAPTION_RE.search(r.url) else None)

        print(f"Opening {args.url}")
        await page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1500)
        if await is_cloudflare_challenge(page):
            print("FAIL: this page shows a Cloudflare check, which the automated browser can't pass.")
            await close(context, args)
            return 1
        if await dismiss_cookie_banner(page):
            print("Dismissed cookie banner")
        frame = await find_video_frame(page, 30)
        if frame is None:
            print("FAIL: no <video> found in the page or its frames:")
            for f in page.frames:
                print("   frame:", f.url)
            await close(context, args)
            return 1
        print(f"Video found in frame: {frame.url or '(main page)'}")

        if args.captions:
            if not captured:
                print("Transcript button clicked" if await click_transcript(page, frame)
                      else "No Transcript button found")
            for _ in range(30):
                if captured:
                    break
                await page.wait_for_timeout(500)
            if captured:
                text = await captured[0].text()
                out = Path(args.outdir) / "captions_test.txt"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(text, encoding="utf-8")
                print(f"Captions: {captured[0].url[:100]}...\n  saved {len(text)} chars to {out}; "
                      f"starts with WEBVTT: {text.lstrip(chr(0xFEFF)).startswith('WEBVTT')}")
            else:
                print("Captions: no caption (.vtt) request seen")

        print(f"Capturing {args.seconds:g} s of video at {args.speed:g}x, mode={args.mode} ...")
        stats = await capture_slides(
            page, frame, args.outdir, "test", mode=args.mode, interval=args.interval,
            threshold=args.threshold, speed=args.speed, max_seconds=args.seconds,
            size=args.size, quality=None if args.quality == "auto" else args.quality)
        size = await frame.evaluate(
            "() => { const v = document.querySelector('video');"
            " return v ? [v.videoWidth, v.videoHeight] : null; }")
        code = report(stats, size, args)
        await close(context, args)
    return code


def report(stats, size, args):
    print("\nResult")
    print(f"  status         : {stats['status']}")
    print(f"  video duration : {fmt_time(stats['duration'])}")
    print(f"  captured       : {stats['video_seconds']:.0f} s of video in {stats['wall_seconds']:.0f} s")
    print(f"  playback rate  : {stats['rate']:.2f}x (target {args.speed:g}x)")
    print(f"  video size     : {size[0]}x{size[1]}" if size else "  video size     : ?")
    print(f"  screenshots    : {stats['saved']} in {args.outdir}/")
    if stats["status"] != "DONE" or stats["saved"] == 0:
        print("Check: did the video play in the window? If it stayed black, try: sudo apt install ffmpeg")
        return 1
    if stats["rate"] < args.speed * 0.8:
        print("Note: playback ran slower than requested (buffering?); slides are still captured.")
    print("Open the PNGs and check they show the slides without player controls on top.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", nargs="?", default=DEFAULT_URL, help="Video page URL")
    parser.add_argument("--outdir", default="test_slides", help="Where to save screenshots")
    parser.add_argument("--mode", choices=["change", "interval"], default="change")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds between shots (interval mode)")
    parser.add_argument("--threshold", type=float, default=10.0, help="Slide-change sensitivity (0-255)")
    parser.add_argument("--speed", type=float, default=2.0, help="Playback speed")
    parser.add_argument("--quality", default="720p", help="Video quality to request, e.g. 720p, auto")
    parser.add_argument("--size", default="1280x720", type=parse_size, help="Page size (WIDTHxHEIGHT)")
    parser.add_argument("--seconds", type=float, default=120, help="Seconds of video to capture")
    parser.add_argument("--captions", action="store_true", help="Also try clicking Transcript and saving the .vtt")
    parser.add_argument("--profile", help="Persistent Firefox profile (for logged-in AACR pages)")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--no-wait", action="store_true", help="Close the browser without waiting for Enter")
    args = parser.parse_args()
    args.url = to_embed_url(args.url)
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
