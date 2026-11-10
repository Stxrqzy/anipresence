"""Reconcile measured playback, AniList totals and dated feed estimates.

Feed dates are list-update dates, not playback timestamps. A known local episode
replaces its feed estimate even when the dates differ. Older logs without episode
identities use a conservative per-title/day overlap deduction instead.
"""
import re
from collections import defaultdict
from datetime import date, datetime, timedelta


def feed_episodes(snapshot):
    seen = set()
    for item in sorted(snapshot.get("items") or [], key=lambda e: e.get("createdAt") or 0):
        media = item.get("media") or {}
        status = str(item.get("status") or "").lower()
        if media.get("type") != "ANIME" or status not in ("watched episode", "rewatched episode", "re-watched episode"):
            continue
        match = re.fullmatch(r"\s*(\d+)(?:\s*[-–]\s*(\d+))?\s*", str(item.get("progress") or ""))
        if not match or not media.get("id"):
            continue
        start, end = int(match[1]), int(match[2] or match[1])
        if not 0 < start <= end <= 100000 or end-start > 5000:
            continue
        try:
            day = datetime.fromtimestamp(float(item.get("createdAt") or 0)).date()
        except (ValueError, OverflowError, OSError):
            continue
        if day > date.today() or not item.get("createdAt"):
            continue
        title = media.get("title") or {}
        duration = media.get("duration") or 24
        seconds = max(1, min(600, float(duration))) * 60
        for episode in range(start, end+1):
            key = (int(media["id"]), episode)
            if key in seen:
                continue
            seen.add(key)
            yield {"key": key, "day": day.isoformat(), "seconds": seconds,
                   "title": title.get("romaji") or title.get("english") or title.get("native") or str(media["id"]),
                   "media_id": int(media["id"]), "episode": episode,
                   "genres": media.get("genres") or [], "url": media.get("siteUrl") or "",
                   "cover": (media.get("coverImage") or {}).get("large") or ""}


def reconcile(titles, local_days, local_episodes, snapshot):
    rows = {name: dict(value, title=name, days=dict(value.get("days") or {}),
                       local_seconds=max(0, value.get("seconds", 0)-(value.get("imported_seconds") or 0)),
                       estimated_days={}, beyond_days={}) for name, value in titles.items()}
    by_id = {int(e["media_id"]): name for name, e in rows.items() if e.get("media_id")}
    by_name = {name.casefold(): name for name in rows}
    measured = set()
    episodes = {}
    for entry in local_episodes.values():
        name = entry["title"]
        media_id = entry.get("media_id")
        key = (int(media_id), entry["episode"]) if media_id else (name.casefold(), entry["episode"])
        measured.add(key)
        measured.add((name.casefold(), entry["episode"]))
        episodes[key] = max(entry.get("days") or {"": 0})
    for event in feed_episodes(snapshot):
        name = by_id.get(event["media_id"]) or by_name.get(event["title"].casefold()) or event["title"]
        row = rows.setdefault(name, dict(event, title=name, days={}, local_seconds=0,
                                        imported_seconds=0, estimated_days={}, beyond_days={}))
        row.setdefault("genres", event["genres"])
        row.setdefault("media_id", event["media_id"])
        by_id[event["media_id"]] = name
        key = event["key"]
        if key in measured or (name.casefold(), event["episode"]) in measured:
            continue
        episodes[key] = event["day"]
        estimates = row["estimated_days"]
        estimates[event["day"]] = estimates.get(event["day"], 0) + event["seconds"]
        if row.get("imported_progress") is not None and event["episode"] > row["imported_progress"]:
            beyond = row["beyond_days"]
            beyond[event["day"]] = beyond.get(event["day"], 0) + event["seconds"]
    combined_days = dict(local_days)
    for row in rows.values():
        # Legacy measured time is not mapped to episodes. Deduct only that
        # unmapped portion, so known local episodes aren't subtracted twice.
        mapped = defaultdict(float)
        beyond_import = 0
        for entry in local_episodes.values():
            if entry["title"] == row["title"] or (row.get("media_id") and entry.get("media_id") == row["media_id"]):
                for day, seconds in (entry.get("days") or {}).items():
                    mapped[day] += seconds
                if row.get("imported_progress") is not None and entry["episode"] > row["imported_progress"]:
                    beyond_import += sum(entry["days"].values())
        for day, seconds in list(row["estimated_days"].items()):
            legacy = max(0, row["days"].get(day, 0)-mapped[day])
            estimate = max(0, seconds-legacy)
            beyond_import += row["beyond_days"].get(day, 0) * (estimate/seconds if seconds else 0)
            row["estimated_days"][day] = estimate
            row["days"][day] = row["days"].get(day, 0)+estimate
            combined_days[day] = combined_days.get(day, 0)+estimate
        dated = row["local_seconds"] + sum(row["estimated_days"].values())
        row["seconds"] = max(dated, (row.get("imported_seconds") or 0)+beyond_import)
        row["estimated_seconds"] = max(0, row["seconds"]-row["local_seconds"])
    return {"titles": sorted(rows.values(), key=lambda e: e["seconds"], reverse=True),
            "days": combined_days, "episodes": episodes, "feed": snapshot}


def streak(days):
    today = date.today()
    if not days.get(today.isoformat()):
        today -= timedelta(days=1)
    count = 0
    while days.get(today.isoformat(), 0) > 0:
        count += 1
        today -= timedelta(days=1)
    return count
