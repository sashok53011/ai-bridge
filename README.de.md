# AI Bridge — Einfache Anleitung

AI Bridge ist ein kleines Programm, das auf Ihrem Computer läuft. Es gibt
Ihren Apps **eine einzige Adresse**, über die sie mit vielen verschiedenen
KI-Anbietern sprechen können (OpenAI, Groq, Ollama und andere). Sie
geben Ihre API-Schlüssel einmal ein, und AI Bridge wählt den richtigen
aus und leitet die Anfrage weiter.

Sie müssen kein Programmierer sein. Die ganze Steuerung läuft über eine
einfache Webseite im Browser.

## Was Sie brauchen

- Einen Computer mit Windows, macOS oder Linux.
- Eine Internetverbindung (nur zum Herunterladen und für Cloud-Anbieter).
- Einen API-Schlüssel von mindestens einem KI-Anbieter (OpenAI, Groq, …).
  Lokale Modelle (Ollama) brauchen keinen Schlüssel.

## Schnellstart (3 Schritte)

### 1. Installieren

Projekt herunterladen und im Terminal in den Ordner wechseln:

```
uv sync
```

Das installiert alles Nötige. Kein Compiler, kein Build-Schritt.

### 2. Einmalig anmelden

Konto auf dem Katalog-Server erstellen, dann Login und Passwort ins
Programm eintragen. Am einfachsten über zwei Umgebungsvariablen
(PowerShell-Beispiel):

```
$env:AI_BRIDGE_EMAIL = "sie@example.com"
$env:AI_BRIDGE_PASSWORD = "ihr-passwort"
```

Oder `config.example.toml` nach `config.toml` kopieren und die Zeilen
`email` und `password` ausfüllen.

### 3. Dienst starten

```
uv run python app.py --serve
```

Eine Webseite öffnet sich unter `http://127.0.0.1:8080/`. Das ist Ihr
Bedienfeld.

## App verbinden

Ihre Apps (ChatGPT-Clients, IDE-Plugins, Skripte) sprechen mit AI Bridge
statt direkt mit einem Anbieter. Geben Sie ihnen:

- **Adresse:** `http://127.0.0.1:8080/v1`
- **Token:** `uv run python app.py --token` ausführen und den Wert
  kopieren.

Der Token ist wie ein Passwort für Ihren lokalen Dienst — halten Sie ihn
geheim.

Dann die App wie gewohnt nutzen. AI Bridge leitet jede Anfrage an den
besten verfügbaren Anbieter weiter und wechselt automatisch, wenn einer
ausfällt.

## Bedienfeld (Webseite)

Öffnen Sie `http://127.0.0.1:8080/` im Browser und melden Sie sich an.
Sie sehen:

- **Katalog** — alle Anbieter. Ein-/ausschalten, eigene hinzufügen
  (Ollama auf einem anderen PC, LM Studio usw.).
- **Schlüssel** — API-Schlüssel für einen Anbieter eingeben. Er bleibt
  auf Ihrem Gerät; die Seite zeigt nur „gesetzt / nicht gesetzt“.
- **Prüfen** — Anbieter testen und sehen, ob er antwortet.
- **Statistik** — wie viele Anfragen Sie gemacht haben, Token verbraucht,
  welche Modell geantwortet hat.
- **Journal** (nur Admin) — Protokoll jeder Anfrage mit Anfrage- und
  Antworttext (standardmäßig eingeklappt, zum Ausklappen klicken).

## Sicherheit

- Ihre API-Schlüssel werden **verschlüsselt auf Ihrem Gerät**
  gespeichert. Sie werden nie an den Katalog-Server gesendet.
- Der lokale Dienst fragt bei jeder Anfrage nach einem Token. Auf Ihrem
  eigenen Rechner passiert das automatisch; über das Internet müssen Sie
  HTTPS oder ein VPN (Tailscale empfohlen) nutzen.
- Teilen Sie Ihren Token nicht. Behandeln Sie ihn wie ein Passwort.

## Hilfe

- `CONTEXT.md` lesen für das Gesamtbild.
- `uv run pytest` ausführen, um zu prüfen, dass alles funktioniert (alle
  Tests laufen offline).
- Bei Fehlern ein Issue auf GitHub öffnen.
