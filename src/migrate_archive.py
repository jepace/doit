#!/usr/bin/env python3
"""
One-time migration: split each user's '## Archive' section out of tasks.md
into its own archive.md.

Completed history used to live at the bottom of tasks.md. That meant every
ordinary edit rewrote the whole history, and every page load parsed and shipped
it to the browser. Moving it to archive.md makes completion an O(1) append and
keeps history out of the request path entirely.

The app also migrates lazily (the next write for a user folds any leftover
'## Archive' block out to archive.md), so running this is optional — but doing
it up front means the very first page load after deploying is already fast.

Usage:
  python3 src/migrate_archive.py --all [--dry-run]
  python3 src/migrate_archive.py --user <email> [--dry-run]

A timestamped backup of tasks.md is written before anything is modified.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from task_manager import (read_tasks, get_tasks_file, get_archive_file,
                          append_to_archive, _write_text_atomic, ARCHIVE_HEADER)
from user_store import UserStore


def migrate_user(user: dict, dry_run: bool) -> tuple[int, int]:
    """Split one user's archive out. Returns (archived_moved, active_kept)."""
    tasks_file   = get_tasks_file(user["id"])
    archive_file = get_archive_file(user["id"])
    if not tasks_file.exists():
        print(f"  {user['email']}: no tasks.md — skipped")
        return 0, 0

    all_tasks = read_tasks(tasks_file)
    archived = [t for t in all_tasks if t.complete or t.section == "Archive"]
    active   = [t for t in all_tasks if not (t.complete or t.section == "Archive")]

    if not archived:
        print(f"  {user['email']}: nothing to move ({len(active)} active)")
        return 0, len(active)

    if dry_run:
        print(f"  {user['email']}: would move {len(archived)} archived, "
              f"keep {len(active)} active")
        return len(archived), len(active)

    # Back up first — this rewrites the file that holds all their data.
    stamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = tasks_file.with_name(f"tasks.md.bak.{stamp}")
    backup.write_text(tasks_file.read_text(encoding="utf-8"), encoding="utf-8")

    # Destination before source: if this is interrupted the task exists in both
    # files (visible, fixable) rather than in neither.
    if archive_file.exists():
        append_to_archive(archived, archive_file)
    else:
        archive_file.write_text(ARCHIVE_HEADER, encoding="utf-8")
        append_to_archive(archived, archive_file)

    # Rewrite tasks.md with only the active tasks, preserving section headers.
    lines = ["# Tasks", ""]
    by_section: dict[str, list] = {}
    for t in active:
        by_section.setdefault(t.section or "Inbox", []).append(t)
    for section, items in by_section.items():
        lines += [f"## {section}", ""]
        for t in items:
            lines.append(t.to_line())
            if t.raw_notes.strip():
                for nl in t.raw_notes.split("\n"):
                    if nl.strip() and nl[:1] not in (" ", "\t"):
                        nl = "  " + nl
                    lines.append(nl)
        lines.append("")
    _write_text_atomic(tasks_file, "\n".join(lines).rstrip("\n") + "\n")

    print(f"  {user['email']}: moved {len(archived)} archived, kept {len(active)} active"
          f"  (backup: {backup.name})")
    return len(archived), len(active)


def main():
    ap = argparse.ArgumentParser(description="Split tasks.md archive into archive.md")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all",  action="store_true", help="migrate every user")
    g.add_argument("--user", help="migrate a single user by email")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing")
    args = ap.parse_args()

    if args.all:
        users = UserStore.list_users()
    else:
        u = UserStore.get_by_email(args.user)
        if not u:
            sys.exit(f"User not found: {args.user}")
        users = [u]

    if args.dry_run:
        print("==> DRY RUN — nothing will be written")
    print(f"==> {len(users)} user(s)")

    total_arch = total_active = 0
    for u in users:
        a, k = migrate_user(u, args.dry_run)
        total_arch += a
        total_active += k

    print(f"==> Done. {total_arch} archived task(s) moved, {total_active} active kept.")
    if not args.dry_run and total_arch:
        print("    Backups are alongside each tasks.md as tasks.md.bak.<timestamp>")


if __name__ == "__main__":
    main()
