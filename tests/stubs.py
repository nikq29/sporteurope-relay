from relay.games import Game


def make_game(game_id, *, live=True, unlocked=True, name="Kassel – Crimmitschau"):
    return Game(id=game_id, slug=f"slug-{game_id}", profile_slug="del2", name=name, home="EC Kassel Huskies",
                guest="Eispiraten Crimmitschau", start=None, live=live, free=False, unlocked=unlocked)


class StubClient:
    def __init__(self, games=(), error=None):
        self.games = list(games)
        self.error = error

    async def list_games(self):
        self.calls = getattr(self, "calls", 0) + 1
        if self.error:
            raise self.error
        return self.games

    @property
    def login_failed(self):
        return type(self.error).__name__ == "LoginFailed"


class StubRelay:
    def __init__(self):
        self.game = None
        self.state = "idle"
        self.error = None
        self.started = []
        self.fail = None
        self.text = None
        self.segments = {}
        self.touched = []

    async def start(self, game):
        if self.fail:
            self.state, self.error = "error", self.fail.code
            raise self.fail
        self.started.append(game.id)
        self.game, self.state = game, "live"

    async def stop(self):
        self.game, self.state = None, "idle"

    def touch(self, ip):
        self.touched.append(ip)

    def playlist_text(self):
        return self.text

    def segment(self, seq):
        return self.segments.get(seq)

    def viewers(self):
        return len(set(self.touched))

    def status(self):
        return {"state": self.state, "game": self.game.to_json() if self.game else None,
                "viewers": self.viewers(), "error": self.error}
