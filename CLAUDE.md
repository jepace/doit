# CLAUDE.md

## What This Is

doit is a multi-user personal task manager. Each user's tasks live in `data/{uuid}/tasks.md` as plain markdown with inline tags. The web UI (`src/serve.py`) lets you view, add, edit, and complete tasks.

## Running

```sh
pip install -r requirements.txt
cp config.json.example config.json
python3 src/serve.py                            # web UI at http://127.0.0.1:8080
python3 src/tasks.py --user <uuid>              # CLI filter for a specific user
python3 src/tasks.py --file data/<uuid>/tasks.md  # CLI filter by path
```

## Architecture

**`src/serve.py`** — Flask web server. Routes: `/tasks*` (task CRUD), `/auth/*` (login/logout/register/reset), `/settings`, `/admin`. Uses ProxyFix when `https: true`.

**`src/task_manager.py`** — Task parsing, reading, and writing. The `Task` class and `read_tasks(tasks_file)`/`write_tasks(tasks, tasks_file, extra_lines=None, archive_file=None)` functions. Also handles recurrence and group headers (`compute_group_headers`, mirroring the JS grouping so headers render server-side).

**Active vs archived.** Active tasks live in `tasks.md`; completed ones are moved to `archive.md`. Completing a task is an *append* to `archive.md` (`append_to_archive`), so its cost doesn't grow with history and routine edits never rewrite it. Only reopen/edit/delete of an already-archived task rewrites the archive (`write_archive`, which moves no-longer-complete tasks back into `tasks.md` first). Rule when moving a task between the two files: **write the destination before the source**, so an interruption leaves a recoverable duplicate rather than losing data. The main page never loads or renders the archive — the "Show completed" toggle fetches `/tasks/archive` on demand. A legacy `## Archive` section inside `tasks.md` is folded out automatically on the next write; `src/migrate_archive.py` does it eagerly (with a backup).

**`src/user_store.py`** — Multi-user auth. `UserStore` class handles registration, login, email verification, password reset, and admin functions. Users stored in `data/{uuid}/`.

**`src/mailer.py`** — Email sending via Resend API. Used for verification and password reset emails.

**`src/tasks.py`** — CLI filter tool. Reads a user's tasks file and prints filtered/sorted tasks. No Flask needed. Accepts `--user <uuid>` or `--file <path>`.

**`src/config.py`** — reads `config.json`. Use `cfg_get(section, key, default)`.

**`src/templates/tasks_view.html`** — the main task UI (desktop table + mobile cards).

**`tests/`** — pytest suite (170+ tests). Covers task parsing, recurrence, config, user store, mailer, CLI, and all Flask routes.

## Task format

```
- [ ] Task description #p:high #due:2026-05-01 #ctx:work #proj:name #rep:1w
  Optional note line (indented)
  - [ ] Subtask (indented checkbox preserved as note content)
```

Tags: `#p:top/high/medium/low`, `#due:YYYY-MM-DD`, `#ctx:context`, `#proj:project`, `#rep:1d/1w/2w/1m/1y` (or a weekday: `#rep:fri`, `#rep:mon,wed,fri` — advances to the next listed weekday after completion), `#start:YYYY-MM-DD`, `#s:waiting/blocked/in-progress/someday`, `#star`, `#done:YYYY-MM-DD`

`#s` is a workflow status for tasks that aren't simply open/done — use it for GTD-style states like `waiting` (blocked on someone else), `blocked`, `in-progress`, or `someday` (maybe/later).

## Data directory layout

```
data/
  email_index.json          # email → uuid mapping (global)
  token_index.json          # token_hash → uuid mapping (global, for O(1) token lookup)
  .email_index.lock         # flock sidecar files (Unix only)
  .token_index.lock
  {uuid}/
    profile.json            # auth fields (password hash, tokens, suspend/admin flags)
    preferences.json        # display preferences (theme, sort, etc.)
    tasks.md                # user's ACTIVE tasks in markdown
    archive.md              # completed tasks (append-only; fetched on demand)
    .{uuid}.lock            # flock sidecar for this user
  .secret                   # auto-generated Flask session secret (chmod 0o600)
```

## Auth

Multi-user. Registration requires email verification via Resend. Passwords hashed with Werkzeug's `generate_password_hash`. Admin users can suspend/delete accounts via `/admin`.

## Platform notes

- **Linux, macOS, FreeBSD, and other Unix-like systems**: fully supported, including multi-worker WSGI deployments. File locking uses `fcntl.flock`.
- **Windows**: supported for single-worker deployments only. `fcntl` is unavailable so locking falls back to `threading.Lock`; concurrent WSGI workers will race on shared files.

For production, run behind a reverse proxy (nginx/caddy) with `https: true` in config.json and a single gunicorn worker, or multiple workers on Unix where `fcntl` locking is safe.
