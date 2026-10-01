# Sporteurope Relay — Design

**Date:** 2026-09-28
**Status:** Approved in brainstorming, pending written-spec review

## Goal

Watch live EC Kassel Huskies games from Sporteurope.tv on several TVs in the
same household at the same time, using one Sporteurope account that only
allows one concurrent stream.

The add-on fetches **one** stream from Sporteurope/Mux and shares it on the
local network, like an HDMI splitter in software.

## Non-goals (explicit boundaries)

- **No DRM circumvention.** If a stream carries DRM, the add-on refuses to relay it.
- **No redistribution outside the household.** The stream is never published to
  YouTube or any other platform. *Amended 2026-09-29 (user decision):* it may be
  reached through the user's Cloudflared tunnel, but only behind the add-on's
  `remote_password` (HTTP Basic auth, lockout after 10 failures); without a password
  every tunnel request is refused, and the LAN stays password-free. Accepted risk:
  Cloudflare's terms restrict video-heavy traffic on self-serve plans.
- **No parallel sessions.** Different games on different TVs at the same time is
  not supported; that would defeat the concurrency limit rather than share one stream.
- **No replays / VOD** (possible later extension).
- **No purchasing.** The add-on only plays games the account already has access to.

## Known risk

Sporteurope's terms of use likely forbid restreaming. The practical risk is an
account ban. Mitigation: the upstream traffic looks like one normal viewer (one
login, one stream, the same API calls as the web player at the same cadence).
The user accepts this risk.

## Environment

- Home Assistant OS 18.2, KVM VM (board `ova`), amd64, LAN IP `192.168.0.90`.
- Supervisor available → runs as a custom Home Assistant add-on.
- Cloudflared add-on is installed; the relay may be routed through it only with `remote_password` set (see Non-goals).
- Viewing devices: Fire TV, Android / Google TV (VLC), Samsung / LG smart TVs (browser).

## Sporteurope API (from a HAR capture, 2026-09-28)

All calls go to `https://api.sporteurope.tv`, with headers `Origin` and
`Referer: https://sporteurope.tv/`.

| Purpose | Call | Notes |
|---|---|---|
| CSRF cookie | `GET /api/web/personal/csrf?lang=de` | Sets `XSRF-TOKEN` cookie |
| Login | `POST /api/web/auth/login?lang=de` body `{email, password}` | 201; cookie session; response includes `bought_products_and_asset_ids` |
| Upcoming / live games | `GET /api/web/public/next-livestreams?page&per_page&lang` | Paginated, all of Sporteurope (~1000); **not used**, Kassel is not in the first pages |
| Team profile id | `GET /api/web/public/profile-slugs/{team_slug}` | `{"profile_id": …}` (verified 2026-09-29) |
| Team's upcoming games | `GET /api/web/public/profiles/{profile_id}/next-livestreams?page&per_page&lang` | All upcoming LIVESTREAMs of the team (46 for the season); **used by the relay** |
| Personal feed | `GET /api/frontend/personal/feed?lang=de` | Contains team games incl. `monetizations`, `price_in_cents` |
| Game by slug | `GET /api/web/public/assets/{profile_slug}/{asset_slug}?lang=de` | Resolves the asset `id` |
| Stream info | `GET /api/web-player/personal/assets/{id}` | Headers `x-version`, `x-accept-language`. Returns `tracks[].sources[].hls` (signed Mux URL) and `mux.{playbackId, tokens.{playback, drm, …}}`. The web player polls this every **60 s** |
| Conference | `GET /api/web-player/personal/assets/{id}/conference` | Polled every 30 s by the web player; not needed for single-game playback |
| Stats event | `POST /api/statistics/e` | Header `x-xsrf-token`; not required |

Streams are Mux HLS: `stream.mux.com/{playbackId}.m3u8?token=…` → master
playlist → `rendition.m3u8` on `manifest-*.mux.com` → segments.

**DRM check (verified 2026-09-28):** two captures, a free livestream and the
paid replay "EHC Freiburg vs. EC Kassel Huskies" (unlocked via the account's
subscription, `analytics.used_monetization: "subscription"`). In both,
`tokens.drm` was `null`, playlists had no `#EXT-X-KEY` tags and no license
server was contacted, i.e. unencrypted HLS. Neither capture showed a dedicated
session/heartbeat endpoint.

**Still unverified:** a paid *live* Kassel game. It is expected to behave the
same, but how the one-stream limit is enforced remains unknown. Covered by the
Friday live test; the DRM guard stays in place regardless.

### Team filter

EC Kassel Huskies is the club with slug `ec-kassel-huskies`. A game belongs to
the team when `home_team.slug` or `guest_team.slug` equals the configured
slug. Only `type == "LIVESTREAM"` assets are listed.

## Architecture

One Python (aiohttp) process inside a Home Assistant add-on named
**Sporteurope Relay**, listening on port **8099** on the LAN.

