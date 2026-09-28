from relay.games import Game


def make_game(game_id, *, live=True, unlocked=True, name="Kassel – Crimmitschau"):
    return Game(id=game_id, slug=f"slug-{game_id}", profile_slug="del2", name=name, home="EC Kassel Huskies",
                guest="Eispiraten Crimmitschau", start=None, live=live, free=False, unlocked=unlocked)
