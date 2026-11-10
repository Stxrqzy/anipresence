"""A deduplicated history view; AniList dates remain explicitly feed dates."""
from .activity_stats import feed_episodes
from .media_identity import MediaIdentity


def history_key(entry, resolver):
    identity = resolver.key(entry)
    episode = entry.get("episode")
    try:
        episode = int(episode) if episode is not None else None
    except (TypeError, ValueError):
        pass
    return (*identity, episode)


def combined_history(local, snapshot, references=(), cleared_before=0, dismissed=()):
    feed = list(feed_episodes(snapshot, newest_first=True))
    resolver = MediaIdentity([*local, *references, *feed])
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
            for field in ("url", "website", "cover", "current_time", "duration"):
                if not target.get(field) and entry.get(field):
                    target[field] = entry[field]
    for event in feed:
        key = history_key(event, resolver)
        if key in entries:
            entry = entries[key]
            entry.setdefault("anilist_id", event["media_id"])
            if not entry.get("cover"):
                entry["cover"] = event["cover"]
            continue
        if event["last_watched"] <= cleared_before or key in hidden:
            continue
        entries[key] = {"title": resolver.title(event), "episode": event["episode"],
                        "anilist_id": event["media_id"], "website": "AniList",
                        "url": event["url"] or f"https://anilist.co/anime/{event['media_id']}",
                        "cover": event["cover"], "last_watched": event["last_watched"],
                        "history_source": "AniList feed", "history_key": list(key)}
    return sorted(entries.values(), key=lambda e: e.get("last_watched") or 0, reverse=True)
