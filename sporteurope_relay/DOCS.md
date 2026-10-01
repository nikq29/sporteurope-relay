# Sporteurope Relay

Holt **einen** Sporteurope-Livestream und verteilt ihn im Heimnetz an mehrere Fernseher.

## Einrichtung
1. E-Mail und Passwort des Sporteurope-Kontos eintragen, speichern, Add-on starten.
2. Fernseher:
   - **Fire TV / Android TV (VLC):** Netzwerkstream `http://192.168.0.90:8099/live.m3u8`
   - **Samsung / LG (Browser):** Lesezeichen `http://192.168.0.90:8099/`

## Zugriff über die eigene Domain (optional)
Über den Cloudflare-Tunnel erreichbar, aber nur mit Passwort:
1. Im Tab *Konfiguration* ein **Passwort für Zugriff über die Domain** setzen und das Add-on neu starten.
2. Im Cloudflared-Add-on eine zusätzliche Route anlegen, z. B. `huskies.deine-domain.de` → `http://192.168.0.90:8099`.
3. Browser: `https://huskies.deine-domain.de/` – Benutzername beliebig, dazu das Passwort.
   VLC: `https://huskies:PASSWORT@huskies.deine-domain.de/live.m3u8`

## AirPlay und Chromecast
- **AirPlay** (iPhone, iPad, Mac mit Safari): Spiel starten, dann AirPlay-Knopf oben rechts oder in der Wiedergabeleiste.
  Funktioniert im Heimnetz und über die Domain.
- **Chromecast** (Chrome am Laptop/Android): nur über die Domain (`https://…`), da Chromecast eine
  verschlüsselte Verbindung verlangt. Spiel starten, dann Cast-Symbol oben rechts.
- Empfänger bekommen einen Link mit Zugangsschlüssel (6 Std. gültig), weil sie kein Passwort eingeben
  können. Der Link öffnet nur den Stream, nicht die Spielauswahl. Ein Neustart des Add-ons macht alle
  Links ungültig.

Ohne gesetztes Passwort lehnt das Add-on jede Anfrage über den Tunnel ab. Nach 10 Fehlversuchen
wird die jeweilige Adresse für 10 Minuten gesperrt. Im Heimnetz ist kein Passwort nötig.

## Grenzen
- Immer nur ein Spiel gleichzeitig; ein Spielwechsel gilt für alle Fernseher.
- DRM-geschützte Streams werden nicht unterstützt.
- Ohne Zuschauer beendet das Add-on den Stream nach 2 Minuten.
