#!/usr/bin/env python3
"""
One-time cleanup: remove #ctx:DoIt, #ctx:Home, #ctx:Personal (and any other
Toodledo FOLDER values) that were incorrectly imported as contexts.

Usage:
  python3 src/cleanup_imported_ctx.py --user <email> [--dry-run]
  python3 src/cleanup_imported_ctx.py --user <email> --strip DoIt,Home,Personal,Work
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from task_manager import read_tasks, write_tasks, get_tasks_file
from user_store import UserStore

# Default Toodledo folder names to strip
DEFAULT_STRIP = {"DoIt", "Home", "Personal", "Work", "Family", "Errands"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--strip", help="Comma-separated ctx values to remove (default: Toodledo folders)")
    args = parser.parse_args()

    strip_set = {s.strip() for s in args.strip.split(",")} if args.strip else DEFAULT_STRIP

    users = UserStore.list_users()
    user = next((u for u in users if u["email"] == args.user), None)
    if not user:
        sys.exit(f"User not found: {args.user}")

    tasks_file = get_tasks_file(user["id"])
    tasks = read_tasks(tasks_file)

    fixed = 0
    for t in tasks:
        if t.context in strip_set:
            if args.dry_run:
                print(f"Would strip #ctx:{t.context} from: {t.description}")
            else:
                t.set_context(None)
            fixed += 1

    if not args.dry_run:
        write_tasks(tasks, tasks_file)
        print(f"Stripped bad ctx from {fixed} tasks.")
    else:
        print(f"\n{fixed} tasks would be updated (--dry-run, nothing written).")


if __name__ == "__main__":
    main()
