from datetime import date, datetime, timedelta

from src.core.history import combined_history
from src.core.watchlog import WatchLog


def snapshot(title="NARUTO", identity=20, progress="1 - 3", days_ago=1):
    stamp = datetime.combine(date.today()-timedelta(days=days_ago), datetime.min.time()).timestamp()
    return {"items": [{"id": 1, "createdAt": stamp, "status": "watched episode", "progress": progress,
                       "media": {"id": identity, "type": "ANIME", "title": {"romaji": title},
                                 "coverImage": {"large": "cover"}}}]}


def test_history_merges_aliases_and_keeps_local_timing_and_playable_url():
    feed = snapshot()
    feed["items"][0]["media"]["title"]["english"] = "Ninja story"
    local = [{"title": " ninja   story ", "episode": 2, "last_watched": 10,
              "url": "https://player/2", "current_time": 120, "duration": 1400, "watch_seconds": 100},
             {"title": "ｎａｒｕｔｏ", "episode": "2", "last_watched": 5, "watch_seconds": 30}]
    result = combined_history(local, feed)
    assert len(result) == 3
    assert {e["title"] for e in result} == {"Ninja story"}
    measured = next(e for e in result if e["history_source"] == "Local")
    assert measured["url"] == "https://player/2"
    assert measured["current_time"] == 120
    assert measured["watch_seconds"] == 130
    assert measured["last_watched"] == 10
    assert measured["cover"] == "cover"
    assert all("duration" not in e for e in result if e["history_source"] == "AniList feed")


def test_explicit_different_ids_and_ambiguous_title_stay_separate():
    local = [{"title": "NARUTO", "episode": 1, "anilist_id": 99},
             {"title": "naruto", "episode": 1}]
    result = combined_history(local, snapshot(progress="1"))
    assert len(result) == 3
    assert {tuple(e["history_key"]) for e in result} == {
        ("anilist", 99, 1), ("anilist", 20, 1), ("title", "naruto", 1)}


def test_title_only_history_uses_one_label_across_episodes():
    result = combined_history([{"title": "NARUTO", "episode": 1},
                               {"title": "naruto", "episode": 2}], {})
    assert {e["title"] for e in result} == {"NARUTO"}


def test_history_sorts_numeric_runs_without_crossing_other_anime_or_days():
    from src.core.history import sorted_history
    stamp = datetime.combine(date.today(), datetime.min.time()).timestamp()
    entries = [{"title": "A", "episode": 10, "last_watched": stamp+30},
               {"title": "a", "episode": 2, "last_watched": stamp+20},
               {"title": "B", "episode": 1, "last_watched": stamp+10},
               {"title": "A", "episode": 1, "last_watched": stamp+5},
               {"title": "A", "episode": 9, "last_watched": stamp-1}]
    assert [e["episode"] for e in sorted_history(entries)] == [10, 2, 1, 1, 9]
    assert [e["episode"] for e in sorted_history(entries, newest_first=False)] == [9, 1, 1, 10, 2]


def test_history_uses_latest_feed_date_and_resolves_old_title_dismissals():
    older = snapshot(progress="1", days_ago=2)["items"][0]
    newer = snapshot(progress="1", days_ago=1)["items"][0]
    feed = {"items": [older, newer]}
    assert combined_history([], feed)[0]["last_watched"] == newer["createdAt"]
    assert combined_history([], feed, dismissed=[["title", "naruto", 1]]) == []


def test_clear_and_dismiss_persist_without_changing_statistics(tmp_path):
    path = tmp_path / "watch.json"
    log = WatchLog(path)
    feed = snapshot()
    log.set_activity(feed)
    stats = log.analytics()
    selected = log.history([])[0]
    log.dismiss_history(selected)
    log = WatchLog(path)
    log.set_activity(feed)
    assert len(log.history([])) == 2
    assert log.analytics() == stats
    log.clear_feed_history()
    log = WatchLog(path)
    log.set_activity(feed)
    assert log.history([]) == []
    assert log.analytics() == stats
    # New local playback remains available after clearing older feed events.
    assert len(log.history([{"title": "naruto", "episode": 1}])) == 1
    # A later feed event is shown, without restoring the older episodes.
    newer = snapshot(progress="4", days_ago=0)["items"][0]
    newer["createdAt"] = log._history_cleared_before + 1
    log.set_activity({"items": [*feed["items"], newer]})
    assert [e["episode"] for e in log.history([])] == [4]


