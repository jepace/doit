#!/usr/bin/env python3
"""
Import tasks from a Toodledo CSV export into doit.

Usage:
  python3 src/import_toodledo.py --user <email> --file export.csv [--dry-run]

Fields mapped:
  TASK        → description
  FOLDER      → #ctx: (Toodledo's real grouping; CONTEXT is usually blank)
  DUEDATE     → #due:
  STARTDATE   → #start:
  PRIORITY    → #p: (3=top 2=high 1=medium 0=low)
  REPEAT      → #rep:
  STAR        → #star
  NOTE        → task note lines

Completed tasks (STATUS == "Checked") are skipped.
Tasks with no due date and no start date get no date tags.
All imported tasks land in ## Inbox.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from task_manager import read_tasks, write_tasks, get_tasks_file, _new_id, DATA_DIR
from user_store import UserStore

# ---------------------------------------------------------------------------
# Field mappings
# ---------------------------------------------------------------------------

PRIORITY_MAP = {
    "3": "top",
    "2": "high",
    "1": "medium",
    "0": "low",
}

# Map Toodledo repeat strings to doit #rep: values.
# Toodledo uses English phrases; doit uses Nd/Nw/Nm/Ny or weekday names.
REPEAT_MAP = {
    "daily":              "1d",
    "every day":          "1d",
    "weekly":             "1w",
    "every week":         "1w",
    "biweekly":           "2w",
    "every 2 weeks":      "2w",
    "fortnightly":        "2w",
    "every 3 weeks":      "3w",
    "every 4 weeks":      "4w",
    "every 5 weeks":      "5w",
    "monthly":            "1m",
    "every month":        "1m",
    "bimonthly":          "2m",   # every 2 months
    "every 2 months":     "2m",
    "every 4 months":     "4m",
    "quarterly":          "3m",
    "every 3 months":     "3m",
    "every 6 months":     "6m",
    "semiannually":       "6m",
    "semi-annually":      "6m",
    "yearly":             "1y",
    "annually":           "1y",
    "every year":         "1y",
    "every 2 years":      "2y",
    "every 3 years":      "3y",
    "every 4 years":      "4y",
    "every 5 years":      "5y",
    "every 10 years":     "10y",
    "every fri":          "fri",
    "every friday":       "fri",
    "every mon":          "mon",
    "every monday":       "mon",
    "every tue":          "tue",
    "every tuesday":      "tue",
    "every wed":          "wed",
    "every wednesday":    "wed",
    "every thu":          "thu",
    "every thursday":     "thu",
    "every sat":          "sat",
    "every saturday":     "sat",
    "every sun":          "sun",
    "every sunday":       "sun",
}

def parse_repeat(val: str) -> str | None:
    """Convert a Toodledo repeat string to a doit #rep: value, or None if unknown."""
    if not val:
        return None
    key = val.strip().lower()
    if key in REPEAT_MAP:
        return REPEAT_MAP[key]
    # "Every N days/weeks/months/years"
    m = re.match(r'^every (\d+) (day|week|month|year)s?$', key)
    if m:
        n, unit = m.group(1), m.group(2)
        return f"{n}{'d' if unit=='day' else 'w' if unit=='week' else 'm' if unit=='month' else 'y'}"
    return None


def row_to_line(row: dict) -> tuple[str, str]:
    """Return (task_line, raw_notes) for a CSV row."""
    desc = row.get("TASK", "").strip()
    if not desc:
        return "", ""

    tags = {}

    # Context: only use CONTEXT field — FOLDER is a list/category, not a context
    ctx = (row.get("CONTEXT") or "").strip()
    if ctx:
        tags["#ctx"] = ctx

    due = (row.get("DUEDATE") or "").strip()
    if due:
        tags["#due"] = due

    start = (row.get("STARTDATE") or "").strip()
    if start:
        tags["#start"] = start

    pri = PRIORITY_MAP.get((row.get("PRIORITY") or "").strip())
    if pri:
        tags["#p"] = pri

    rep = parse_repeat((row.get("REPEAT") or "").strip())
    if rep:
        tags["#rep"] = rep

    if (row.get("STAR") or "").strip() == "1":
        tags["#star"] = None

    # Assign a stable id now
    tags["#id"] = _new_id()

    # Build tag string (put #id last)
    parts = []
    for k, v in sorted(tags.items(), key=lambda kv: (kv[0] == "#id", kv[0])):
        parts.append(f"{k}:{v}" if v is not None else k)
    tag_str = " ".join(parts)

    line = f"- [ ] {desc}"
    if tag_str:
        line += f" {tag_str}"

    note = (row.get("NOTE") or "").strip()
    return line, note


def import_csv(csv_path: Path, tasks_file: Path, dry_run: bool) -> int:
    """Import tasks from csv_path into tasks_file. Returns count imported."""
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    imported = []
    skipped = 0
    unknown_repeats = set()

    for row in rows:
        # Skip completed tasks
        status = (row.get("STATUS") or "").strip().lower()
        if status in ("checked", "done", "complete", "completed"):
            skipped += 1
            continue

        line, note = row_to_line(row)
        if not line:
            skipped += 1
            continue

        rep_raw = (row.get("REPEAT") or "").strip()
        if rep_raw and parse_repeat(rep_raw) is None:
            unknown_repeats.add(rep_raw)

        imported.append((line, note))

    print(f"Importing {len(imported)} tasks, skipping {skipped} completed.")
    if unknown_repeats:
        print(f"Warning: unrecognised repeat values (imported without #rep): "
              f"{', '.join(sorted(unknown_repeats))}")

    if dry_run:
        print("\n--- DRY RUN (first 10) ---")
        for line, note in imported[:10]:
            print(line)
            if note:
                for nl in note.splitlines():
                    print(f"  {nl}")
        return len(imported)

    # Read existing tasks and append new ones under Inbox
    existing = read_tasks(tasks_file)
    # Build extra_lines: task line + indented notes
    extra = []
    for line, note in imported:
        extra.append(line)
        if note:
            for nl in note.splitlines():
                nl = nl.rstrip()
                if nl and nl[0] not in (" ", "\t"):
                    nl = "  " + nl
                extra.append(nl)

    write_tasks(existing, tasks_file, extra_lines=extra)
    return len(imported)


def main():
    parser = argparse.ArgumentParser(description="Import Toodledo CSV into doit.")
    parser.add_argument("--user", required=True, help="User email address")
    parser.add_argument("--file", required=True, help="Path to Toodledo CSV export")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and print without writing anything")
    args = parser.parse_args()

    csv_path = Path(args.file)
    if not csv_path.exists():
        sys.exit(f"File not found: {csv_path}")

    users = UserStore.list_users()
    user = next((u for u in users if u["email"] == args.user), None)
    if not user:
        sys.exit(f"User not found: {args.user}")

    tasks_file = get_tasks_file(user["id"])
    if not tasks_file.exists():
        sys.exit(f"Tasks file not found: {tasks_file}")

    n = import_csv(csv_path, tasks_file, args.dry_run)
    if not args.dry_run:
        print(f"Done — {n} tasks imported into {tasks_file}")


if __name__ == "__main__":
    main()
