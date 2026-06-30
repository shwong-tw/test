"""
Extract AACR 2026 Annual Meeting Program Guide from PDF to Excel.

This script reads the AACR2026_Program_Guide.pdf file (pages 7-90),
parses session information, and outputs an Excel file with columns:
- Date
- Time
- Session Type
- Session Title
- Talk Title
- Speaker

Requirements:
    pip install pdfplumber openpyxl pandas
"""

import re
import sys
import pdfplumber
import pandas as pd


def extract_columns_from_pdf(pdf_path, start_page=7, end_page=90):
    """Extract text from PDF using column-aware approach."""
    all_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for i in range(start_page - 1, min(end_page, len(pdf.pages))):
            page = pdf.pages[i]
            page_width = page.width

            # Split into left and right columns
            mid = page_width / 2

            left = page.crop((0, 0, mid, page.height))
            left_text = left.extract_text() or ""

            right = page.crop((mid, 0, page_width, page.height))
            right_text = right.extract_text() or ""

            # Combine: left column first, then right column
            all_text.append(left_text)
            all_text.append(right_text)

    return "\n".join(all_text)


def parse_program(text, track_untaken=False):
    """Parse the program text into structured records.

    Args:
        text: The full extracted text from the PDF.
        track_untaken: If True, also return lines that were not captured into records.

    Returns:
        If track_untaken is False: list of record dicts.
        If track_untaken is True: (list of record dicts, list of untaken line dicts).
    """
    records = []
    lines = text.split("\n")
    # Track which lines are taken (used in a record) vs untaken
    taken_lines = set()  # indices of lines incorporated into output records
    skipped_lines = set()  # indices of lines intentionally skipped (headers, chairs, etc.)
    contextual_lines = set()  # indices used as context (room, session title) but not in talk records

    # Patterns
    date_pattern = re.compile(
        r"^(FRIDAY|SATURDAY|SUNDAY|MONDAY|TUESDAY|WEDNESDAY|THURSDAY),\s+APRIL\s+\d+",
        re.IGNORECASE,
    )
    time_entry_pattern = re.compile(r"^(\d{1,2}:\d{2}\s+[ap]\.m\.)\s+(.+)")
    time_range_pattern = re.compile(
        r"^(\d{1,2}:\d{2}\s+[ap]\.m\.)\s*[\u2013\-]\s*(\d{1,2}:\d{2}\s+[ap]\.m\.)"
    )
    room_pattern = re.compile(
        r"^(Room \d+|Ballroom \d|Ballroom 6|Grand Hall|Hall [A-Z]|Halls [A-Z])",
        re.IGNORECASE,
    )

    # Session type keywords (ordered from most specific to least)
    session_type_keywords = [
        "Clinical Trials Plenary Session",
        "Clinical Trials Minisymposium",
        "Clinical Trials",
        "Educational Sessions",
        "Educational Session",
        "Minisymposia",
        "Major Symposia",
        "Major Symposium",
        "Plenary Session",
        "Awards and Lectures",
        "Special Session",
        "Professional Development Session",
        "Methods Workshops",
        "Methods Workshop",
        "Poster Sessions",
        "Advances in Diagnostics and Therapeutics",
        "Advances in Organ Site Research",
        "Advances in Population Sciences",
        "Advances in Technologies",
        "Advances in Prevention Research",
        "Advances in Early Detection and Interception",
        "Advances in the Science of Cancer Disparities",
        "Advances in Survivorship Research",
        "Advances in Hematologic Malignancies",
        "Advances in Immunotherapy",
        "Advances in Precision Oncology",
        "Advances in Cancer Research",
        "Advances in Cancer Epigenetics",
        "Advances in Therapeutic Antibodies",
        "Advances in Melanoma Research",
        "Advances in Glioblastoma Research",
        "New Drugs on the Horizon",
        "NCI-NIH-Selected Session",
        "Meet and Greet",
        "Forums",
        "Town Meetings",
    ]

    current_date = ""
    current_session_type = ""
    current_session_title = ""

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Skip empty lines, page numbers, and single-char artifacts
        if not line or re.match(r"^\d+\s*$", line) or re.match(r"^\d+\s+AACR", line):
            skipped_lines.add(i)
            i += 1
            continue
        if len(line) <= 2 and not re.match(r"\d", line):
            skipped_lines.add(i)
            i += 1
            continue

        # Check for date header
        date_match = date_pattern.match(line)
        if date_match:
            raw_date = date_match.group(0)
            parts = raw_date.split(", ")
            if len(parts) == 2:
                current_date = parts[0].capitalize() + ", " + parts[1].title()
            else:
                current_date = raw_date.title()
            contextual_lines.add(i)
            i += 1
            continue

        # Check for session type header
        clean_line = line.rstrip(" |").strip()
        clean_line = re.sub(r"\s*\(cont['\u2019]d\)", "", clean_line)

        found_session_type = False
        for stype in session_type_keywords:
            if clean_line == stype or clean_line.startswith(stype):
                current_session_type = stype
                found_session_type = True
                break
        if found_session_type:
            contextual_lines.add(i)
            i += 1
            continue

        # Check for time range (session time block header) - skip
        if time_range_pattern.match(line):
            skipped_lines.add(i)
            i += 1
            continue

        # Check for room info - session title follows
        if room_pattern.match(line):
            contextual_lines.add(i)
            i += 1
            # Skip room description continuation lines
            while i < len(lines):
                next_line = lines[i].strip()
                if next_line and (
                    "Level" in next_line
                    or "Convention Center" in next_line
                    or "Hyatt" in next_line
                ) and not time_entry_pattern.match(next_line):
                    skipped_lines.add(i)
                    i += 1
                    continue
                break

            # Collect session title lines
            title_lines = []
            title_line_indices = []
            while i < len(lines):
                next_line = lines[i].strip()
                if not next_line:
                    skipped_lines.add(i)
                    i += 1
                    break
                if (
                    time_entry_pattern.match(next_line)
                    or date_pattern.match(next_line)
                    or room_pattern.match(next_line)
                    or time_range_pattern.match(next_line)
                    or next_line.startswith("Chair:")
                    or next_line.startswith("Cochairs:")
                    or next_line.startswith("Cochair:")
                    or next_line == "NOT ELIGIBLE FOR CME CREDIT"
                ):
                    break
                temp_clean = re.sub(
                    r"\s*\(cont['\u2019]d\)", "", next_line.rstrip(" |").strip()
                )
                is_session_type = any(
                    temp_clean == s or temp_clean.startswith(s)
                    for s in session_type_keywords
                )
                if is_session_type:
                    break
                if len(next_line) > 2:
                    title_lines.append(next_line)
                    title_line_indices.append(i)
                i += 1

            if title_lines:
                current_session_title = " ".join(title_lines)
                current_session_title = re.sub(r"\s+", " ", current_session_title).strip()
                for idx in title_line_indices:
                    contextual_lines.add(idx)
            continue

        # Skip Chair/Cochair/misc lines (but don't let their content bleed into titles)
        if (
            line.startswith("Chair:")
            or line.startswith("Cochairs:")
            or line.startswith("Cochair:")
            or line.startswith("NOT ELIGIBLE")
            or line.startswith("Panelists:")
            or line.startswith("Moderator:")
            or line.startswith("Moderators:")
            or line.startswith("Section ")
        ):
            # Skip multi-line Chair/Cochair/Panelist blocks
            skipped_lines.add(i)
            i += 1
            while i < len(lines):
                next_line = lines[i].strip()
                if not next_line:
                    break
                if (
                    time_entry_pattern.match(next_line)
                    or date_pattern.match(next_line)
                    or room_pattern.match(next_line)
                    or time_range_pattern.match(next_line)
                    or next_line.startswith("NOT ELIGIBLE")
                    or next_line.startswith("Chair:")
                    or next_line.startswith("Cochairs:")
                ):
                    break
                # Continuation of names/locations list (contains commas with state codes
                # or semicolons separating multiple people)
                # Heuristic: if the line has a state/country location pattern, it's a
                # continuation of the names list
                if re.search(r",\s+[A-Z]{2}[;\s]*$", next_line) or re.search(
                    r",\s+[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\s*$", next_line
                ):
                    skipped_lines.add(i)
                    i += 1
                    continue
                # Also skip lines that look like continuation of name lists
                # (start with a name or location)
                if re.match(r"^[A-Z][a-zA-Z\.\s,;\-']+$", next_line) and (
                    ";" in next_line
                    or re.search(r",\s+[A-Z]{2}", next_line)
                    or re.search(r",\s+[A-Z][a-z]", next_line)
                ):
                    skipped_lines.add(i)
                    i += 1
                    continue
                break
            continue

        # Check for a talk entry (time + content)
        time_match = time_entry_pattern.match(line)
        if time_match:
            talk_time = time_match.group(1)
            talk_content = time_match.group(2)
            talk_line_indices = [i]

            # Collect continuation lines
            i += 1
            while i < len(lines):
                next_line = lines[i].strip()
                if not next_line:
                    break
                if (
                    time_entry_pattern.match(next_line)
                    or date_pattern.match(next_line)
                    or room_pattern.match(next_line)
                    or time_range_pattern.match(next_line)
                    or next_line.startswith("Chair:")
                    or next_line.startswith("Cochairs:")
                    or next_line.startswith("NOT ELIGIBLE")
                    or next_line.startswith("Panelists:")
                    or next_line.startswith("Moderator:")
                    or next_line.startswith("Moderators:")
                    or next_line.startswith("Section ")
                ):
                    break
                temp_clean = re.sub(
                    r"\s*\(cont['\u2019]d\)", "", next_line.rstrip(" |").strip()
                )
                is_session_type = any(
                    temp_clean == s or temp_clean.startswith(s)
                    for s in session_type_keywords
                )
                if is_session_type:
                    break
                if len(next_line) <= 2 and not re.match(r"\d", next_line):
                    skipped_lines.add(i)
                    i += 1
                    continue
                talk_content += " " + next_line
                talk_line_indices.append(i)
                i += 1

            # Clean talk content
            talk_content = re.sub(r"\s+", " ", talk_content).strip()

            # Parse talk title and speaker
            talk_title, speaker = parse_talk_and_speaker(talk_content)

            if talk_title and current_date:
                records.append(
                    {
                        "Date": current_date,
                        "Time": talk_time,
                        "Session Type": current_session_type,
                        "Session Title": current_session_title,
                        "Talk Title": talk_title,
                        "Speaker": speaker,
                    }
                )
                for idx in talk_line_indices:
                    taken_lines.add(idx)
            continue

        # Check if line might be a session title (standalone, followed by Chair: or time)
        j = i + 1
        potential_title_lines = [line]
        while j < len(lines) and lines[j].strip():
            next_l = lines[j].strip()
            if (
                next_l.startswith("Chair:")
                or next_l.startswith("Cochairs:")
                or next_l.startswith("Cochair:")
                or time_entry_pattern.match(next_l)
                or next_l.startswith("NOT ELIGIBLE")
            ):
                current_session_title = " ".join(potential_title_lines)
                current_session_title = re.sub(r"\s+", " ", current_session_title).strip()
                break
            if (
                date_pattern.match(next_l)
                or room_pattern.match(next_l)
                or time_range_pattern.match(next_l)
            ):
                break
            temp_clean = re.sub(r"\s*\(cont['\u2019]d\)", "", next_l.rstrip(" |").strip())
            is_stype = any(
                temp_clean == s or temp_clean.startswith(s)
                for s in session_type_keywords
            )
            if is_stype:
                break
            if len(next_l) > 2:
                potential_title_lines.append(next_l)
            j += 1

        i += 1

    if not track_untaken:
        return records

    # Build untaken text report
    all_classified = taken_lines | skipped_lines | contextual_lines
    untaken = []
    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if idx not in all_classified and stripped:
            untaken.append({"line_number": idx + 1, "text": stripped})

    return records, untaken


