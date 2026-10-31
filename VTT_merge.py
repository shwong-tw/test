#!/usr/bin/env python3
"""
Regroup a WEBVTT transcript by slide.

Inputs:
  - a transcript (Sxxx.txt, WEBVTT format, as saved by browser_download.py)
  - slide times: a list file (Sxxx_Interval.txt, one time per line such as
    00h00m03s) or a screenshot folder (Sxxx/ with Sxxx_00h00m03s.png files)

Slide times t1 < t2 < ... < tn give numbered slide intervals
    01: 0 - t1,  02: t1 - t2,  ...,  n+1: tn - end of the transcript
(an empty 0 - 0 interval is skipped when t1 is 00h00m00s).

Each caption cue is assigned to the slide interval it overlaps most; on a
tie it goes to the earlier interval. The cues' text is merged per interval:

    01
    00:00:00 --> 00:00:03      (only with --time)
    Great. Welcome everybody.

    02
    ...

Intervals without speech are kept (empty text), so the numbers always match
the slide intervals.

Usage:
    python VTT_merge.py S001.txt S001_Interval.txt
    python VTT_merge.py S001.txt output/slides/S001 --time -o S001_slides.txt
"""

import argparse
import re
import sys
from pathlib import Path

CUE_TIME_RE = re.compile(
    r"^\s*((?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d+)?)\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d+)?)")
SLIDE_TIME_RE = re.compile(r"(\d+)h(\d{1,2})m(\d{1,2})s")
TAG_RE = re.compile(r"<[^>]+>")


def vtt_seconds(text):
    """'01:02:03.500', '02:03.500' or '02:03,500' -> seconds."""
    parts = text.replace(",", ".").split(":")
    seconds = float(parts[-1])
    for i, p in enumerate(reversed(parts[:-1]), 1):
        seconds += int(p) * 60 ** i
    return seconds


def fmt_hms(seconds):
    s = int(seconds + 0.5)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def read_vtt(path):
    """Return [(start, end, [text lines])] from a WEBVTT file."""
    text = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip("\n").split("\n")
        for i, line in enumerate(lines):
            m = CUE_TIME_RE.match(line)
            if m:
                body = [TAG_RE.sub("", ln).strip() for ln in lines[i + 1:]]
                body = [ln for ln in body if ln]
                cues.append((vtt_seconds(m.group(1)), vtt_seconds(m.group(2)), body))
                break
        # Blocks without a timing line (WEBVTT header, NOTE, STYLE) are skipped.
    return sorted(cues, key=lambda c: c[0])


def read_slide_times(path):
    """Slide times in seconds from an interval list file or a screenshot folder."""
    path = Path(path)
    if path.is_dir():
        sources = [p.name for p in sorted(path.glob("*.png"))]
    else:
        sources = [ln.split("#", 1)[0] for ln in path.read_text(encoding="utf-8-sig").splitlines()]
    times = set()
    for src in sources:
        m = SLIDE_TIME_RE.search(src)
        if m:
            h, mi, s = map(int, m.groups())
            times.add(h * 3600 + mi * 60 + s)
        elif src.strip():
            print(f"{path}: ignoring '{src.strip()}' (no time like 00h00m03s)", file=sys.stderr)
    return sorted(times)


def slide_intervals(times, end):
    """[(start, end)] for 0-t1, t1-t2, ..., tn-end."""
    bounds = [0.0] + [float(t) for t in times if t > 0]
    end = max(end, bounds[-1])
    return list(zip(bounds, bounds[1:] + [end]))


def assign(cues, intervals):
    """Index of the interval each cue overlaps most (ties -> the earlier one)."""
    result = []
    for start, end, _ in cues:
        best, best_overlap = None, -1.0
        for i, (a, b) in enumerate(intervals):
            if a > end:
                break
            overlap = min(end, b) - max(start, a)
            if overlap > best_overlap:
                best, best_overlap = i, overlap
        if best_overlap <= 0:
            # Zero-length cue (or touching only a boundary): use the interval
            # its start falls in.
            best = next((i for i, (a, b) in enumerate(intervals) if a <= start < b),
                        len(intervals) - 1)
        result.append(best)
    return result


def merge(cues, times, show_time=False):
    end = max((c[1] for c in cues), default=0.0)
    intervals = slide_intervals(times, end)
    texts = [[] for _ in intervals]
    for cue, idx in zip(cues, assign(cues, intervals)):
        texts[idx].extend(cue[2])
    width = max(2, len(str(len(intervals))))
    blocks = []
    for n, ((a, b), lines) in enumerate(zip(intervals, texts), 1):
        block = [f"{n:0{width}d}"]
        if show_time:
            block.append(f"{fmt_hms(a)} --> {fmt_hms(b)}")
        block += lines
        blocks.append("\n".join(block))
    return "\n\n".join(blocks) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vtt", help="Transcript in WEBVTT format (e.g. S001.txt)")
    parser.add_argument("slides", help="Slide time list (e.g. S001_Interval.txt) or screenshot folder")
    parser.add_argument("-o", "--output", help="Output file (default: <transcript name>_merged.txt)")
    parser.add_argument("--time", action="store_true", help="Print each slide interval's time range")
    args = parser.parse_args()

    cues = read_vtt(args.vtt)
    if not cues:
        sys.exit(f"{args.vtt}: no caption cues found")
    times = read_slide_times(args.slides)
    out = Path(args.output) if args.output else Path(args.vtt).with_name(Path(args.vtt).stem + "_merged.txt")
    out.write_text(merge(cues, times, args.time), encoding="utf-8")
    print(f"{out}: {len(cues)} cues -> {len(slide_intervals(times, max(c[1] for c in cues)))} slide intervals")


if __name__ == "__main__":
    main()
