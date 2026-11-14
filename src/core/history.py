"""A deduplicated history view; AniList dates remain explicitly feed dates."""
from .activity_stats import feed_episodes
from .media_identity import MediaIdentity
from datetime import datetime
from itertools import groupby
import math


def sorted_history(entries, newest_first=True):
    """Keep chronology/day groups; order adjacent runs of one anime numerically."""
    ordered = sorted(entries, key=lambda e: e.get("last_watched") or 0, reverse=newest_first)
    resolver = MediaIdentity(ordered)
    def run_key(entry):
        stamp = entry.get("last_watched") or 0
        return datetime.fromtimestamp(stamp).date(), resolver.key(entry)
    def number(entry):
        try:
            return float(entry.get("episode"))
        except (ValueError, TypeError):
            return float("-inf")
    return [entry for _, run in groupby(ordered, run_key) for entry in sorted(run, key=number, reverse=True)]


def history_key(entry, resolver):
    identity = resolver.key(entry)
    episode = entry.get("episode")
    try:
        number = float(episode) if episode is not None else None
        if number is not None and math.isfinite(number):
            episode = int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        pass
    return (*identity, episode)


def combined_history(local, snapshot, references=(), cleared_before=0, dismissed=()):
    feed = list(feed_episodes(snapshot, newest_first=True, unique=False))
    resolver = MediaIdentity([*local, *references, *feed])
    metadata = {}
    for entry in [*feed, *references]:
        target = metadata.setdefault(resolver.key(entry), {})
        for field in ("title_english", "aliases", "season_year", "cover"):
            if entry.get(field):
                target[field] = entry[field]
    entries = {}
    hidden = {(*resolver.key({"title": key[1]}), key[2]) if key[0] == "title" else tuple(key)
              for key in dismissed}
    # Local playback always wins over the estimate, even if AniList was updated
    # later. Among local copies, keep the most recent playable URL and timing.
    for entry in sorted(local, key=lambda e: e.get("last_watched") or 0, reverse=True):
        key = history_key(entry, resolver)
        if key not in entries:
            entries[key] = dict(entry, title=resolver.title(entry), history_source="Local",
                                history_key=list(key))
            if key[0] == "anilist":
                entries[key]["anilist_id"] = key[1]
        else:
            target = entries[key]
            target["watch_seconds"] = target.get("watch_seconds", 0) + entry.get("watch_seconds", 0)
            for field in ("url", "website", "cover", "current_time", "duration", "episode_title"):
                if not target.get(field) and entry.get(field):
                    target[field] = entry[field]
    local_keys = set(entries)
    matched_feed = set()
    for event in feed:
        key = history_key(event, resolver)
        if key in local_keys:
            entry = entries[key]
            entry.setdefault("anilist_id", event["media_id"])
            entry["feed_activity_ids"] = list(dict.fromkeys(
                [*(entry.get("feed_activity_ids") or []), event["activity_id"]]))
            entry["feed_last_updated"] = max(entry.get("feed_last_updated", 0), event["last_watched"])
            if not entry.get("cover"):
                entry["cover"] = event["cover"]
            continue
        if key in matched_feed:
            key = ("activity", event["activity_id"], event["episode"])
        else:
            matched_feed.add(key)
        if key in entries:
            entry = entries[key]
            entry.setdefault("anilist_id", event["media_id"])
            if not entry.get("cover"):
                entry["cover"] = event["cover"]
            continue
        if event["last_watched"] <= cleared_before or key in hidden:
            continue
        entries[key] = {"title": resolver.title(event), "episode": event["episode"],
                        "title_english": event.get("title_english") or "",
                        "aliases": event.get("aliases") or [], "season_year": event.get("season_year"),
                        "anilist_id": event["media_id"], "website": "AniList",
                        "url": event["url"] or f"https://anilist.co/anime/{event['media_id']}",
                        "cover": event["cover"], "last_watched": event["last_watched"],
                         "history_source": "AniList feed", "history_key": list(key)}
    # AniList also records completion, planning, pausing and dropping without
    # episode numbers. Keep those actual events without inventing watch dates.
    for activity in snapshot.get("items") or []:
        media = activity.get("media") or {}
        status = str(activity.get("status") or "").lower()
        if media.get("type") != "ANIME" or not media.get("id") or not activity.get("id"):
            continue
        if status in ("watched episode", "rewatched episode", "re-watched episode"):
            continue
        stamp = activity.get("createdAt") or 0
        key = ("activity", activity["id"], None)
        if stamp <= cleared_before or key in hidden or not status:
            continue
        titles = media.get("title") or {}
        covers = media.get("coverImage") or {}
        entries[key] = {"title": titles.get("english") or titles.get("romaji") or titles.get("native") or "Unknown",
                        "title_english": titles.get("english") or "", "anilist_id": media["id"],
                        "aliases": list(filter(None, titles.values())), "season_year": media.get("seasonYear"),
                        "episode": None, "activity_status": status.capitalize(), "website": "AniList",
                        "url": media.get("siteUrl") or f"https://anilist.co/anime/{media['id']}",
                        "cover": covers.get("extraLarge") or covers.get("large") or "",
                        "last_watched": stamp, "history_source": "AniList feed", "history_key": list(key)}
    for entry in entries.values():
        entry.update(metadata.get(resolver.key(entry), {}))
        entry["title"] = resolver.title(entry)
    return sorted_history(entries.values())