| Module | Responsibility | Depends on |
|---|---|---|
| `config` | Loads add-on options (`email`, `password`, `team_slug` default `ec-kassel-huskies`, `max_height` default `1080`) from `/data/options.json` | — |
| `sporteurope_client` | Login + cookie/XSRF session, re-login once on 401, list team games with unlock status, fetch stream info, detect DRM | `config`, aiohttp |
| `hls_relay` | One upstream session per active game: picks one rendition (highest ≤ `max_height`), polls it, downloads each new segment **once**, keeps ~2 min in memory, serves a rewritten playlist whose segment URIs point to the relay; refreshes the signed URL every ~60 s via the client | `sporteurope_client` |
| `web` | HTTP routes: `/` TV page (game list + full-screen hls.js player), `/live.m3u8`, `/seg/{n}.ts`, `/api/status` | `hls_relay`, `sporteurope_client` |
| `ha_status` | Publishes `sensor.sporteurope_relay` (idle / live, game name, viewer count) via the Supervisor API | Supervisor token |

### Data flow

1. A TV opens `http://192.168.0.90:8099/` and sees upcoming and live Kassel games,
   each marked "unlocked" or "not purchased".
2. Selecting an unlocked live game starts the relay: stream info → DRM check →
   master playlist → chosen rendition.
3. The relay polls the rendition, fetches new segments once, and appends them to
   its buffer.
4. All TVs read `/live.m3u8` (VLC) or the player page (hls.js) and get segments
   from the buffer. Upstream traffic is independent of the number of TVs.
5. When no TV has requested the playlist or a segment for ~2 min, the relay stops
   and releases the upstream session.

Only one game can be active at a time. Selecting a different game while one is
running switches the relay for every TV.

### Viewing endpoints

*Added 2026-10-01 (user request):* **AirPlay and Chromecast, also through the domain.**
Receivers fetch the stream themselves and cannot send the password, so logged-in viewers get
a signed, expiring link (`/api/stream-url` → `live.m3u8?t=<expiry>.<hmac>`, 6 h, per-process
secret). The token opens only `/live.m3u8` and `/seg/*`, never the API or the page; invalid
tokens count toward the lockout. Stream responses send `Access-Control-Allow-Origin: *` for the
Chromecast receiver. Safari plays natively (needed for AirPlay). Chromecast uses Google's Default
Media Receiver; its sender SDK is loaded from gstatic.com only on `https` pages in Chrome — a
deliberate exception to "no CDN dependency": the page and local playback work without it.

- **Fire TV / Android TV:** VLC network stream `http://192.168.0.90:8099/live.m3u8`
  (always the active game).
- **Samsung / LG:** browser bookmark `http://192.168.0.90:8099/`, remote-friendly
  UI (large focusable tiles, arrow-key navigation), hls.js player served locally,
  no CDN dependency.

## Error handling

| Situation | Relay behaviour | TV shows |
|---|---|---|
| Wrong credentials | Stop, no retry loop | "Login fehlgeschlagen – Zugangsdaten in HA prüfen" |
| Session expired | Re-login once, continue | Nothing |
| Game not purchased | Don't start | "🔒 Nicht gekauft" on the tile |
| `tokens.drm` non-null / DRM in playlist (`#EXT-X-KEY` with `SAMPLE-AES` or `KEYFORMAT`) | Refuse to relay | "Dieses Spiel ist DRM-geschützt – nicht unterstützt" |
| Signed token expired (403 from Mux) | Refresh stream info, retry the request once | Nothing |
| Network / Mux error | Exponential backoff for up to ~30 s; TVs play from the buffer | A short freeze at most |
| Sporteurope rejects the session (concurrency limit) | Stop, don't fight for the slot | "Stream wird an anderer Stelle genutzt" |
| Game not live yet | No upstream; the page polls every 30 s and switches automatically | Countdown to start |

The password is never logged. Logs never contain tokens or signed URLs (query
strings are stripped).

## Testing

- **Unit tests (pytest):** playlist rewriting, segment buffer (fetch-once,
  expiry), team filter, unlock status, DRM detection, token-refresh logic.
  Fixtures are anonymised API responses from the HAR capture. No test touches
  the real account.
- **Integration test:** a fake Sporteurope API + fake Mux server; 3 simulated TV
  clients; asserts each upstream segment is fetched exactly once and the relay
  stops after the idle timeout.
- **Manual verification with the user:**
  1. Before building the relay: a spike that logs in and fetches stream info for a
     paid Kassel replay the user owns; check `tokens.drm` and the playlist for DRM.
     If DRM is present, stop the project.
  2. **Fri 2 Oct 2026, 19:00:** EC Kassel Huskies vs. Eispiraten Crimmitschau,
     live on two TVs (VLC on Fire TV + Samsung/LG browser) at the same time.

## Deployment

A Home Assistant add-on repository (`repository.yaml` + `sporteurope_relay/`
with `config.yaml`, `Dockerfile`, source). It can be installed either as a
GitHub repository added in the add-on store or by copying the folder into
`/addons` on the HA host. The exact method is chosen in the implementation plan.
