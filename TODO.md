# Production Readiness TODO

From Opus code review. Prioritised: Critical → High → Medium → Low.

---

## CRITICAL

- [x] **C1** `src/serve.py:110-118` — CSRF bypass on all JSON endpoints (`/tasks/toggle`, `/tasks/add`, `/tasks/update`, `/tasks/bulk-update`). The `is_json` exemption is not safe. Fix: validate CSRF via `X-CSRF-Token` header or include token in JSON body.

- [x] **C2** `src/serve.py:381-413`, `src/task_manager.py:227-264` — `_toggle_task` and `_add_task` use `write_text` directly (not atomic). A crash mid-write truncates `tasks.md`. Fix: write via tmp file + `os.replace` like `_write_json` does.

- [x] **C3** `src/user_store.py:33-46` — `threading.Lock` is per-process; useless under multi-worker gunicorn/uWSGI. Concurrent workers can corrupt `tasks.md` and `email_index.json`. Fix: use `fcntl.flock` for OS-level file locking, or enforce single-worker and document it.

---

## HIGH

- [x] **H1** `src/serve.py:125-139` — Rate limiter is in-memory per-process (ineffective with multiple workers) and never evicts keys (unbounded memory growth from spoofed IPs). Fix: added thread lock, key cap (10k), and stale-key pruning. Note: multi-worker effectiveness still requires Redis for a true fix.

- [x] **H2** `src/serve.py:142-143` — `_ip()` always trusts `X-Forwarded-For` even without a proxy, letting clients spoof IPs to bypass rate limits and forge audit logs. Fix: only use `request.remote_addr` (ProxyFix rewrites it when active).

- [x] **H3** `src/serve.py:306-314`, `src/user_store.py:207-222` — (a) Login timing oracle: `check_password_hash` is skipped for missing users. Fix: dummy hash call. (b) Auto-resend on login allows email-bombing unverified addresses. Fix: removed auto-resend; user is directed to verify_pending page.

- [x] **H4** `src/user_store.py:276-297, 314-340` — Token lookup scans all users (O(n) disk reads); hash comparison non-constant-time. Fix: added `token_index.json` for O(1) lookup; use `secrets.compare_digest`.

- [x] **H5** `src/tasks.py:36-47` — CLI broken in multi-user layout; different parser from `task_manager.py`. Fix: rewritten to use `task_manager.read_tasks`; accepts `--user <uuid>` or `--file <path>`.

---

## MEDIUM

- [x] **M1** `src/serve.py:501-510` — Recurrence append is a second non-atomic write after `write_tasks`. Fix: added `extra_lines` param to `write_tasks`; recurrence task now included in the single atomic write.

- [ ] **M2** `src/serve.py:431-440, 456-471` — Task identity is positional (ordinal index). Concurrent edits or stale clients silently modify the wrong task. Requires stable IDs in the file format — design decision deferred.

- [x] **M3** `src/serve.py:551-553` — Bulk "delete" wrote `[DELETED]` instead of removing the task. Fix: tasks are now removed from the list before `write_tasks`.

- [x] **M4** `src/serve.py:251-254, 272-282` — `/auth/resend-verify` unthrottled. Fix: rate-limited 3/hour by IP and by target email.

- [x] **M5** `src/serve.py:585-594` — `send_*_email` return values ignored. Fix: checked in register, resend-verify, forgot, and settings; errors logged and surfaced to user on email change.

- [x] **M6** `src/task_manager.py:205-264` — Indented checkboxes (subtasks) extracted as top-level tasks, corrupting round-trip. Fix: note-collection loop now only breaks on non-indented task lines, preserving subtasks as note content.

- [x] **M7** `src/serve.py:68-78` — `.secret` file written world-readable. Fix: `os.chmod(_secret_file, 0o600)` after write.

- [x] **M8** Multiple files — Logging gaps. Fix: email send failures logged at all call sites; `@app.errorhandler(500)` added with user/route context.

- [x] **M9** `src/task_manager.py:145` — Malformed `#due` causes uncaught `ValueError` in `get_next_recurrence`. Fix: wrapped in try/except with fallback to today.

---

## LOW / MINOR

- [x] **L1** Tests insert non-existent `tools/` into `sys.path` — removed.
- [ ] **L2** `#s` / `Task.status` tag is undocumented and inconsistent with the documented tag set in CLAUDE.md. Low impact — leaving as-is.
- [x] **L3** `_safe_next` — added explicit rejection of protocol-relative URLs (`//`, `/\`).
- [x] **L4** `cfg_bool` uses `bool(v)` — string `"false"` evaluated to `True`. Fixed to parse truthy/falsy strings.
- [x] **L5** `datetime.utcnow()` deprecated in Python 3.12+. Replaced with `_utcnow()` helper using `datetime.now(timezone.utc).replace(tzinfo=None)`.
- [x] **L6** `inject_globals` created sessions for anonymous visitors. Fixed to only call `generate_csrf()` when a session already exists.
- [x] **L7** `_check_rate_limit` mutated shared dict without a lock. Fixed with `threading.Lock`.
