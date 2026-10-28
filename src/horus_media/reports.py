"""Summary reports: the same numbers as the graphs, boiled down to a few lines of text.

Used three ways: the Report card in Settings (on screen, plus a CSV download), "send now", and the
optional weekly or monthly report that goes out through the notification channels.
"""

import csv
import io
import sqlite3
from datetime import date, timedelta, tzinfo

from .stats import WEEKDAYS, compute_stats


def build(conn: sqlite3.Connection, start: date, end: date, tz: tzinfo | None = None) -> dict:
    now = compute_stats(conn, start, end, tz)
    span = (end - start).days + 1
    before = compute_stats(conn, start - timedelta(days=span), start - timedelta(days=1), tz)
    total = now["total_plays"]
    methods = {m["name"]: m["count"] for m in now["play_methods"]}
    by_day = [sum(day) for day in zip(*(s["data"] for s in now["plays_per_day"]["series"]),
                                      strict=False)]
    weekday = now["by_weekday"]["data"]
    peak_kbps = max((p[2] for p in now["bandwidth_total"]), default=0)
    return {
        "range": now["range"],
        "days": span,
        "total_plays": total,
        "previous_plays": before["total_plays"],
        "per_server": [{"server": s["server"], "plays": sum(s["data"])}
                       for s in now["plays_per_day"]["series"]],
        "top_users": now["by_user"][:5],
        "top_platforms": now["by_platform"][:3],
        "transcode_percent": round(100 * methods.get("transcode", 0) / total) if total else 0,
        "busiest_day": (now["plays_per_day"]["days"][by_day.index(max(by_day))]
                        if total and by_day else None),
        "busiest_weekday": WEEKDAYS[weekday.index(max(weekday))] if total else None,
        "busiest_hour": now["by_hour"].index(max(now["by_hour"])) if total else None,
        "peak_mbps": round(peak_kbps / 1000, 1),
        "daily": {"days": now["plays_per_day"]["days"], "series": now["plays_per_day"]["series"]},
    }


def to_text(r: dict) -> str:
    plural = "" if r["total_plays"] == 1 else "s"
    lines = [f"{r['range']['start']} to {r['range']['end']}: {r['total_plays']} play{plural}"
             f" (the {r['days']} days before: {r['previous_plays']})"]
    if not r["total_plays"]:
        return lines[0] + "\nNothing was played."
    lines.append("By server: " + ", ".join(f"{s['server']} {s['plays']}" for s in r["per_server"]))
    lines.append("Top people: " + ", ".join(f"{u['name']} {u['count']}" for u in r["top_users"]))
    lines.append("Top devices: " + ", ".join(f"{p['name']} {p['count']}" for p in r["top_platforms"]))
    lines.append(f"Transcoded: {r['transcode_percent']}% of plays")
    lines.append(f"Busiest day: {r['busiest_day']}; usually {r['busiest_weekday']}s around "
                 f"{r['busiest_hour']:02d}:00")
    if r["peak_mbps"]:
        lines.append(f"Peak bandwidth: {r['peak_mbps']} Mbps (all servers together)")
    return "\n".join(lines)


def to_csv(r: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["date", *[s["server"] for s in r["daily"]["series"]], "total"])
    for i, day in enumerate(r["daily"]["days"]):
        counts = [s["data"][i] for s in r["daily"]["series"]]
        w.writerow([day, *counts, sum(counts)])
    return out.getvalue()


def last_days(days: int, today: date | None = None) -> tuple[date, date]:
    """The `days` full days ending yesterday."""
    end = (today or date.today()) - timedelta(days=1)
    return end - timedelta(days=days - 1), end
