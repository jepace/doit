#!/usr/bin/env python3
"""
One-time migration: assign a stable #id tag to every task that lacks one.

Tasks without an #id get a fresh random id on each read, which makes their
content_hash unstable and causes spurious 409 conflicts (e.g. completing two
tasks in quick succession). Running this once writes permanent ids into every
user's tasks.md so the problem can't recur.

Usage:
  python3 src/backfill_ids.py            # migrate all users
  python3 src/backfill_ids.py --dry-run  # report what would change, write nothing
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from task_manager import read_tasks, write_tasks, DATA_DIR


def migrate_file(tasks_file: Path, dry_run: bool) -> int:
    """Return the number of tasks that were missing an #id."""
    if not tasks_file.exists():
        return 0
    tasks = read_tasks(tasks_file)
    missing = [t for t in tasks if not t.id]
    if missing and not dry_run:
        # write_tasks assigns an #id to every idless task before writing.
        write_tasks(tasks, tasks_file)
    return len(missing)


def main():
    parser = argparse.ArgumentParser(description="Backfill stable #id tags on all tasks.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report counts without modifying any files.")
    args = parser.parse_args()

    files = sorted(DATA_DIR.glob("*/tasks.md"))
    # Also handle a flat data/tasks.md layout, if present.
    flat = DATA_DIR / "tasks.md"
    if flat.exists():
        files.append(flat)

    if not files:
        print(f"No tasks.md files found under {DATA_DIR}")
        return

    total = 0
    for f in files:
        n = migrate_file(f, args.dry_run)
        total += n
        if n:
            verb = "would assign" if args.dry_run else "assigned"
            print(f"{f}: {verb} ids to {n} task(s)")

    action = "Would assign" if args.dry_run else "Assigned"
    print(f"\n{action} ids to {total} task(s) across {len(files)} file(s).")


if __name__ == "__main__":
    main()
