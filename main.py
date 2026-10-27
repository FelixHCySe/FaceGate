"""FaceGate – Gesichtserkennung mit Zugangskontrolle für den Raspberry Pi 5.

Start:  python main.py              (mit Bildschirmanzeige)
        python main.py --headless   (ohne Monitor, nur Web-Dashboard)
"""
import argparse
import logging
import threading
import time

import cv2

import config
from database import Database, ascii_fold
from hardware import Camera, Door
from recognizer import FaceEngine, FaceMatcher

log = logging.getLogger("facegate")

GREEN = (80, 200, 60)
ORANGE = (0, 165, 255)
RED = (60, 60, 230)
WHITE = (255, 255, 255)


class SharedState:
    """Daten, die das Web-Dashboard aus der Erkennungsschleife liest."""

    def __init__(self):
        self.lock = threading.Lock()
        self.jpeg = None          # letztes Bild mit Overlay (für den Live-Stream)
        self.raw_frame = None     # letztes Rohbild (für "Foto aus Kamera" im Dashboard)
        self.faces = []           # aktuell erkannte Gesichter (für /api/status)
        self.door_open = False
        self.fps = 0.0

    def update(self, jpeg, raw, faces, door_open, fps):
        with self.lock:
            self.jpeg, self.raw_frame, self.faces = jpeg, raw, faces
            self.door_open, self.fps = door_open, fps

    def snapshot_raw(self):
        with self.lock:
            return None if self.raw_frame is None else self.raw_frame.copy()


class AccessController:
    """Entscheidet über Zutritt. Eine Person muss mehrere Bilder in Folge
    erkannt werden, bevor die Tür öffnet – das verhindert Fehlauslösungen."""

    def __init__(self, db, door):
        self.db = db
        self.door = door
        self.streaks = {}      # key -> Anzahl aufeinanderfolgender Bilder
        self.last_logged = {}  # key -> Zeitpunkt des letzten Protokolleintrags

    def process(self, results):
        seen = set()
        for r in results:
            key = r["person_id"] if r["person_id"] is not None else "unknown"
            if key in seen:
                continue
            seen.add(key)
            self.streaks[key] = self.streaks.get(key, 0) + 1
            if self.streaks[key] < config.CONFIRM_FRAMES:
                continue
            if r["granted"]:
                self.door.open()
            self._log(key, r)
        for key in list(self.streaks):
            if key not in seen:
                del self.streaks[key]

    def _log(self, key, r):
        now = time.monotonic()
        if now - self.last_logged.get(key, -1e9) < config.LOG_COOLDOWN:
            return
        self.last_logged[key] = now
        if r["person_id"] is None:
            note = "Unbekannte Person"
        elif r["granted"]:
            note = "Zutritt gewährt"
        else:
            note = f"Keine Berechtigung '{config.DOOR_PERMISSION}'"
        self.db.log_access(r["person_id"], r["name"], r["score"], r["granted"], note)
        log.info("%s: %s (%.2f)", note, r["name"], r["score"])


def recognize(frame, engine, matcher, persons):
    results = []
    for face in engine.detect(frame):
        x, y, w, h = (int(v) for v in face[:4])
        if min(w, h) < config.MIN_FACE_SIZE:
            continue
        person_id, score = matcher.match(engine.embed(frame, face))
        person = persons.get(person_id) if person_id is not None else None
        if person is None:
            person_id = None
        results.append({
            "box": (x, y, w, h),
            "person_id": person_id,
            "name": person["name"] if person else "Unbekannt",
            "age": person["age"] if person else None,
            "permissions": person["permissions"] if person else [],
            "granted": bool(person and config.DOOR_PERMISSION in person["permissions"]),
            "score": score,
        })
    return results


def draw_overlay(frame, results, door_open, fps):
    for r in results:
        x, y, w, h = r["box"]
        if r["person_id"] is None:
            color = RED
            lines = ["Unbekannt"]
        else:
            color = GREEN if r["granted"] else ORANGE
            lines = [ascii_fold(r["name"])]
            lines.append(f"Alter: {r['age']}" if r["age"] is not None else "Alter: -")
            perms = ", ".join(r["permissions"]) or "keine"
            lines.append(f"Rechte: {perms}")
        cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
        # Infobox unter dem Gesicht
        line_h = 26
        box_w = max(w, max(cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0][0] for t in lines) + 16)
        top = y + h + 4
        cv2.rectangle(frame, (x, top), (x + box_w, top + line_h * len(lines) + 8), color, -1)
        for i, text in enumerate(lines):
            cv2.putText(frame, text, (x + 8, top + 22 + i * line_h),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, WHITE, 2, cv2.LINE_AA)

    status = "TUER OFFEN" if door_open else "TUER ZU"
    cv2.rectangle(frame, (0, 0), (frame.shape[1], 40), (30, 30, 30), -1)
    cv2.putText(frame, f"FaceGate   {status}", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                GREEN if door_open else WHITE, 2, cv2.LINE_AA)
    cv2.putText(frame, f"{fps:4.1f} FPS", (frame.shape[1] - 130, 28), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, WHITE, 2, cv2.LINE_AA)


def start_web(db, state):
    from werkzeug.serving import make_server
    from web import create_app

    app = create_app(db, state)
    server = make_server(config.WEB_HOST, config.WEB_PORT, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, name="web", daemon=True)
    thread.start()
    log.info("Dashboard läuft auf http://%s:%d", config.WEB_HOST, config.WEB_PORT)
    return server


def main():
    parser = argparse.ArgumentParser(description="FaceGate Gesichtserkennung")
    parser.add_argument("--headless", action="store_true", help="kein Fenster öffnen")
    parser.add_argument("--no-web", action="store_true", help="Dashboard nicht starten")
    parser.add_argument("--camera", default=config.CAMERA_SOURCE, help="auto | picamera | usb | Videodatei")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db = Database(config.DB_PATH)
    db.purge_old_log(config.LOG_RETENTION_DAYS)
    engine = FaceEngine(config.DETECTOR_MODEL, config.RECOGNIZER_MODEL,
                        config.DETECTION_SCORE, config.DETECT_WIDTH)
    matcher = FaceMatcher(db, config.MATCH_THRESHOLD)
    camera = Camera(args.camera, config.FRAME_WIDTH, config.FRAME_HEIGHT)
    door = Door(config.RELAY_PIN, config.RELAY_ACTIVE_HIGH, config.UNLOCK_SECONDS)
    access = AccessController(db, door)
    state = SharedState()
    server = None if args.no_web else start_web(db, state)

    window = "FaceGate"
    if not args.headless:
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(window, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    persons, persons_version = {}, None
    fps, last = 0.0, time.monotonic()
    try:
        while True:
            frame = camera.read()
            if frame is None:
                time.sleep(0.05)
                continue
            raw = frame.copy()

            matcher.maybe_reload()
            if matcher._version != persons_version:  # Personendaten mit den Embeddings neu laden
                persons = {p["id"]: p for p in db.list_persons()}
                persons_version = matcher._version

            results = recognize(frame, engine, matcher, persons)
            access.process(results)
            draw_overlay(frame, results, door.is_open, fps)

            now = time.monotonic()
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - last, 1e-3))
            last = now

            ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            faces = [{k: v for k, v in r.items() if k != "box"} for r in results]
            state.update(jpeg.tobytes() if ok else None, raw, faces, door.is_open, fps)

            if not args.headless:
                cv2.imshow(window, frame)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Beende FaceGate …")
        camera.close()
        door.cleanup()
        if server is not None:
            server.shutdown()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
