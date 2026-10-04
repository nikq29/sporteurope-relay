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
  var streamUrl = null; // signed link: receivers (AirPlay, Chromecast, VLC) can't send the password
  var castContext = null;
  var APPLE = !!window.WebKitPlaybackTargetAvailabilityEvent; // Safari: play natively so AirPlay works
  // Android Chrome casts only natively played video (Remote Playback API); hls.js/MediaSource can't be cast.
  var ANDROID = /Android/i.test(navigator.userAgent);
  var NATIVE = (APPLE || ANDROID) && !!video.canPlayType("application/vnd.apple.mpegurl");
  var airplayButton = document.getElementById("airplay-button");
  var remoteButton = document.getElementById("remote-button");
  var castButton = document.getElementById("cast-button");
  var vlcUrl = document.getElementById("vlc-url");

  vlcUrl.textContent = location.origin + "/live.m3u8";

  function refreshStreamUrl() {
    return api("GET", "/api/stream-url").then(function (data) {
      if (data.url) {
        streamUrl = data.url;
        vlcUrl.textContent = streamUrl;
      }
    }).catch(function () {});
  }

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
    if (NATIVE) {
      video.src = streamUrl || "/live.m3u8";
      watchRemotePlayback();
    } else if (window.Hls && window.Hls.isSupported()) {
      hls = new window.Hls({ liveSyncDurationCount: 3, manifestLoadingMaxRetry: 10, manifestLoadingRetryDelay: 2000 });
      hls.on(window.Hls.Events.ERROR, function (event, data) {
        if (!data.fatal) return;
        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) { hls.recoverMediaError(); return; }
        setTimeout(function () { if (!player.hidden) attach(); }, 3000);
      });
      hls.loadSource(streamUrl || "/live.m3u8");
      hls.attachMedia(video);
    } else {
      video.src = streamUrl || "/live.m3u8";
    }
    var started = video.play();
    if (started && started.catch) started.catch(function () {});
  }

  function openPlayer(id) {
    playingGameId = id;
    home.hidden = true;
    player.hidden = false;
    playerMessage.textContent = "";
    refreshStreamUrl().then(function () {
      attach();
      if (castContext && castContext.getCurrentSession()) castCurrent();
    });
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
      if (s.state === "ended") { playerMessage.textContent = s.message; return; } // play out the rest
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

  // --- Android: Chromecast via the browser's own cast picker (Remote Playback API) ---
  var remoteWatched = false;
  function watchRemotePlayback() {
    if (APPLE || remoteWatched || !video.remote || !video.remote.watchAvailability) return;
    remoteWatched = true;
    video.remote.watchAvailability(function (available) { remoteButton.hidden = !available; })
      .catch(function () { remoteButton.hidden = false; }); // availability unknown: let the picker decide
  }
  remoteButton.addEventListener("click", function () {
    video.remote.prompt().catch(function (err) {
      if (err && err.name !== "AbortError") playerMessage.textContent = "Kein Cast-Gerät gefunden";
    });
  });
  if (video.remote) {
    video.remote.addEventListener("connect", function () { playerMessage.textContent = "Läuft auf dem Fernseher"; });
    video.remote.addEventListener("disconnect", function () { playerMessage.textContent = ""; });
  }

  // --- AirPlay (Safari / iOS) ---
  if (APPLE) {
    video.addEventListener("webkitplaybacktargetavailabilitychanged", function (e) {
      airplayButton.hidden = e.availability !== "available";
    });
    airplayButton.addEventListener("click", function () { video.webkitShowPlaybackTargetPicker(); });
  }

  // --- Chromecast (Chrome, only on https: Cast and the receiver need a secure stream URL) ---
  function castCurrent() {
    var session = castContext && castContext.getCurrentSession();
    if (!session || !streamUrl || player.hidden) return;
    var media = window.chrome.cast.media;
    var info = new media.MediaInfo(streamUrl, "application/x-mpegurl");
    info.streamType = media.StreamType.LIVE;
    if (media.HlsSegmentFormat) info.hlsSegmentFormat = media.HlsSegmentFormat.TS;
    if (media.HlsVideoSegmentFormat) info.hlsVideoSegmentFormat = media.HlsVideoSegmentFormat.MPEG2_TS;
    info.metadata = new media.GenericMediaMetadata();
    var game = games.filter(function (g) { return g.id === playingGameId; })[0];
    info.metadata.title = game ? game.home + " – " + game.guest : document.title;
    var request = new media.LoadRequest(info);
    request.autoplay = true;
    session.loadMedia(request).then(function () {
      video.pause();
      playerMessage.textContent = "Läuft auf " + session.getCastDevice().friendlyName;
    }, function () { playerMessage.textContent = "Chromecast konnte den Stream nicht starten"; });
  }

  window.__onGCastApiAvailable = function (available) {
    if (!available) return;
    var framework = window.cast.framework;
    castContext = framework.CastContext.getInstance();
    castContext.setOptions({
      receiverApplicationId: window.chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID,
      autoJoinPolicy: window.chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED
    });
    castButton.hidden = false;
    castContext.addEventListener(framework.CastContextEventType.SESSION_STATE_CHANGED, function (e) {
      var states = framework.SessionState;
      if (e.sessionState === states.SESSION_STARTED || e.sessionState === states.SESSION_RESUMED) {
        refreshStreamUrl().then(castCurrent);
      } else if (e.sessionState === states.SESSION_ENDED) {
        playerMessage.textContent = "";
        if (!player.hidden) attach();
      }
    });
  };

  if (location.protocol === "https:" && window.chrome) {
    var castScript = document.createElement("script");
    castScript.src = "https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1";
    document.head.appendChild(castScript);
  }

  video.addEventListener("ended", function () { if (!player.hidden) closePlayer("Spiel beendet"); });

  document.getElementById("back-button").addEventListener("click", function () { closePlayer(); });

  refreshStreamUrl();
  setInterval(refreshStreamUrl, 60 * 60 * 1000); // links last 6 h; keep a fresh one around
  loadGames();
  setInterval(function () { if (player.hidden) loadGames(); }, GAMES_POLL_MS);
})();