def parse_talk_and_speaker(content):
    """
    Parse a talk entry into title and speaker.

    Format: "Talk title. Speaker Name, City, State/Country"
    Sometimes with abstract number prefix: "CT004 Title. Speaker, Location"
    """
    # The speaker is always at the end in the format: "FirstName [M.] LastName, City, STATE"
    # or "FirstName [M.] LastName, City, Country"
    # Key insight: the speaker section ends with a US state (2 caps) or country/city name
    # and the speaker name+location has commas separating name, city, state

    # Match speaker at end: Name, City, STATE or Name, City, Country
    # US states: 2 uppercase letters
    # Countries/other: capitalized words
    # Pattern: "Name Name, City, ST" or "Name Name, City, ST, Country"
    # or "Name Name, City, Country"

    # Try to find the speaker by looking for the location pattern at the end
    # then working backwards to find where the speaker name starts (after ". ")

    # Match location at end: ", City, STATE" or ", City, Country"
    location_end = re.search(
        r",\s+[A-Z][a-zA-Z\s\-\.]+,\s+(?:[A-Z]{2}(?:,\s+[A-Z][a-zA-Z\s]+)?|"
        r"[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)*)\s*$",
        content,
    )

    if location_end:
        # Find where speaker name starts - look for ". " before the location
        # The speaker name starts after the last ". " that precedes this location
        loc_start = location_end.start()

        # Search backwards from location for the start of the speaker name
        # Speaker name starts after ". " - find the correct period
        # The name is typically: "FirstName [MiddleInit.] LastName"
        # So we need to find the ". " that is NOT part of a middle initial

        search_region = content[:loc_start]
        period_positions = [m.start() for m in re.finditer(r"\.\s", search_region)]

        best_split = -1
        for pos in reversed(period_positions):
            # Check if this period is part of a middle initial (single letter before it)
            # Middle initial pattern: " X. " where X is a single uppercase letter
            if (
                pos >= 2
                and content[pos - 1].isupper()
                and content[pos - 2] in " ."
            ):
                continue
            # Check: text after this ". " up to end should be a valid speaker string
            candidate_speaker = content[pos + 2:].strip()
            # A valid speaker has: name part, then comma, then location
            # Name part should not be too long and should be mostly proper nouns
            comma_pos = candidate_speaker.find(",")
            if comma_pos > 0 and comma_pos < 80:
                name_part = candidate_speaker[:comma_pos].strip()
                # Name should be capitalized words (allow periods for initials)
                if re.match(r"^[A-Z][a-zA-Z\.\s\-\']+$", name_part):
                    best_split = pos
                    break

        if best_split >= 0:
            talk_title = content[:best_split].strip()
            speaker = content[best_split + 2:].strip()
            return talk_title, speaker

    # Fallback: simple last-period split
    period_positions = [m.start() for m in re.finditer(r"\.\s", content)]
    for pos in reversed(period_positions):
        # Skip middle initials
        if (
            pos >= 2
            and content[pos - 1].isupper()
            and content[pos - 2] in " ."
        ):
            continue
        remaining = content[pos + 2:].strip()
        if "," in remaining and remaining[0].isupper() and len(remaining) < 100:
            return content[:pos].strip(), remaining

    return content, ""


