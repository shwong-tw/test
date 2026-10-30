#!/usr/bin/env python3
"""
Extract session titles and URLs from saved AACR 2026 planning HTML pages.

Each session card in the HTML is an <a href="/event/aacr2026/planning/...">
containing the session title in an <h3> (falling back to the banner <img alt>,
then the small-card title <span>).
Titles are checked against the "Session Title" column (column 4) of
AACR2026_Program_Schedule.xlsx, and results are written as a tab-separated
file with "Session Title", "URL" and "Schedule Session Title" columns, the
last being the one-to-one matched xlsx title (blank when there is no match).

Usage:
    python extract_url.py [HTML ...] [--xlsx FILE] [--output FILE] [--prefix URL]
"""

import argparse
import glob
import re
import sys
import unicodedata

import openpyxl
from bs4 import BeautifulSoup

DEFAULT_PREFIX = "https://connect.aacr26.org"
DEFAULT_XLSX = "AACR2026_Program_Schedule.xlsx"
DEFAULT_OUTPUT = "AACR2026_Session_URL.txt"
MIN_PARTIAL_LEN = 15  # normalized chars needed for a substring match
PLANNING_RE = re.compile(r"/event/aacr2026/planning/")


def clean(text):
    """Collapse whitespace (HTML source wraps long titles across lines)."""
    return re.sub(r"\s+", " ", text or "").strip()


def normalize(text):
    """Loose key for matching titles: case/punctuation/accent insensitive."""
    text = unicodedata.normalize("NFKD", clean(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def extract_sessions(html_path):
    """Return a list of (title, href) in document order from one HTML file."""
    with open(html_path, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    sessions = []
    for a in soup.find_all("a", href=PLANNING_RE):
        heading = a.find("h3")
        title = clean(heading.get_text(" ")) if heading else ""
        if not title:
            img = a.find("img", alt=True)
            title = clean(img["alt"]) if img else ""
        if not title:
            # Small cards (e.g. "bookmarked" sidebar) keep the title in a span.
            span = a.find(class_=re.compile(r"planning-card-small__Title"))
            title = clean(span.get_text(" ")) if span else ""
        if title:
            sessions.append((title, a["href"]))
    return sessions


def load_schedule_titles(xlsx_path):
    """Return {normalized title: original title} from column 4 'Session Title'."""
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb.active
    titles = {}
    for row in ws.iter_rows(min_row=2, min_col=4, max_col=4, values_only=True):
        if row[0]:
            key = normalize(str(row[0]))
            if key:
                titles.setdefault(key, clean(str(row[0])))
    wb.close()
    return titles


def match_kind(key, s, min_len=MIN_PARTIAL_LEN):
    """
    Classify how normalized HTML title `key` matches normalized schedule title `s`.

    "exact"   - identical (ignoring case/punctuation/accents)
    "prefix"  - the schedule cell gained extra words in front, e.g.
                "Immunology Combating T Cell Checkpoints"
    "partial" - any other containment, e.g. a schedule cell cut short like
                "Bayesian and Model-Assisted Designs for Early-Phase"
    """
    if key == s:
        return "exact"
    if len(key) >= min_len and s.endswith(key):
        return "prefix"
    if (len(key) >= min_len and key in s) or (len(s) >= min_len and s in key):
        return "partial"
    return None


def match_titles(titles, schedule):
    """
    One-to-one match HTML titles to schedule titles.

    Returns {html title: (schedule title, kind)}. Tiers are assigned in order
    (exact, prefix, partial); within a tier the closest pairs (smallest length
    difference) win, and each schedule title is used at most once.
    """
    keys = {t: normalize(t) for t in titles}
    matches, used = {}, set()
    for tier in ("exact", "prefix", "partial"):
        pairs = sorted(
            (abs(len(s) - len(k)), t, s)
            for t, k in keys.items() if t not in matches
            for s in schedule if s not in used and match_kind(k, s) == tier
        )
        for _, t, s in pairs:
            if t not in matches and s not in used:
                matches[t] = (schedule[s], tier)
                used.add(s)
    return matches


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("html", nargs="*", help="HTML files (default: *.html)")
    parser.add_argument("--xlsx", default=DEFAULT_XLSX, help="Program schedule workbook")
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, help="Output TXT file")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="URL prefix")
    args = parser.parse_args()

    html_files = args.html or sorted(glob.glob("*.html"))
    if not html_files:
        sys.exit("No HTML files found.")

    # Collect unique sessions across files, keyed by URL (recommendation
    # carousels repeat cards that also appear in the main list).
    seen = {}
    for path in html_files:
        found = extract_sessions(path)
        new = 0
        for title, href in found:
            url = args.prefix.rstrip("/") + href
            if url not in seen:
                seen[url] = title
                new += 1
        print(f"{path}: {len(found)} cards, {new} new sessions")

    schedule = load_schedule_titles(args.xlsx)
    matches = match_titles(set(seen.values()), schedule)

    counts = {"exact": 0, "prefix": 0, "partial": 0}
    unmatched = []
    for url, title in seen.items():
        if title in matches:
            counts[matches[title][1]] += 1
        else:
            unmatched.append((title, url))

    with open(args.output, "w", encoding="utf-8") as f:
        f.write("Session Title\tURL\tSchedule Session Title\n")
        for url, title in seen.items():
            f.write(f"{title}\t{url}\t{matches.get(title, ('',))[0]}\n")

    print(f"\nWrote {len(seen)} sessions to {args.output}")
    print(f"Schedule match: {counts['exact']} exact, {counts['prefix']} prefix, "
          f"{counts['partial']} partial, {len(unmatched)} not found")
    if unmatched:
        print("WARNING: titles with no match in schedule 'Session Title' column:")
        for title, url in unmatched:
            print(f"  - {title}  ({url})")

if __name__ == "__main__":
    main()
