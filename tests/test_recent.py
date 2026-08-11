"""RecentHistory add/dedupe/remove/clear behavior."""
from src.core.recent import RecentHistory


def _make(entry):
    return {
        "title": entry[0],
        "episode": entry[1],
        "website": entry[2],
        "url": entry[3],
    }


def test_add_dedupes_by_key(tmp_path):
    hist = RecentHistory(path=tmp_path / "recent.json")
    hist.add(_make(("One Piece", 1104, "Miruro", "https://a")))
    hist.add(_make(("one piece", 1104, "miruro", "https://b")))
    assert len(hist.items()) == 1


def test_remove_drops_only_matching_key(tmp_path):
    hist = RecentHistory(path=tmp_path / "recent.json")
    hist.add(_make(("One Piece", 1104, "Miruro", "https://a")))
    hist.add(_make(("Frieren", 2, "Crunchyroll", "https://b")))
    hist.remove(_make(("One Piece", 1104, "Miruro", "https://a")))
    items = hist.items()
    assert len(items) == 1
    assert items[0]["title"] == "Frieren"


def test_remove_noop_for_missing_key(tmp_path):
    hist = RecentHistory(path=tmp_path / "recent.json")
    hist.add(_make(("One Piece", 1104, "Miruro", "https://a")))
    hist.remove(_make(("Ghost", 1, "site", "https://x")))
    assert len(hist.items()) == 1


def test_clear_removes_everything(tmp_path):
    hist = RecentHistory(path=tmp_path / "recent.json")
    hist.add(_make(("One Piece", 1104, "Miruro", "https://a")))
    hist.clear()
    assert hist.items() == []
