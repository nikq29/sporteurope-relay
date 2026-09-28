# Sporteurope Relay

Holt **einen** Sporteurope-Livestream und verteilt ihn im Heimnetz an mehrere Fernseher.

## Einrichtung
1. E-Mail und Passwort des Sporteurope-Kontos eintragen, speichern, Add-on starten.
2. Fernseher:
   - **Fire TV / Android TV (VLC):** Netzwerkstream `http://192.168.0.90:8099/live.m3u8`
   - **Samsung / LG (Browser):** Lesezeichen `http://192.168.0.90:8099/`

## Grenzen
- Nur im Heimnetz. Port 8099 **nicht** im Cloudflared-Add-on freigeben.
- Immer nur ein Spiel gleichzeitig; ein Spielwechsel gilt für alle Fernseher.
- DRM-geschützte Streams werden nicht unterstützt.
- Ohne Zuschauer beendet das Add-on den Stream nach 2 Minuten.
