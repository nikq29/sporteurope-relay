(function () {
  "use strict";

  var GAMES_POLL_MS = 30000;
  var STATUS_POLL_MS = 10000;
  var BACK_KEYS = [8, 27, 461, 10009]; // Backspace, Esc, webOS back, Tizen back
  var DAYS = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];

  var home = document.getElementById("home");
  var grid = document.getElementById("games");
  var message = document.getElementById("message");
  var player = document.getElementById("player");
  var video = document.getElementById("video");
  var playerMessage = document.getElementById("player-message");

  var hls = null;
  var games = [];
  var armedId = null;
  var armedRetries = 0;
  var ARMED_MAX_RETRIES = 10; // with the 30 s poll: keep trying for ~5 min after start
  var RETRYABLE = ["upstream", "not_live", "not_purchased"]; // never retry drm / stream_in_use / login_failed
  var playingGameId = null;
  var statusTimer = null;

  document.getElementById("vlc-url").textContent = location.origin + "/live.m3u8";

  function api(method, path, body) {
    return fetch(path, {
      method: method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        data.httpOk = resp.ok;
        return data;
      });
    });
  }

  function pad(n) { return n < 10 ? "0" + n : "" + n; }

  function whenText(game) {
    if (game.live) return "● LIVE";
    if (!game.start) return "Termin offen";
    var start = new Date(game.start);
    var mins = Math.round((start.getTime() - Date.now()) / 60000);
    var label = DAYS[start.getDay()] + " " + pad(start.getDate()) + "." + pad(start.getMonth() + 1) + ". " +
      pad(start.getHours()) + ":" + pad(start.getMinutes());
    if (mins <= 0) return label + " · gleich";
    if (mins < 60) return label + " · in " + mins + " Min";
    if (mins < 48 * 60) return label + " · in " + Math.floor(mins / 60) + " Std " + (mins % 60) + " Min";
    return label + " · in " + Math.floor(mins / 1440) + " Tagen";
  }

  function render() {
    var focusedId = document.activeElement && document.activeElement.getAttribute("data-id");
    grid.innerHTML = "";
    if (!games.length) {
      grid.innerHTML = '<p class="hint">Keine anstehenden Spiele gefunden.</p>';
      return;
    }
    games.forEach(function (game) {
      var tile = document.createElement("button");
      tile.className = "tile" + (game.live ? " live" : "") + (game.unlocked === false ? " locked" : "");
      tile.setAttribute("data-id", game.id);
      var teams = document.createElement("div");
      teams.className = "teams";
      teams.textContent = game.home + " – " + game.guest;
      var when = document.createElement("div");
      when.className = "when";
      when.textContent = (game.unlocked === false ? "🔒 Nicht gekauft · " : "") + whenText(game) +
        (armedId === game.id && !game.live ? " · startet automatisch" : "");
      tile.appendChild(teams);
      tile.appendChild(when);
      tile.addEventListener("click", function () { select(game); });
      grid.appendChild(tile);
    });
    var target = (focusedId && grid.querySelector('[data-id="' + focusedId + '"]')) || grid.querySelector(".tile");
    if (target && player.hidden) target.focus();
  }

  function loadGames() {
    return api("GET", "/api/games").then(function (data) {
      games = data.games || [];
      message.textContent = data.message || "";
      render();
      if (armedId && player.hidden) {
        var armed = games.filter(function (g) { return g.id === armedId; })[0];
        if (armed && armed.live) {
          armedId = null;
          play(armed.id, true);
        }
      }
    }).catch(function () { message.textContent = "Relay nicht erreichbar"; });
  }

  function select(game) {
    if (!game.live) {
      armedId = game.id;
      armedRetries = 0;
      message.textContent = "Startet automatisch, sobald das Spiel live ist.";
      render();
      return;
    }
    play(game.id);
  }

  function play(id, fromArm) {
    message.textContent = "Stream wird gestartet …";
    api("POST", "/api/play", { game_id: id }).then(function (data) {
      if (!data.httpOk) {
        message.textContent = data.message || "Start fehlgeschlagen";
        // Game just went live but the stream isn't ready yet: try again on the next poll.
        if (fromArm && RETRYABLE.indexOf(data.error) >= 0 && armedRetries < ARMED_MAX_RETRIES) {
          armedRetries++;
          armedId = id;
          message.textContent += " – neuer Versuch in 30 s";
        }
        return;
      }
      message.textContent = "";
      openPlayer(id);
    }).catch(function () { message.textContent = "Relay nicht erreichbar"; });
  }

  function attach() {
    if (hls) { hls.destroy(); hls = null; }
    if (window.Hls && window.Hls.isSupported()) {
      hls = new window.Hls({ liveSyncDurationCount: 3, manifestLoadingMaxRetry: 10, manifestLoadingRetryDelay: 2000 });
      hls.on(window.Hls.Events.ERROR, function (event, data) {
        if (!data.fatal) return;
        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) { hls.recoverMediaError(); return; }
        setTimeout(function () { if (!player.hidden) attach(); }, 3000);
      });
      hls.loadSource("/live.m3u8");
      hls.attachMedia(video);
    } else {
      video.src = "/live.m3u8";
    }
    var started = video.play();
    if (started && started.catch) started.catch(function () {});
  }

  function openPlayer(id) {
    playingGameId = id;
    home.hidden = true;
    player.hidden = false;
    playerMessage.textContent = "";
    attach();
    clearInterval(statusTimer);
    statusTimer = setInterval(checkStatus, STATUS_POLL_MS);
  }

  function closePlayer(text) {
    clearInterval(statusTimer);
    if (hls) { hls.destroy(); hls = null; }
    video.removeAttribute("src");
    video.load();
    player.hidden = true;
    home.hidden = false;
    playingGameId = null;
    loadGames().then(function () { if (text) message.textContent = text; });
  }

  function checkStatus() {
    api("GET", "/api/status").then(function (s) {
      if (s.state === "error") { closePlayer(s.message); return; }
      if (s.state === "idle") { closePlayer("Stream beendet"); return; }
      if (s.game && s.game.id !== playingGameId) { // another TV switched the game
        playingGameId = s.game.id;
        attach();
      }
    });
  }

  function columns() {
    var tiles = grid.querySelectorAll(".tile");
    if (!tiles.length) return 1;
    var top = tiles[0].offsetTop, n = 0;
    for (var i = 0; i < tiles.length && tiles[i].offsetTop === top; i++) n++;
    return n || 1;
  }

  function moveFocus(delta) {
    var tiles = Array.prototype.slice.call(grid.querySelectorAll(".tile"));
    if (!tiles.length) return;
    var i = tiles.indexOf(document.activeElement);
    tiles[Math.max(0, Math.min(tiles.length - 1, (i < 0 ? 0 : i) + delta))].focus();
  }

  document.addEventListener("keydown", function (e) {
    if (!player.hidden) {
      if (BACK_KEYS.indexOf(e.keyCode) >= 0) { e.preventDefault(); closePlayer(); }
      return;
    }
    var cols = columns();
    if (e.keyCode === 37) moveFocus(-1);
    else if (e.keyCode === 39) moveFocus(1);
    else if (e.keyCode === 38) moveFocus(-cols);
    else if (e.keyCode === 40) moveFocus(cols);
    else return;
    e.preventDefault();
  });

  loadGames();
  setInterval(function () { if (player.hidden) loadGames(); }, GAMES_POLL_MS);
})();
