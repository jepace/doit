"""Productivity statistics for the /stats dashboard.

Pure functions over Task lists (active from tasks.md, completed from
archive.md) so they're easy to test and never touch the filesystem.
"""

from collections import Counter
from datetime import date, timedelta

WEEKS = 26
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _d(s):
    try:
        return date.fromisoformat(s) if s else None
    except (TypeError, ValueError):
        return None


def compute_stats(active: list, archived: list, today: date) -> dict:
    open_tasks = [t for t in active if not t.complete]
    done = [(t, _d(t.tags.get("#done"))) for t in archived if t.complete]
    done = [(t, d) for t, d in done if d and d <= today]
    done_dates = [d for _, d in done]

    # ── Headline tiles ────────────────────────────────────────────
    week_start = today - timedelta(days=today.weekday())
    overdue = [t for t in open_tasks if _d(t.due) and _d(t.due) < today]
    days_with = set(done_dates)
    streak, day = 0, today if today in days_with else today - timedelta(days=1)
    while day in days_with:
        streak += 1
        day -= timedelta(days=1)

    recent_due = [(t, d) for t, d in done
                  if d > today - timedelta(days=90) and _d(t.due)]
    on_time = sum(1 for t, d in recent_due if d <= _d(t.due))
    on_time_pct = round(100 * on_time / len(recent_due)) if recent_due else None

    tiles = {
        "open":          len(open_tasks),
        "overdue":       len(overdue),
        "done_week":     sum(1 for d in done_dates if d >= week_start),
        "done_30":       sum(1 for d in done_dates if d > today - timedelta(days=30)),
        "streak":        streak,
        "on_time_pct":   on_time_pct,
        "on_time_n":     len(recent_due),
        "total_done":    len(done_dates),
    }

    # ── Completions per week (last WEEKS weeks, oldest first) ─────
    first = week_start - timedelta(weeks=WEEKS - 1)
    per_week = Counter((d - first).days // 7 for d in done_dates if d >= first)
    weekly = [{"start": (first + timedelta(weeks=i)).isoformat(),
               "count": per_week.get(i, 0)} for i in range(WEEKS)]
    avg_week = round(sum(w["count"] for w in weekly) / WEEKS, 1)

    # ── Day-of-week & context (last 90 days) ──────────────────────
    last90 = [(t, d) for t, d in done if d > today - timedelta(days=90)]
    dow = Counter(d.weekday() for _, d in last90)
    by_dow = [{"label": DOW[i], "count": dow.get(i, 0)} for i in range(7)]
    ctx = Counter((t.context or "(none)") for t, _ in last90)
    by_ctx = [{"label": k, "count": v} for k, v in ctx.most_common(8)]
    rest = sum(ctx.values()) - sum(c["count"] for c in by_ctx)
    if rest:
        by_ctx.append({"label": "Other", "count": rest})

    # ── Open tasks by when they're due ────────────────────────────
    buckets = Counter()
    for t in open_tasks:
        due = _d(t.due)
        if due is None:              buckets["No date"] += 1
        elif due < today:            buckets["Overdue"] += 1
        elif due == today:           buckets["Today"] += 1
        elif due <= today + timedelta(days=7):  buckets["Next 7 days"] += 1
        elif due <= today + timedelta(days=30): buckets["Next 30 days"] += 1
        else:                        buckets["Later"] += 1
    order = ["Overdue", "Today", "Next 7 days", "Next 30 days", "Later", "No date"]
    open_by_due = [{"label": k, "count": buckets.get(k, 0)} for k in order]

    # ── Longest-overdue open tasks ────────────────────────────────
    lingering = sorted(overdue, key=lambda t: t.due)[:5]
    lingering = [{"description": t.description, "due": t.due,
                  "days": (today - _d(t.due)).days, "context": t.context}
                 for t in lingering]

    return {"tiles": tiles, "weekly": weekly, "avg_week": avg_week,
            "by_dow": by_dow, "by_ctx": by_ctx, "open_by_due": open_by_due,
            "lingering": lingering}
