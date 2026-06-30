# AACR 2026 Program Guide Extractor

Extracts session and talk information from the AACR 2026 Annual Meeting Program Guide PDF into a structured Excel spreadsheet.

## Requirements

```bash
pip install pdfplumber openpyxl pandas
```

## Usage

### Basic extraction
```bash
python extract_program.py
```
Outputs `AACR2026_Program_Schedule.xlsx` with columns: Date, Time, Session Type, Session Title, Talk Title, Speaker.

### Show untaken text (debug missing information)
```bash
python extract_program.py --show-untaken
```
Generates `untaken_text.txt` listing all PDF lines that were not captured into any output record. Use this to verify no information is missing from the extraction.

## Documentation

See [TUTORIAL.md](TUTORIAL.md) for a detailed explanation of the script logic, parsing strategy, and debugging tips.
