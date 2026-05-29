# CLAUDE.md

## What This Is

doit is a standalone personal task manager. Tasks live in `data/tasks.md` as plain markdown with inline tags. The web UI (`src/serve.py`) lets you view, add, edit, and complete tasks.

## Running

```sh
pip install flask
cp config.json.example config.json
python3 src/serve.py               # web UI at http://127.0.0.1:8080
python3 src/tasks.py --due-today   # CLI filter (no server needed)
```

## Architecture

**`src/serve.py`** — Flask web server. Routes: `/tasks*` (task CRUD), `/auth/*` (login/logout), `/setup` (first-run password creation). No AI, no wiki, no external dependencies beyond Flask.

**`src/task_manager.py`** — Task parsing, reading, and writing. The `Task` class and `read_tasks()`/`write_tasks()` functions. Also handles recurrence.

**`src/tasks.py`** — CLI filter tool. Reads `data/tasks.md` and prints filtered/sorted tasks. No Flask needed.

**`tests/`** — pytest suite (109 tests). Covers task parsing, recurrence, config, and all Flask routes.

**`src/config.py`** — reads `config.json`. Use `cfg_get(section, key, default)`.

**`src/templates/tasks_view.html`** — the main task UI (desktop table + mobile cards).

## Task format

```
- [ ] Task description #p:high #due:2026-05-01 #ctx:work #proj:name #rep:1w
  Optional note line (indented)
```

Tags: `#p:top/high/medium/low`, `#due:YYYY-MM-DD`, `#ctx:context`, `#proj:project`, `#rep:1d/1w/2w/1m/1y`, `#start:YYYY-MM-DD`, `#star`, `#done:YYYY-MM-DD`

## Auth

Single-user password auth. On first visit `/setup` creates a password stored as a SHA-256 hash in `data/.passwd`. Subsequent logins check against it.
