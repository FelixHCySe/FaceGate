"""Zentrale Einstellungen für FaceGate.

Alle Werte lassen sich über Umgebungsvariablen überschreiben (z. B. in der
systemd-Unit oder vor dem Start: FACEGATE_RELAY_PIN=27 python main.py).
"""
import os
from pathlib import Path


def _env(name, default, cast=str):
    value = os.environ.get(name)
    if value is None:
        return default
    if cast is bool:
        return value.strip().lower() in ("1", "true", "yes", "ja", "on")
    return cast(value)


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(_env("FACEGATE_DATA", str(BASE_DIR / "data")))
MODEL_DIR = Path(_env("FACEGATE_MODELS", str(BASE_DIR / "models")))

DB_PATH = DATA_DIR / "facegate.db"
SECRET_KEY_PATH = DATA_DIR / "secret.key"
ADMIN_HASH_PATH = DATA_DIR / "admin.hash"

# OpenCV-Zoo-Modelle (werden von download_models.sh geladen)
DETECTOR_MODEL = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = MODEL_DIR / "face_recognition_sface_2021dec.onnx"

# Kamera: "auto" (erst Pi-Kamera, dann USB), "picamera", "usb" oder Pfad zu einer Videodatei
CAMERA_SOURCE = _env("FACEGATE_CAMERA", "auto")
FRAME_WIDTH = _env("FACEGATE_WIDTH", 1280, int)
FRAME_HEIGHT = _env("FACEGATE_HEIGHT", 720, int)
DETECT_WIDTH = _env("FACEGATE_DETECT_WIDTH", 640, int)  # Erkennung auf verkleinertem Bild = schneller

# Erkennung
DETECTION_SCORE = _env("FACEGATE_DETECTION_SCORE", 0.85, float)
# Kosinus-Ähnlichkeit für SFace. OpenCV empfiehlt 0.363; etwas höher = weniger Verwechslungen
MATCH_THRESHOLD = _env("FACEGATE_MATCH_THRESHOLD", 0.42, float)
MIN_FACE_SIZE = _env("FACEGATE_MIN_FACE", 80, int)  # Pixel; kleinere Gesichter werden ignoriert
CONFIRM_FRAMES = _env("FACEGATE_CONFIRM_FRAMES", 4, int)  # so oft hintereinander erkannt, bevor entschieden wird

# Tür / Relais
DOOR_PERMISSION = _env("FACEGATE_DOOR_PERMISSION", "tuer").lower()
RELAY_PIN = _env("FACEGATE_RELAY_PIN", 17, int)  # BCM-Nummer (GPIO17 = Pin 11)
RELAY_ACTIVE_HIGH = _env("FACEGATE_RELAY_ACTIVE_HIGH", True, bool)
UNLOCK_SECONDS = _env("FACEGATE_UNLOCK_SECONDS", 5.0, float)

# Protokoll
LOG_COOLDOWN = _env("FACEGATE_LOG_COOLDOWN", 30.0, float)  # Sekunden zwischen zwei Einträgen pro Person
LOG_RETENTION_DAYS = _env("FACEGATE_LOG_RETENTION_DAYS", 30, int)

# Web-Dashboard
WEB_HOST = _env("FACEGATE_WEB_HOST", "0.0.0.0")
WEB_PORT = _env("FACEGATE_WEB_PORT", 8080, int)
MAX_UPLOAD_MB = _env("FACEGATE_MAX_UPLOAD_MB", 20, int)
