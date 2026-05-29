# CLAUDE.md

## What This Is

doit is a standalone personal task manager. Tasks live in `wiki/tasks.md` as plain markdown with inline tags. The web UI (`tools/serve.py`) lets you view, add, edit, and complete tasks.

## Running

```sh
pip install flask
cp config.json.example config.json
python3 tools/serve.py               # web UI at http://127.0.0.1:8080
python3 tools/tasks.py --due-today   # CLI filter (no server needed)
```

## Architecture

**`tools/serve.py`** — Flask web server. Routes: `/tasks*` (task CRUD), `/auth/*` (login/logout), `/setup` (first-run password creation). No AI, no wiki, no external dependencies beyond Flask.

**`tools/task_manager.py`** — Task parsing, reading, and writing. The `Task` class and `read_tasks()`/`write_tasks()` functions. Also handles recurrence.

**`tools/tasks.py`** — CLI filter tool. Reads `wiki/tasks.md` and prints filtered/sorted tasks. No Flask needed.

**`tools/config.py`** — reads `config.json`. Use `cfg_get(section, key, default)`.

**`tools/templates/tasks_view.html`** — the main task UI (desktop table + mobile cards).

## Task format

```
- [ ] Task description #p:high #due:2026-05-01 #ctx:work #proj:name #rep:1w
  Optional note line (indented)
```

Tags: `#p:top/high/medium/low`, `#due:YYYY-MM-DD`, `#ctx:context`, `#proj:project`, `#rep:1d/1w/2w/1m/1y`, `#start:YYYY-MM-DD`, `#star`, `#done:YYYY-MM-DD`

## Auth

Single-user password auth. On first visit `/setup` creates a password stored as a SHA-256 hash in `wiki/.passwd`. Subsequent logins check against it.
