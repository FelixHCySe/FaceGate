# FaceGate – Gesichtserkennung mit Zugangskontrolle auf dem Raspberry Pi 5

Erkennt registrierte Personen über die Pi Camera Module 3, zeigt **Name, Alter und
Berechtigungen** live auf dem Bildschirm, öffnet bei passender Berechtigung eine Tür
über ein Relais und protokolliert jeden Zutritt. Verwaltet wird alles über ein
passwortgeschütztes Web-Dashboard.

```
Kamera ─► YuNet (Gesicht finden) ─► SFace (128-D-Merkmale) ─► Vergleich mit Datenbank
                                                                   │
              Bildschirm-Overlay ◄─ Name / Alter / Rechte ◄─────────┤
              Relais (GPIO17)    ◄─ Recht „tuer" + 4 Bilder in Folge ┤
              Zugangsprotokoll   ◄─────────────────────────────────┘
```

| Datei | Aufgabe |
|---|---|
| `main.py` | Hauptschleife: Kamera, Erkennung, Overlay, Tür, startet das Dashboard |
| `recognizer.py` | YuNet + SFace (OpenCV), Abgleich per Kosinus-Ähnlichkeit |
| `database.py` | SQLite: Personen, Merkmalsvektoren, Protokoll |
| `hardware.py` | Pi-Kamera (Fallback USB/Video) und Relais (Fallback Simulation) |
| `web.py` + `templates/` | Dashboard: Live-Bild, Personen, Protokoll, CSV-Export |
| `cli.py` | Passwort setzen, Personen per Kommandozeile anlegen/auflisten/löschen |
| `config.py` | Alle Einstellungen, per Umgebungsvariable überschreibbar |

## Hardware

- Raspberry Pi 5 (4 GB reichen), Raspberry Pi OS 64-bit
- Pi Camera Module 3 – **Achtung:** der Pi 5 hat kleinere Kamerabuchsen, du brauchst das 22-auf-15-Pin-Kabel
- 1-Kanal-Relaismodul 5 V (für den Türöffner), optional Monitor per HDMI

**Relais anschließen**

| Relaismodul | Raspberry Pi |
|---|---|
| VCC | Pin 2 (5 V) |
| GND | Pin 6 (GND) |
| IN  | Pin 11 (GPIO17) |

Der Türöffner hängt an COM/NO des Relais mit **eigenem Netzteil** (nie vom Pi versorgen).
Viele günstige Relaismodule schalten bei LOW – wenn das Relais „verkehrt herum" klickt:
`FACEGATE_RELAY_ACTIVE_HIGH=0`.

## Installation

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-gpiozero python3-venv git
rpicam-hello --list-cameras          # Kamera muss hier auftauchen

cd ~ && git clone <dein-repo> facegate && cd facegate   # oder ZIP entpacken
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x download_models.sh && ./download_models.sh

python cli.py set-password            # Admin-Passwort fürs Dashboard (min. 10 Zeichen)
```

## Benutzung

```bash
python main.py                # Vollbild auf dem Monitor + Dashboard (q / Esc beendet)
python main.py --headless     # ohne Monitor
```

Dashboard: `http://<IP-des-Pi>:8080` (IP mit `hostname -I`).

**Personen anlegen** – am besten im Dashboard unter *Personen → + Person anlegen*:
Name, Geburtsdatum, Berechtigungen (z. B. `tuer, labor`) und 3–5 Fotos aus leicht
unterschiedlichen Winkeln. Alternativ „aktuelles Kamerabild verwenden" anhaken, während
die Person vor der Kamera steht. Oder per Kommandozeile:

```bash
python cli.py add "Max Muster" --birthdate 2001-05-14 --permissions tuer,labor --camera 5
python cli.py list
```

Das Alter wird aus dem Geburtsdatum berechnet. Die Berechtigung **`tuer`** öffnet die Tür,
alle anderen sind frei wählbar und werden nur angezeigt. Farben im Overlay:
grün = Zutritt, orange = bekannt ohne Türrecht, rot = unbekannt.

## Autostart

```bash
sudo cp facegate.service /etc/systemd/system/   # vorher User/Pfade prüfen
sudo systemctl daemon-reload
sudo systemctl enable --now facegate
journalctl -u facegate -f                       # Logs ansehen
```

## Feinjustierung

| Variable | Standard | Wirkung |
|---|---|---|
| `FACEGATE_MATCH_THRESHOLD` | 0.42 | höher = strenger (weniger Verwechslungen, öfter „unbekannt") |
| `FACEGATE_CONFIRM_FRAMES` | 4 | Bilder in Folge bis zur Entscheidung |
| `FACEGATE_UNLOCK_SECONDS` | 5 | wie lange das Relais anzieht |
| `FACEGATE_RELAY_PIN` | 17 | GPIO (BCM-Nummer) |
| `FACEGATE_MIN_FACE` | 80 | Mindestgröße eines Gesichts in Pixel | 
| `FACEGATE_DOOR_PERMISSION` | tuer | welches Recht die Tür öffnet |
| `FACEGATE_LOG_RETENTION_DAYS` | 30 | Protokoll wird danach gelöscht |
| `FACEGATE_CAMERA` | auto | `picamera`, `usb` oder Pfad zu einem Video (zum Testen am PC) |

Den passenden Schwellwert findest du im Protokoll: Die Spalte „Ähnlichkeit" zeigt, wie
sicher die Erkennung war. Echte Treffer liegen meist über 0.5, fremde Personen unter 0.3.

## Sicherheit – was das System kann und was nicht

Umgesetzt: Dashboard nur mit Passwort (gehashed), CSRF-Schutz, Login-Bremse gegen
Passwort-Raten, Session-Cookies `HttpOnly`/`SameSite=Strict`, Bestätigung über mehrere Bilder.

**Grenzen, die du kennen solltest:**

- **Keine Lebenderkennung:** Ein gutes Foto oder Video auf dem Handy kann das System
  täuschen. Für eine echte Tür also nur als Komfort oder zusammen mit einem zweiten Faktor
  (PIN, RFID) einsetzen. Spannende Erweiterung: Blinzel-Erkennung oder eine Tiefenkamera.
- Das Dashboard läuft über HTTP. Nur im eigenen Netz betreiben, nicht ins Internet
  freigeben; für mehr Schutz einen Reverse-Proxy mit HTTPS (z. B. Caddy) davorsetzen.

## Datenschutz (DSGVO)

Gesichtsmerkmale sind biometrische Daten (Art. 9 DSGVO). Deshalb:

- Nur Personen registrieren, die **ausdrücklich eingewilligt** haben.
- Gespeichert werden keine Fotos, nur die Merkmalsvektoren. Unbekannte Personen werden
  nicht gespeichert, nur als „Unbekannte Person" protokolliert.
- Löschen einer Person entfernt alle ihre Gesichtsdaten sofort.
- Das Protokoll wird nach 30 Tagen automatisch bereinigt.
- Ein Hinweisschild an der Kamera ist Pflicht, wenn andere Menschen ins Bild laufen können.

## Am PC testen (ohne Pi)

```bash
pip install opencv-python flask numpy
./download_models.sh && python cli.py set-password
python main.py                      # nutzt automatisch die Webcam, Tür wird simuliert
```
