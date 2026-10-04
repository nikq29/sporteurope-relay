import json

from relay.config import Config, load_config


def test_load_config_applies_defaults(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"email": "fan@example.org", "password": "pw"}))
    assert load_config(str(path)) == Config(
        email="fan@example.org", password="pw", team_slug="ec-kassel-huskies", max_height=1080
    )


def test_load_config_reads_overrides(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"email": "a@b.de", "password": "pw", "team_slug": "other", "max_height": 720}))
    cfg = load_config(str(path))
    assert (cfg.team_slug, cfg.max_height) == ("other", 720)


def test_config_repr_hides_password():
    assert "geheim" not in repr(Config(email="a@b.de", password="geheim"))


def test_remote_password_defaults_to_empty_and_is_hidden(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"email": "a@b.de", "password": "pw"}))
    assert load_config(str(path)).remote_password == ""
    path.write_text(json.dumps({"email": "a@b.de", "password": "pw", "remote_password": "tor-kassel"}))
    cfg = load_config(str(path))
    assert cfg.remote_password == "tor-kassel"
    assert "tor-kassel" not in repr(cfg)


def test_title_defaults_to_huskies_live(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"email": "a@b.de", "password": "pw"}))
    assert load_config(str(path)).title == "Huskies live"
    path.write_text(json.dumps({"email": "a@b.de", "password": "pw", "title": "Eishockey bei Niko"}))
    assert load_config(str(path)).title == "Eishockey bei Niko"