def clean_talk_title(title):
    """Clean up common artifacts in talk titles."""
    # Fix PDF extraction artifact: space after first capital letter
    title = re.sub(r"^([A-Z])\s([a-z])", r"\1\2", title)
    # Fix unicode
    title = title.replace("\u203a", "'").replace("\u2019", "'").replace("\u2018", "'")
    return title.strip()


def main():
    pdf_path = "AACR2026_Program_Guide.pdf"
    output_path = "AACR2026_Program_Schedule.xlsx"
    show_untaken = "--show-untaken" in sys.argv

    print("Extracting text from PDF...")
    text = extract_columns_from_pdf(pdf_path, start_page=7, end_page=90)

    print("Parsing program schedule...")
    if show_untaken:
        records, untaken = parse_program(text, track_untaken=True)
    else:
        records = parse_program(text)

    print(f"Found {len(records)} entries")

    # Create DataFrame
    df = pd.DataFrame(
        records,
        columns=["Date", "Time", "Session Type", "Session Title", "Talk Title", "Speaker"],
    )

    # Clean up
    df["Talk Title"] = df["Talk Title"].apply(clean_talk_title)
    df["Speaker"] = df["Speaker"].str.replace("\u203a", "'", regex=False)
    df["Speaker"] = df["Speaker"].str.replace("\u2019", "'", regex=False)
    # Remove page footer artifacts from speaker field
    df["Speaker"] = df["Speaker"].str.replace(
        r"\s*AACR ANNUAL MEETING 2026 PROGRAM GUIDE\s*", "", regex=True
    )
    # Remove trailing page numbers
    df["Speaker"] = df["Speaker"].str.replace(r"\s+\d+\s*$", "", regex=True)

    # Save to Excel
    print(f"Saving to {output_path}...")
    df.to_excel(output_path, index=False, engine="openpyxl")
    print(f"Done! Saved {len(df)} records to {output_path}")

    # Print summary
    print("\n--- Summary ---")
    print(f"Total entries: {len(df)}")
    print(f"\nDates found:")
    for date in df["Date"].unique():
        count = len(df[df["Date"] == date])
        print(f"  {date}: {count} entries")
    print(f"\nSession Types found:")
    for stype in sorted(df["Session Type"].unique()):
        count = len(df[df["Session Type"] == stype])
        print(f"  {stype}: {count} entries")

    # Print sample records
    print("\n--- Sample Records (first 15) ---")
    for idx, row in df.head(15).iterrows():
        print(f"\n  [{idx}] Date: {row['Date']}")
        print(f"       Time: {row['Time']}")
        print(f"       Session Type: {row['Session Type']}")
        print(f"       Session Title: {row['Session Title']}")
        print(f"       Talk Title: {row['Talk Title']}")
        print(f"       Speaker: {row['Speaker']}")

    # Untaken text report
    if show_untaken:
        untaken_path = "untaken_text.txt"
        total_lines = len(text.split("\n"))
        non_empty_lines = len([l for l in text.split("\n") if l.strip()])

        print(f"\n--- Untaken Text Report ---")
        print(f"Total lines in PDF text: {total_lines}")
        print(f"Non-empty lines: {non_empty_lines}")
        print(f"Untaken lines (not captured into records or recognized as structure): {len(untaken)}")
        if non_empty_lines > 0:
            pct = (len(untaken) / non_empty_lines) * 100
            print(f"Untaken percentage: {pct:.1f}% of non-empty lines")

        with open(untaken_path, "w", encoding="utf-8") as f:
            f.write("UNTAKEN TEXT REPORT\n")
            f.write("=" * 60 + "\n")
            f.write(f"Total lines: {total_lines}\n")
            f.write(f"Non-empty lines: {non_empty_lines}\n")
            f.write(f"Untaken lines: {len(untaken)}\n")
            if non_empty_lines > 0:
                f.write(f"Untaken percentage: {pct:.1f}%\n")
            f.write("=" * 60 + "\n\n")
            f.write("Each line below was NOT captured into any output record.\n")
            f.write("Review these to check for missing information.\n\n")
            f.write("-" * 60 + "\n")
            for item in untaken:
                f.write(f"Line {item['line_number']:>5}: {item['text']}\n")

        print(f"\nUntaken text saved to: {untaken_path}")
        print("Review this file to identify any missing information from the PDF.")


if __name__ == "__main__":
    main()
