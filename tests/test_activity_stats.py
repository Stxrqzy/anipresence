from datetime import date, datetime, timedelta

import pytest

from src.anime.anilist import AniListClient, AniListError
from src.core.activity_stats import feed_episodes
from src.core.watchlog import WatchLog


def event(identity=1, progress="1 - 3", days_ago=0, **changes):
    stamp = datetime.combine(date.today()-timedelta(days=days_ago), datetime.min.time()).timestamp()
    result = {"id": identity, "createdAt": stamp, "status": "watched episode", "progress": progress,
              "media": {"id": 42, "type": "ANIME", "title": {"romaji": "Example"}, "duration": 24}}
    result.update(changes)
    return result


def test_feed_ranges_and_overlaps_are_unique():
    values = list(feed_episodes({"items": [event(), event(2, "3 – 4"), event(2, "3 – 4")]}))
    assert [e["episode"] for e in values] == [1,2,3,4]
    assert sum(e["seconds"] for e in values) == 5760


@pytest.mark.parametrize("changes", [
    {"status": "plans to watch"}, {"status": "completed", "progress": ""},
    {"progress": "5 - 2"}, {"progress": "0"}, {"progress": "unknown"},
    {"createdAt": 0}, {"media": {"id": 1, "type": "MANGA"}},
])
def test_non_playback_or_invalid_activity_has_no_invented_timing(changes):
    assert list(feed_episodes({"items": [event(**changes)]})) == []


def test_local_episode_wins_across_dates_and_import_is_not_added_twice(tmp_path):
    log = WatchLog(tmp_path/'stats.json')
    log.add("Example", 2, "site", "", "", 300, media_id=42)
    log.import_titles([{"title": "Example", "media_id": 42, "seconds": 5760}])
    feed = {"items": [event(progress="1 - 4", days_ago=1)], "fetched_at": 123}
    log.set_activity(feed)
    first = log.analytics()
    assert first["titles"][0]["seconds"] == 5760
    assert sum(first["days"].values()) == 4620  # 3 estimates and 300 measured seconds
    assert len(first["episodes"]) == 4
    assert first["episodes"][(42,2)] == date.today().isoformat()
    log.set_activity(feed)
    assert log.analytics() == first
    assert WatchLog(tmp_path/'stats.json').analytics() == first
    # Edited grouped activity replaces its old range, rather than accumulating.
    log.set_activity({"items": [event(progress="1 - 2")]})
    assert sum(log.analytics()["days"].values()) == 1740


def test_legacy_daily_overlap_and_missing_duration(tmp_path):
    log = WatchLog(tmp_path/'stats.json')
    log.add("Example", None, "site", "", "", 600)
    item = event(progress="1")
    item["media"]["duration"] = None
    log.set_activity({"items": [item]})
    assert sum(log.analytics()["days"].values()) == 1440
    assert log.analytics()["titles"][0]["estimated_seconds"] == 840
    log.clear()
    assert log.analytics()["episodes"] == {}
    assert log.analytics()["titles"] == []


def test_new_episode_after_list_snapshot_adds_only_new_progress(tmp_path):
    log = WatchLog(tmp_path/'stats.json')
    log.import_titles([{"title": "Example", "media_id": 42, "seconds": 5760, "episode": 4}])
    log.add("Example", 5, "site", "", "", 300, media_id=42)
    log.set_activity({"items": [event(progress="1 - 6")]})
    assert log.analytics()["titles"][0]["seconds"] == 5760+300+1440
    # Once the list catches up, its new total replaces the earlier baseline.
    log.import_titles([{"title": "Example", "media_id": 42, "seconds": 8640, "episode": 6}])
    assert log.analytics()["titles"][0]["seconds"] == 8640


def test_feed_only_title_can_be_removed(tmp_path):
    log = WatchLog(tmp_path/'stats.json')
    log.set_activity({"items": [event()]})
    log.remove_title("Example")
    assert not log.analytics()["titles"]


def test_activity_pagination_deduplicates_ids_and_reports_truncation(monkeypatch):
    client = AniListClient()
    calls = []
    def query(text, variables):
        if "User(name:" in text:
            return {"User": {"id": 7, "name": "Alice"}}
        calls.append(variables)
        return {"Page": {"pageInfo": {"hasNextPage": True}, "activities": [event(1), event(variables["page"]+1)]}}
    monkeypatch.setattr(client, "_query", query)
    result = client.fetch_user_activity("Alice", max_pages=2)
    assert [v["page"] for v in calls] == [1,2]
    assert all(v["userId"] == 7 for v in calls)
    assert len(result["items"]) == 3
    assert result["truncated"]
    assert result["account"] == "Alice"


def test_activity_pagination_failure_does_not_return_partial_snapshot(monkeypatch):
    client = AniListClient()
    def query(text, variables):
        if "User(name:" in text:
            return {"User": {"id": 7}}
        if variables["page"] == 2:
            raise AniListError("rate limited")
        return {"Page": {"pageInfo": {"hasNextPage": True}, "activities": [event()]}}
    monkeypatch.setattr(client, "_query", query)
    with pytest.raises(AniListError, match="rate limited"):
        client.fetch_user_activity("Alice")
