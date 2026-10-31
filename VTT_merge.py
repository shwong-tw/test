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

After writing, the output is checked against the transcript (re-scanned
independently): every caption line must appear once, in order. Problems are
listed and the script exits with status 1.

--start / --end (e.g. 01h00m00s) restrict the work to a time window: only
cues starting in [start, end) and the slide intervals inside it. Blocks keep
the numbers they have in a full run, so outputs can be compared.

Usage:
    python VTT_merge.py S001.txt S001_Interval.txt
    python VTT_merge.py S001.txt S001_Interval.txt --start 01h00m00s --end 01h30m00s
    python VTT_merge.py S001.txt output/slides/S001 --time -o S001_slides.txt
"""

import argparse
import bisect
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


def parse_hms(text):
    """'01h00m00s' (also '1h30m', '45m', '90s') or '01:00:00' / '30:00' -> seconds."""
    t = text.strip().lower()
    if ":" in t:
        return vtt_seconds(t)
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", t)
    if not parts or "".join(n + u for n, u in parts) != t.replace(" ", ""):
        raise argparse.ArgumentTypeError(f"invalid time '{text}' (use e.g. 01h00m00s or 01:00:00)")
    return sum(float(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in parts)


def in_window(t, start, end):
    return t >= start and (end is None or t < end)


def merge(cues, times, show_time=False, start=0.0, end=None):
    """
    Merged text, one block per slide interval. With `start`/`end`, only cues
    starting in [start, end) and the slide intervals inside that window are
    included; blocks keep the numbers they have in a full run.
    """
    vtt_end = max((c[1] for c in cues), default=0.0)
    full = slide_intervals(times, vtt_end)
    full_starts = [a for a, _ in full]
    if start <= 0 and end is None:
        intervals = full
    else:
        bounds = [start] + [float(t) for t in times if start < t and (end is None or t < end)]
        last = end if end is not None else max(vtt_end, bounds[-1])
        intervals = list(zip(bounds, bounds[1:] + [last]))
    numbers = [bisect.bisect_right(full_starts, a) for a, _ in intervals]   # 1-based

    cues = [c for c in cues if in_window(c[0], start, end)]
    texts = [[] for _ in intervals]
    for cue, idx in zip(cues, assign(cues, intervals)):
        texts[idx].extend(cue[2])
    width = max(2, len(str(len(full))))
    blocks = []
    for n, (a, b), lines in zip(numbers, intervals, texts):
        block = [f"{n:0{width}d}"]
        if show_time:
            block.append(f"{fmt_hms(a)} --> {fmt_hms(b)}")
        block += lines
        blocks.append("\n".join(block))
    return "\n\n".join(blocks) + "\n", len(intervals), len(cues)


LOOSE_TIMING_RE = re.compile(r"\d+:\d+\S*\s*-+>|-->")


def raw_caption_lines(path):
    """
    Independent re-scan of the WEBVTT file for the check. Every non-empty
    line counts as caption text except the WEBVTT header, NOTE/STYLE/REGION
    blocks, cue numbers and timing lines -- so text is counted even when its
    timing line is broken and the cue could not be read.
    Returns (number of timing lines, [(cue start or None, text line)]).
    """
    text = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    timings, lines = 0, []
    for block in re.split(r"\n\s*\n", text):
        rows = [r.strip() for r in block.split("\n") if r.strip()]
        if not rows or re.match(r"(WEBVTT|NOTE|STYLE|REGION)\b", rows[0]):
            continue
        start = None
        for i, row in enumerate(rows):
            if LOOSE_TIMING_RE.search(row):
                timings += 1
                m = CUE_TIME_RE.match(row)
                if m:
                    start = vtt_seconds(m.group(1))
                else:
                    lines.append((None, f"(unreadable timing line: {row})"))
                rows = rows[i + 1:]
                break
        else:
            if re.fullmatch(r"\d+", rows[0]) and len(rows) > 1:
                rows = rows[1:]       # cue number of a cue whose timing is missing
        for row in rows:
            row = TAG_RE.sub("", row).strip()
            if row:
                lines.append((start, row))
    return timings, lines


def output_text_lines(path):
    """Text lines of a merged file (block numbers and time ranges removed)."""
    lines = []
    for block in Path(path).read_text(encoding="utf-8").split("\n\n"):
        rows = [r for r in block.split("\n") if r.strip()]
        if rows and re.fullmatch(r"\d+", rows[0].strip()):
            rows = rows[1:]
        if rows and re.fullmatch(r"\d{2}:\d{2}:\d{2} --> \d{2}:\d{2}:\d{2}", rows[0].strip()):
            rows = rows[1:]
        lines += [r.strip() for r in rows]
    return lines


def check_merged(vtt_path, out_path, parsed_cues, start=0.0, end=None):
    """
    Compare the merged file with the original transcript (only cues starting
    in [start, end)). Returns a list of problems (empty if every caption line
    is present once, in order).
    """
    from collections import Counter
    problems = []
    timings, expected = raw_caption_lines(vtt_path)
    if timings != len(parsed_cues):
        problems.append(f"{timings} timing lines in the transcript but {len(parsed_cues)} cues were read")
    for _, text in expected:
        if text.startswith("(unreadable timing line: "):
            problems.append(f"unreadable timing line, cue skipped: {text[25:-1]}")
    expected = [(t, text) for t, text in expected if not text.startswith("(unreadable timing line: ")
                and (t is None or in_window(t, start, end))]
    got = output_text_lines(out_path)
    want = [text for _, text in expected]
    missing = Counter(want) - Counter(got)
    extra = Counter(got) - Counter(want)
    for start, text in expected:
        if missing[text] > 0:
            missing[text] -= 1
            when = fmt_hms(start) if start is not None else "?"
            problems.append(f"missing line (cue at {when}): {text}")
    for text, n in extra.items():
        problems.append(f"line not in the transcript{f' (x{n})' if n > 1 else ''}: {text}")
    if not problems and got != want:
        problems.append("all lines are present but their order differs from the transcript")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vtt", help="Transcript in WEBVTT format (e.g. S001.txt)")
    parser.add_argument("slides", help="Slide time list (e.g. S001_Interval.txt) or screenshot folder")
    parser.add_argument("-o", "--output", help="Output file (default: <transcript name>_merged.txt)")
    parser.add_argument("--time", action="store_true", help="Print each slide interval's time range")
    parser.add_argument("--start", type=parse_hms, default=0.0, metavar="01h00m00s",
                        help="Only process captions/slides from this time on")
    parser.add_argument("--end", type=parse_hms, metavar="01h30m00s",
                        help="Only process captions/slides before this time")
    args = parser.parse_args()
    if args.end is not None and args.end <= args.start:
        parser.error("--end must be later than --start")

    cues = read_vtt(args.vtt)
    if not cues:
        sys.exit(f"{args.vtt}: no caption cues found")
    times = read_slide_times(args.slides)
    out = Path(args.output) if args.output else Path(args.vtt).with_name(Path(args.vtt).stem + "_merged.txt")
    text, n_intervals, n_cues = merge(cues, times, args.time, args.start, args.end)
    out.write_text(text, encoding="utf-8")
    window = ""
    if args.start > 0 or args.end is not None:
        window = (f" (window {fmt_hms(args.start)} - "
                  f"{fmt_hms(args.end) if args.end is not None else 'end'})")
    print(f"{out}: {n_cues} cues -> {n_intervals} slide intervals{window}")

    problems = check_merged(args.vtt, out, cues, args.start, args.end)
    if problems:
        print(f"Check FAILED: {len(problems)} problem(s)")
        for p in problems[:50]:
            print(f"  - {p}")
        if len(problems) > 50:
            print(f"  ... and {len(problems) - 50} more")
        sys.exit(1)
    n_lines = len(output_text_lines(out))
    print(f"Check OK: all {n_lines} caption lines are in the output, once each, in order")


if __name__ == "__main__":
    main()
