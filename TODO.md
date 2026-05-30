# Production Readiness TODO

From Opus code review. Prioritised: Critical → High → Medium → Low.

---

## CRITICAL

- [x] **C1** `src/serve.py:110-118` — CSRF bypass on all JSON endpoints (`/tasks/toggle`, `/tasks/add`, `/tasks/update`, `/tasks/bulk-update`). The `is_json` exemption is not safe. Fix: validate CSRF via `X-CSRF-Token` header or include token in JSON body.

- [x] **C2** `src/serve.py:381-413`, `src/task_manager.py:227-264` — `_toggle_task` and `_add_task` use `write_text` directly (not atomic). A crash mid-write truncates `tasks.md`. Fix: write via tmp file + `os.replace` like `_write_json` does.

- [x] **C3** `src/user_store.py:33-46` — `threading.Lock` is per-process; useless under multi-worker gunicorn/uWSGI. Concurrent workers can corrupt `tasks.md` and `email_index.json`. Fix: use `fcntl.flock` for OS-level file locking, or enforce single-worker and document it.

---

## HIGH

- [ ] **H1** `src/serve.py:125-139` — Rate limiter is in-memory per-process (ineffective with multiple workers) and never evicts keys (unbounded memory growth from spoofed IPs). Fix: shared store (Redis) or prune expired keys and cap dict size.

- [ ] **H2** `src/serve.py:142-143` — `_ip()` always trusts `X-Forwarded-For` even without a proxy, letting clients spoof IPs to bypass rate limits and forge audit logs. Fix: only use `X-Forwarded-For` when ProxyFix is active; otherwise use `request.remote_addr`.

- [ ] **H3** `src/serve.py:306-314`, `src/user_store.py:207-222` — (a) Login timing oracle: `check_password_hash` is skipped for missing/locked/unverified users, revealing account existence. (b) Auto-resend verification email on login has no per-email rate limit — attacker can flood any unverified address. Fix: dummy `check_password_hash` call for missing users; rate-limit or remove auto-resend.

- [ ] **H4** `src/user_store.py:276-297, 314-340` — Token lookup scans all users (O(n) disk reads); hash comparison uses `!=` (not constant-time). Fix: token→user_id index for O(1) lookup; use `secrets.compare_digest`.

- [ ] **H5** `src/tasks.py:36-47` — CLI broken in multi-user layout (`data/tasks.md` doesn't exist); uses a different parser than `task_manager.py` (different tags, subtask handling). Fix: accept user path arg; import `task_manager.read_tasks` instead of reimplementing.

---

## MEDIUM

- [ ] **M1** `src/serve.py:501-510` — Recurrence append is a second non-atomic write after `write_tasks`. A crash between them loses the recurrence. Fix: add recurrence task to `tasks_list` before `write_tasks` so it's one atomic write.

- [ ] **M2** `src/serve.py:431-440, 456-471` — Task identity is positional (ordinal index). Concurrent edits or stale clients silently modify the wrong task. Fix: stable identifier (e.g. hash of task line) validated before applying changes.

- [ ] **M3** `src/serve.py:551-553` — Bulk "delete" writes `[DELETED]` instead of removing the task. Deleted tasks still appear in `get_all_projects` / `get_all_contexts`. Fix: remove from list before `write_tasks`, or filter `[DELETED]` everywhere.

- [ ] **M4** `src/serve.py:251-254, 272-282` — `/auth/resend-verify` takes arbitrary email from form with no rate limit. Fix: rate-limit by IP and email; use pending-session email, not arbitrary form input.

- [ ] **M5** `src/serve.py:585-594` — `send_*_email` return values ignored everywhere. If email fails on address change, user is locked out silently. Fix: check return values, surface error to user and log it.

- [ ] **M6** `src/task_manager.py:205-264` — `read_tasks`/`write_tasks` round-trip corrupts files with indented checkboxes (subtasks): reads them as top-level tasks, rewrites them flat. Fix: decide on subtask support and handle consistently in both parsers.

- [ ] **M7** `src/serve.py:68-78` — `.secret` file written with default umask (world-readable). No error handling if `DATA_DIR` unwritable (raw traceback). Fix: `os.chmod(_secret_file, 0o600)` after writing; fail fast with clear message on startup.

- [ ] **M8** Multiple files — Logging gaps: email send failures not logged at call sites; task mutations not logged; no `@app.errorhandler(500)` so unexpected exceptions produce a bare 500 with no app-level log. Fix: log email outcomes, add 500 handler, consider audit log for task mutations.

- [ ] **M9** `src/task_manager.py:145` — Malformed `#due` causes uncaught `ValueError` in `get_next_recurrence`, producing a 500. Fix: validate in `set_due`/`set_start`, or wrap parse with fallback to today.

---

## LOW / MINOR

- [ ] **L1** Tests insert non-existent `tools/` into `sys.path` — dead code.
- [ ] **L2** `#s` / `Task.status` tag is undocumented and inconsistent with the documented tag set in CLAUDE.md.
- [ ] **L3** `_safe_next` — add explicit test/rejection of protocol-relative URLs (`//evil.com`).
- [ ] **L4** `cfg_bool` uses `bool(v)` — string `"false"` evaluates to `True`. Use explicit truthy-string parsing.
- [ ] **L5** `datetime.utcnow()` deprecated in Python 3.12+ throughout `user_store.py`. Use `datetime.now(timezone.utc)`.
- [ ] **L6** `inject_globals` creates a session (Set-Cookie) for every anonymous visitor via `generate_csrf()`.
- [ ] **L7** `_check_rate_limit` mutates shared dict without a lock — threads can race and under-count.
