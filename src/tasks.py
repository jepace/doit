#!/usr/bin/env python3
"""
doit — Task Filter CLI

Reads a user's tasks.md and filters/displays tasks.

Usage: python3 src/tasks.py --user <uuid> [options]
       python3 src/tasks.py --file <path/to/tasks.md> [options]

Options:
  --user UUID       Read tasks for the given user UUID (from data/{uuid}/tasks.md)
  --file PATH       Read tasks directly from the given file path
  --open            Show open tasks only (default)
  --done            Show completed tasks only
  --all             Show all tasks regardless of status
  --priority P      Filter by priority: top, high, medium, low
  --context C       Filter by context tag (#ctx:C), e.g. work, home
  --project P       Filter by project tag (#proj:P)
  --due-today       Show tasks due today or overdue
  --overdue         Show tasks with a past due date only
  --star            Show starred tasks only
  --repeat          Show recurring tasks only

Results are sorted by due date (soonest first, no-date last), then by priority.
"""

import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent))

from task_manager import read_tasks, DATA_DIR

PRIORITY_ORDER = {"top": 0, "high": 1, "medium": 2, "low": 3}


def parse_date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def priority_key(p):
    return PRIORITY_ORDER.get(p, 99)


def format_task(task, today):
    status = "[x]" if task.complete else "[ ]"
    star   = "★ " if "#star" in task.tags else ""
    pri    = f"#p:{task.priority}" if task.priority else ""
    rep    = f"rep:{task.recurrence}" if task.recurrence else ""

    due_str = ""
    if task.due:
        d = parse_date(task.due)
        if d:
            if task.complete:
                due_str = f"due:{task.due}"
            elif d < today:
                due_str = f"OVERDUE({task.due})"
            elif d == today:
                due_str = "DUE TODAY"
            else:
                delta = (d - today).days
                due_str = f"due:{task.due} ({delta}d)"
        else:
            due_str = f"due:{task.due}"

    ctx  = f"@{task.context}"  if task.context  else ""
    proj = f"+{task.project}"  if task.project  else ""

    meta = "  ".join(x for x in [pri, due_str, rep, ctx, proj] if x)
    line = f"  {status} {star}{task.description}"
    if meta:
        line += f"  [{meta}]"
    return line


def main():
    args = sys.argv[1:]

    tasks_file    = None
    show_open     = True
    show_done     = False
    filter_priority = None
    filter_context  = None
    filter_project  = None
    due_today_only  = False
    overdue_only    = False
    star_only       = False
    repeat_only     = False

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--user" and i + 1 < len(args):
            tasks_file = DATA_DIR / args[i + 1] / "tasks.md"; i += 1
        elif a == "--file" and i + 1 < len(args):
            tasks_file = Path(args[i + 1]); i += 1
        elif a == "--open":
            show_open, show_done = True, False
        elif a == "--done":
            show_open, show_done = False, True
        elif a == "--all":
            show_open = show_done = True
        elif a == "--priority" and i + 1 < len(args):
            filter_priority = args[i + 1].lower(); i += 1
        elif a == "--context" and i + 1 < len(args):
            filter_context = args[i + 1].lower(); i += 1
        elif a == "--project" and i + 1 < len(args):
            filter_project = args[i + 1].lower(); i += 1
        elif a == "--due-today":
            due_today_only = True
        elif a == "--overdue":
            overdue_only = True
        elif a == "--star":
            star_only = True
        elif a == "--repeat":
            repeat_only = True
        elif a in ("-h", "--help"):
            print(__doc__.strip())
            sys.exit(0)
        else:
            print(f"Unknown option: {a}\nRun with --help for usage.", file=sys.stderr)
            sys.exit(1)
        i += 1

    if tasks_file is None:
        print("Error: specify --user <uuid> or --file <path>", file=sys.stderr)
        sys.exit(1)

    if not tasks_file.exists():
        print(f"Error: tasks file not found: {tasks_file}", file=sys.stderr)
        sys.exit(1)

    all_tasks = read_tasks(tasks_file)
    today     = date.today()

    filtered = []
    for t in all_tasks:
        if t.complete and not show_done:    continue
        if not t.complete and not show_open: continue
        if filter_priority and t.priority != filter_priority: continue
        if filter_context  and t.context  != filter_context:  continue
        if filter_project  and t.project  != filter_project:  continue
        if star_only   and "#star" not in t.tags: continue
        if repeat_only and not t.recurrence:      continue
        if due_today_only:
            d = parse_date(t.due)
            if not d or d > today: continue
        if overdue_only:
            d = parse_date(t.due)
            if not d or d >= today: continue
        filtered.append(t)

    if not filtered:
        print("No tasks match the given filters.")
        sys.exit(0)

    filtered.sort(key=lambda t: (
        parse_date(t.due) or date(9999, 12, 31),
        priority_key(t.priority),
        t.description.lower(),
    ))

    by_section: dict[str, list] = {}
    for t in filtered:
        by_section.setdefault(t.section or "Inbox", []).append(t)

    print(f"\nTasks ({len(filtered)} shown):\n")
    for section, section_tasks in by_section.items():
        print(f"### {section}")
        for t in section_tasks:
            print(format_task(t, today))
            if t.notes:
                for note_line in t.notes.splitlines():
                    print(f"         {note_line.strip()}")
        print()


if __name__ == "__main__":
    main()
