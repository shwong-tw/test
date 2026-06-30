# AACR 2026 Program Guide Extraction Script – Tutorial

This tutorial explains the logic of `extract_program.py` step-by-step so you can understand, debug, and extend the script easily.

---

## Table of Contents

1. [Overview](#overview)
2. [Pipeline Summary](#pipeline-summary)
3. [Step 1: PDF Text Extraction (`extract_columns_from_pdf`)](#step-1-pdf-text-extraction)
4. [Step 2: Parsing the Program (`parse_program`)](#step-2-parsing-the-program)
5. [Step 3: Parsing Talk Entries (`parse_talk_and_speaker`)](#step-3-parsing-talk-entries)
6. [Step 4: Cleaning and Output (`main`)](#step-4-cleaning-and-output)
7. [Debugging Tips](#debugging-tips)
8. [Untaken Text Feature](#untaken-text-feature)

---

## Overview

The script reads the AACR 2026 Annual Meeting Program Guide PDF, extracts session and talk information, and outputs a structured Excel file with columns: Date, Time, Session Type, Session Title, Talk Title, and Speaker.

---

## Pipeline Summary

```
PDF File
  │
  ▼
extract_columns_from_pdf()   ← Reads pages 7-90, splits each page into left/right columns
  │
  ▼
Raw text (one long string, columns concatenated in reading order)
  │
  ▼
parse_program()              ← Line-by-line state machine that identifies structure
  │
  ▼
List of record dicts [{Date, Time, Session Type, Session Title, Talk Title, Speaker}, ...]
  │
  ▼
main()                       ← Cleans data, writes to Excel, prints summary
```

---

## Step 1: PDF Text Extraction

**Function:** `extract_columns_from_pdf(pdf_path, start_page=7, end_page=90)`

**What it does:**
- Opens the PDF and iterates over pages 7 through 90 (the actual program content; earlier pages are table of contents, etc.).
- For each page, it splits the page into **left and right halves** at the midpoint (`page_width / 2`).
- Extracts text from the left column first, then the right column.
- Joins all text blocks with newlines.

**Why column splitting?**
The PDF is formatted in a two-column layout. Without splitting, `pdfplumber` would extract text in a mixed order (jumping between columns). By explicitly cropping left and right halves, we get text in natural reading order.

**Debugging this step:**
- If text is garbled or out of order, the column midpoint may be wrong for certain pages.
- Print text for a specific page to verify: add `print(f"Page {i+1}:\n{left_text}\n---\n{right_text}")`.
- Some pages may not have two columns (e.g., full-width headers). These may lose text at column boundaries.

---

## Step 2: Parsing the Program

**Function:** `parse_program(text)`

**What it does:**
This is a **state machine** that processes the extracted text line-by-line. It maintains three state variables:
- `current_date` – the day being processed (e.g., "Friday, April 25")
- `current_session_type` – the category (e.g., "Minisymposia", "Plenary Session")
- `current_session_title` – the specific session name

### Line Classification (in priority order):

| Priority | Pattern | Action |
|----------|---------|--------|
| 1 | Empty / page numbers / short artifacts | **Skip** |
| 2 | Day header (`FRIDAY, APRIL 25`) | Update `current_date` |
| 3 | Session type keyword match | Update `current_session_type` |
| 4 | Time range (`9:00 a.m. – 12:00 p.m.`) | **Skip** (block header) |
| 5 | Room pattern (`Room 301`, `Ballroom 6`) | Read session title from following lines |
| 6 | Chair/Cochair/Panelists/Moderator lines | **Skip** (with continuation) |
| 7 | Time + content (`9:00 a.m. Talk title...`) | **Record a talk entry** |
| 8 | Unmatched lines | Attempt to interpret as session title |

### Detailed Logic for Each Case:

#### Skip Lines (Priority 1)
```
- Empty lines
- Lines matching "^\d+\s*$" (standalone page numbers)
- Lines matching "^\d+\s+AACR" (page footer like "42 AACR ANNUAL MEETING...")
- Lines ≤ 2 characters that aren't digits (PDF artifacts)
```

#### Date Headers (Priority 2)
Matches patterns like `FRIDAY, APRIL 25`. Capitalizes and stores as `current_date`.

#### Session Type (Priority 3)
The script maintains a list of known session type keywords (ordered from most specific to least specific to avoid partial matches). If a line matches one of these keywords, it updates `current_session_type`.

**Important:** Lines are cleaned before matching:
- Trailing ` |` is stripped (PDF artifact)
- `(cont'd)` suffixes are removed

#### Time Ranges (Priority 4)
Lines like `9:00 a.m. – 12:00 p.m.` are session time block headers. They are skipped because the individual talk times are what matter.

#### Room Patterns (Priority 5)
When a room is detected:
1. Skip continuation lines containing location details ("Level", "Convention Center", "Hyatt")
2. Collect subsequent non-empty lines as the **session title** until hitting a boundary (time entry, date, another room, Chair:, etc.)

#### Chair/Cochair/Moderator Lines (Priority 6)
These lines and their continuations are skipped. Continuation detection uses heuristics:
- Lines ending with state codes (`, CA;`)
- Lines matching name-list patterns (capitalized words with commas/semicolons)

#### Talk Entries (Priority 7)
This is the **main output generator**. When a line starts with a time followed by content:
1. Extract the time and initial content
2. Collect continuation lines (same boundary rules as above)
3. Pass combined content to `parse_talk_and_speaker()`
4. Create a record with all current state variables

#### Fallback Title Detection (Priority 8)
If none of the above match, the script looks ahead to see if subsequent lines start with `Chair:` or a time entry. If so, the current unmatched lines are treated as a session title.

---

## Step 3: Parsing Talk Entries

**Function:** `parse_talk_and_speaker(content)`

**What it does:**
Splits a talk entry string (e.g., `"CT004 Novel therapy for lung cancer. John Smith, Boston, MA"`) into a title and speaker.

### Strategy:
1. **Find the location pattern at the end** – looks for `, City, STATE` or `, City, Country` at the end of the string.
2. **Work backwards to find the period** that separates the title from the speaker name – skips periods that are part of middle initials (e.g., `J. Smith`).
3. **Validate the speaker candidate** – the text between the period and the location should look like a proper name (capitalized, not too long).

### Fallback:
If the location-based approach fails, it tries a simpler last-period split: finds the last `. ` where the remaining text contains a comma, starts with an uppercase letter, and is under 100 characters.

**Common issues:**
- Titles containing periods (e.g., abbreviations like "U.S.") may cause incorrect splits.
- Speakers with unusual name formats may not match the regex.
- Missing location info means no speaker is extracted (returns empty string).

---

## Step 4: Cleaning and Output

**Function:** `main()`

**Post-processing applied to the DataFrame:**
1. `clean_talk_title()` – fixes PDF artifacts like extra spaces after capital letters, unicode quote characters.
2. Speaker field – replaces unicode quotes, removes page footer artifacts (`AACR ANNUAL MEETING 2026 PROGRAM GUIDE`), removes trailing page numbers.

**Output:**
- Writes to `AACR2026_Program_Schedule.xlsx`
- Prints summary: total entries, entries per date, entries per session type
- Prints first 15 sample records for quick verification

---

## Debugging Tips

### 1. Verify PDF text extraction
Add this after `extract_columns_from_pdf()`:
```python
with open("/tmp/raw_text.txt", "w") as f:
    f.write(text)
```
Then inspect the raw text to see if the PDF content is being read correctly.

### 2. Trace the parser state
Add print statements inside the `while` loop in `parse_program()`:
```python
print(f"Line {i}: [{line[:60]}] → date={current_date}, type={current_session_type}")
```

### 3. Check for missed content
Use the **untaken text feature** (see below) by running:
```bash
python extract_program.py --show-untaken
```
This outputs all lines that were not captured into any record, helping you find missing information.

### 4. Inspect specific date/session
Filter the output DataFrame:
```python
df[df["Date"] == "Friday, April 25"]
df[df["Session Type"] == "Minisymposia"]
```

### 5. Test the talk parser in isolation
```python
result = parse_talk_and_speaker("CT004 My talk title. Jane Doe, San Francisco, CA")
print(result)  # ("CT004 My talk title", "Jane Doe, San Francisco, CA")
```

---

## Untaken Text Feature

The script includes a `--show-untaken` mode that reports all PDF text lines that were **not** incorporated into any output record. This helps identify:
- Missing talks or sessions
- Unrecognized formatting patterns
- Content that fell through the parser's logic

### Usage:
```bash
python extract_program.py --show-untaken
```

This will:
1. Run the normal extraction
2. Generate an `untaken_text.txt` file listing every line that was skipped or not captured
3. Print a summary of how much text was taken vs. untaken

### Interpreting the output:
- **Expected untaken lines:** Page headers/footers, room descriptions, chair names, time ranges, blank lines
- **Unexpected untaken lines:** If you see talk-like content (times followed by descriptions) in the untaken output, the parser is missing entries

See the implementation in `extract_program.py` for details.
