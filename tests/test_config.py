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
