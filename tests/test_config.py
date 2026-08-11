"""Config persistence: round-trip, unknown-key tolerance and redaction."""
import json

from src.config import ConfigManager, redact_urls


def test_roundtrip_new_ui_fields(tmp_path):
    path = tmp_path / "config.json"
    cfg = ConfigManager(str(path))
    cfg.set(
        theme="system",
        window_geometry="10,20,900,700",
        window_maximized=True,
        current_tab=1,
    )
    loaded = ConfigManager(str(path))
    assert loaded.data.theme == "system"
    assert loaded.data.window_geometry == "10,20,900,700"
    assert loaded.data.window_maximized is True
    assert loaded.data.current_tab == 1


def test_defaults_when_key_missing(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"theme": "light"}), encoding="utf-8")
    cfg = ConfigManager(str(path))
    assert cfg.data.window_geometry == ""
    assert cfg.data.window_maximized is False
    assert cfg.data.current_tab == 0
    assert cfg.data.mica_backdrop is True


def test_legacy_username_migrates_to_accounts(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"anilist_username": "NarutoFan"}), encoding="utf-8")
    cfg = ConfigManager(str(path))
    assert cfg.data.anilist_accounts == ["NarutoFan"]


def test_accounts_persist_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cfg = ConfigManager(str(path))
    cfg.set(anilist_accounts=["Alice", "Bob"], anilist_username="Bob")
    loaded = ConfigManager(str(path))
    assert loaded.data.anilist_accounts == ["Alice", "Bob"]
    assert loaded.data.anilist_username == "Bob"


def test_fresh_config_not_onboarded(tmp_path):
    cfg = ConfigManager(str(tmp_path / "config.json"))
    assert cfg.data.onboarding_complete is False


def test_prewizard_config_marks_onboarding_done(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    cfg = ConfigManager(str(path))
    assert cfg.data.onboarding_complete is True


def test_onboarding_flag_roundtrips(tmp_path):
    path = tmp_path / "config.json"
    cfg = ConfigManager(str(path))
    cfg.set(onboarding_complete=True)
    assert ConfigManager(str(path)).data.onboarding_complete is True


def test_unknown_keys_dropped(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"bogus_key": 1, "theme": "dark"}), encoding="utf-8")
    cfg = ConfigManager(str(path))
    assert not hasattr(cfg.data, "bogus_key")
    assert cfg.data.theme == "dark"


def test_corrupt_config_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ not json", encoding="utf-8")
    cfg = ConfigManager(str(path))
    assert cfg.data.theme == "dark"


def test_set_ignores_unknown_kwargs(tmp_path):
    cfg = ConfigManager(str(tmp_path / "config.json"))
    cfg.set(nonsense=123)  # unknown key must not raise
    assert cfg.data.theme == "dark"


def test_redact_urls():
    text = "watch at https://example.com/a?ep=5 then http://b.co/x"
    result = redact_urls(text)
    assert "https://example.com" not in result
    assert result.count("[url]") == 2


def test_cinematic_backdrop_defaults_and_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cfg = ConfigManager(str(path))
    assert cfg.data.cinematic_backdrop is True
    cfg.set(cinematic_backdrop=False)
    assert ConfigManager(str(path)).data.cinematic_backdrop is False