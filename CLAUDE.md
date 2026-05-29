# CLAUDE.md

## What This Is

doit is a multi-user personal task manager. Each user's tasks live in `data/{uuid}/tasks.md` as plain markdown with inline tags. The web UI (`src/serve.py`) lets you view, add, edit, and complete tasks.

## Running

```sh
pip install -r requirements.txt
cp config.json.example config.json
python3 src/serve.py               # web UI at http://127.0.0.1:8080
python3 src/tasks.py --due-today   # CLI filter (no server needed)
```

## Architecture

**`src/serve.py`** — Flask web server. Routes: `/tasks*` (task CRUD), `/auth/*` (login/logout/register/reset), `/settings`, `/admin`. Uses ProxyFix when `https: true`.

**`src/task_manager.py`** — Task parsing, reading, and writing. The `Task` class and `read_tasks(tasks_file)`/`write_tasks(tasks, tasks_file)` functions. Also handles recurrence.

**`src/user_store.py`** — Multi-user auth. `UserStore` class handles registration, login, email verification, password reset, and admin functions. Users stored in `data/{uuid}/`.

**`src/mailer.py`** — Email sending via Resend API. Used for verification and password reset emails.

**`src/tasks.py`** — CLI filter tool. Reads a tasks file and prints filtered/sorted tasks. No Flask needed.

**`src/config.py`** — reads `config.json`. Use `cfg_get(section, key, default)`.

**`src/templates/tasks_view.html`** — the main task UI (desktop table + mobile cards).

**`tests/`** — pytest suite (106 tests). Covers task parsing, recurrence, config, and all Flask routes.

## Task format

```
- [ ] Task description #p:high #due:2026-05-01 #ctx:work #proj:name #rep:1w
  Optional note line (indented)
```

Tags: `#p:top/high/medium/low`, `#due:YYYY-MM-DD`, `#ctx:context`, `#proj:project`, `#rep:1d/1w/2w/1m/1y`, `#start:YYYY-MM-DD`, `#star`, `#done:YYYY-MM-DD`

## Auth

Multi-user. Registration requires email verification via Resend. Passwords hashed with Werkzeug's `generate_password_hash`. Admin users can suspend/delete accounts via `/admin`.
