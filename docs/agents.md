# AI agent access (MCP + REST API)

doit exposes your task list to AI assistants and scripts two ways, both backed
by the same code (`src/task_api.py`) so they behave identically:

| Endpoint | For |
|---|---|
| `POST /mcp` | MCP clients — Claude Code, Claude Desktop, Gemini CLI, ChatGPT, etc. |
| `/api/v1/...` | Anything that speaks HTTP + JSON |

## Tokens

Create tokens in **Settings → AI Agents**. Each token has a name and a set of
permissions, and can be revoked on its own — make one per assistant.

| Scope | Allows |
|---|---|
| `read` | list / get tasks, list contexts |
| `write` | add, edit, complete, reopen |
| `delete` | permanently delete (off by default) |

Send it as `Authorization: Bearer doit_…`. Only a SHA-256 of the token is
stored; the plaintext is shown once. The older Siri quick-add token still works
and is treated as `write`-only.

## Connecting

**Claude Code**

    claude mcp add --transport http doit https://YOUR-HOST/mcp --header "Authorization: Bearer TOKEN"

**Gemini CLI** — `~/.gemini/settings.json`

    {"mcpServers": {"doit": {"httpUrl": "https://YOUR-HOST/mcp",
                             "headers": {"Authorization": "Bearer TOKEN"}}}}

**Claude Desktop** — `claude_desktop_config.json` (uses `mcp-remote`, needs Node)

    {"mcpServers": {"doit": {"command": "npx",
      "args": ["-y", "mcp-remote", "https://YOUR-HOST/mcp", "--header", "Authorization:${DOIT_AUTH}"],
      "env": {"DOIT_AUTH": "Bearer TOKEN"}}}}

claude.ai and ChatGPT *web/mobile* "custom connectors" expect OAuth rather than
a static token, so they aren't supported yet — use a desktop/CLI client.

## MCP tools

`list_tasks`, `get_task`, `list_contexts` (read) · `add_task`, `update_task`,
`complete_task`, `reopen_task` (write) · `delete_task` (delete).
`tools/list` only returns the tools a token's scopes allow.

## REST API

    GET    /api/v1/tasks?status=open|done|all&context=&project=&priority=&due_before=&due_after=&search=&limit=
    POST   /api/v1/tasks                      body: {description, due, start, priority, context,
                                                     project, recurrence, status, starred, notes, section}
    GET    /api/v1/tasks/{id}
    PATCH  /api/v1/tasks/{id}                 body: any settable fields; null clears; optional "version"
    POST   /api/v1/tasks/{id}/complete        returns {task, next_occurrence}
    POST   /api/v1/tasks/{id}/reopen
    DELETE /api/v1/tasks/{id}?version=
    GET    /api/v1/contexts
    GET    /api/v1/me                         the calling token's scopes

Errors are `{"error": {"code", "message"}}` with 400 (validation), 401, 403
(scope or origin), 404, 409 (stale `version`), 429 (rate limit).

Every task carries a `version`. Pass it back on a write to fail with 409 instead
of overwriting a change made in the meantime.

## Safety properties

- **Strict validation.** `tasks.md` is line-oriented with inline `#tags`, so
  descriptions must be one line with no `#tags`, dates must be real
  `YYYY-MM-DD`, contexts can't contain spaces, etc. Bad input is rejected with
  a readable message and nothing is written.
- **No cookies.** These routes ignore the session cookie entirely, so a web page
  can't drive them through your login (no CSRF). Browser requests from another
  origin are refused.
- **Rate limits.** 120 req/min per token, 600/min per IP, and 20 failed
  authentications per 10 min per IP.
- **Audit log.** Every change is logged with the token that made it
  (`doit.api` logger, e.g. `/var/log/doit.log`).
- **Prompt injection.** Task text is user data; the MCP server's instructions
  and tool descriptions tell the model never to follow instructions found in it.
