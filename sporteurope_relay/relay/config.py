"""Add-on options written by the Supervisor to /data/options.json."""
import json
from dataclasses import dataclass, field

OPTIONS_PATH = "/data/options.json"


@dataclass(frozen=True)
class Config:
    email: str
    password: str = field(repr=False)
    team_slug: str = "ec-kassel-huskies"
    max_height: int = 1080


def load_config(path: str = OPTIONS_PATH) -> Config:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return Config(
        email=raw["email"],
        password=raw["password"],
        team_slug=raw.get("team_slug") or "ec-kassel-huskies",
        max_height=int(raw.get("max_height") or 1080),
    )