def test_stats_alias_merge_preserves_local_days_and_deduplicates_imports(tmp_path):
    log = WatchLog(tmp_path / "watch.json")
    log.add("naruto", 1, "site", "https://watch/1", "", 300)
    log.add(" NARUTO ", 1, "site", "https://watch/1", "", 200)
    log.import_titles([{"title": "NARUTO", "media_id": 20, "episode": 3, "seconds": 4320},
                       {"title": "Naruto", "media_id": 20, "episode": 3, "seconds": 4320}])
    log.set_activity(snapshot())
    stats = log.analytics()
    assert len(stats["titles"]) == 1
    assert stats["titles"][0]["local_seconds"] == 500
    assert stats["titles"][0]["seconds"] == 4320
    assert sum(stats["days"].values()) == 500 + 2880
    assert len(stats["episodes"]) == 3
    assert len(log.continue_list()) == 1
    log.remove_title(stats["titles"][0])
    assert log.analytics()["titles"] == []
    assert log.days() == {}


def test_same_title_different_ids_survive_storage_and_targeted_removal(tmp_path):
    log = WatchLog(tmp_path / "watch.json")
    log.add("Example", 1, "site", "", "", 100, media_id=1)
    log.add("Example", 1, "site", "", "", 200, media_id=2)
    log.import_titles([{"title": "Example", "media_id": 1, "seconds": 1440},
                       {"title": "Example", "media_id": 2, "seconds": 2880}])
    log = WatchLog(log._path)
    assert {e["media_id"] for e in log.analytics()["titles"]} == {1, 2}
    log.remove_title({"title": "Example", "media_id": 1})
    assert [e["media_id"] for e in log.analytics()["titles"]] == [2]
    assert sum(log.days().values()) == 200


def test_non_episode_feed_events_are_visible_and_dismissable(tmp_path):
    feed=snapshot(progress="",days_ago=400)
    feed["items"][0]["status"]="completed"
    log=WatchLog(tmp_path/"history.json");log.set_activity(feed)
    rows=log.history([])
    assert len(rows)==1 and rows[0]["activity_status"]=="Completed"
    assert rows[0]["title"]=="NARUTO" and rows[0]["episode"] is None
    assert not log.analytics()["episodes"]  # no invented dated episodes
    log.dismiss_history(rows[0])
    assert not log.history([])


def test_distinct_rewatch_events_remain_in_history_without_double_counting_stats(tmp_path):
    newer=snapshot(progress="1",days_ago=1)["items"][0]
    older=snapshot(progress="1",days_ago=400)["items"][0]
    older["id"]=2;newer["status"]="rewatched episode"
    log=WatchLog(tmp_path/"history.json");log.set_activity({"items":[newer,older]})
    rows=log.history([])
    assert len(rows)==2
    assert rows[0]["last_watched"]==newer["createdAt"]
    assert rows[1]["last_watched"]==older["createdAt"]
    assert len(log.analytics()["episodes"])==1
    log.dismiss_history(rows[1])
    assert len(log.history([]))==1


def test_local_episode_merges_all_feed_occurrences_and_dismisses_them(tmp_path):
    newer = snapshot(title="Frieren: Beyond Journey’s End", progress="2")["items"][0]
    older = snapshot(title="Frieren: Beyond Journey’s End", progress="2", days_ago=400)["items"][0]
    older["id"] = 2
    local = [{"title": "Frieren Beyond Journey's End", "episode": "2.0", "last_watched": 100,
              "url": "https://ani.pm/watch/2", "current_time": 350, "duration": 1400, "watch_seconds": 200}]
    log = WatchLog(tmp_path / "merged.json")
    log.set_activity({"items": [newer, older]})
    rows = log.history(local)
    assert len(rows) == 1
    assert set(rows[0]["feed_activity_ids"]) == {1, 2}
    assert rows[0]["url"] == local[0]["url"] and rows[0]["current_time"] == 350
    assert rows[0]["last_watched"] == 100
    log.dismiss_history(rows[0])
    assert not WatchLog(log._path).history([])
