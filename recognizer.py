"""Gesichtserkennung mit OpenCV: YuNet (Erkennung) + SFace (Wiedererkennung).

Beide Modelle sind klein (<40 MB zusammen) und laufen auf dem Pi 5 ohne
Zusatzhardware flüssig.
"""
import time

import cv2
import numpy as np


class FaceEngine:
    """Findet Gesichter und berechnet ihre Merkmalsvektoren.

    Nicht thread-sicher: jeder Thread braucht seine eigene Instanz.
    """

    def __init__(self, detector_path, recognizer_path, score_threshold=0.85, detect_width=640):
        for p in (detector_path, recognizer_path):
            if not p.exists():
                raise FileNotFoundError(f"Modell fehlt: {p} – bitte ./download_models.sh ausführen")
        self.detector = cv2.FaceDetectorYN.create(
            str(detector_path), "", (320, 320), score_threshold, 0.3, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")
        self.detect_width = detect_width

    def detect(self, frame):
        """Gibt ein Array (N, 15) zurück: x, y, w, h, 5 Landmarken (x,y), Score – in Original-Koordinaten."""
        h, w = frame.shape[:2]
        scale = min(1.0, self.detect_width / w)
        small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1.0 else frame
        sh, sw = small.shape[:2]
        self.detector.setInputSize((sw, sh))
        _, faces = self.detector.detect(small)
        if faces is None:
            return np.empty((0, 15), dtype=np.float32)
        faces = faces.copy()
        faces[:, :14] /= scale
        return faces

    def embed(self, frame, face):
        """Normierter 128-D-Merkmalsvektor für ein erkanntes Gesicht."""
        aligned = self.recognizer.alignCrop(frame, face)
        feature = self.recognizer.feature(aligned).ravel().astype(np.float32)
        return feature / (np.linalg.norm(feature) + 1e-9)

    def embed_single(self, frame):
        """Für die Registrierung: nimmt das größte Gesicht im Bild.

        Gibt (vektor, fehlermeldung) zurück.
        """
        faces = self.detect(frame)
        if len(faces) == 0:
            return None, "Kein Gesicht gefunden"
        largest = faces[np.argmax(faces[:, 2] * faces[:, 3])]
        if min(largest[2], largest[3]) < 60:
            return None, "Gesicht zu klein – näher an die Kamera"
        return self.embed(frame, largest), None


class FaceMatcher:
    """Vergleicht Merkmalsvektoren mit allen gespeicherten Personen."""

    def __init__(self, db, threshold, reload_interval=2.0):
        self.db = db
        self.threshold = threshold
        self.reload_interval = reload_interval
        self._version = None
        self._last_check = 0.0
        self.ids = np.empty(0, dtype=np.int64)
        self.matrix = np.empty((0, 128), dtype=np.float32)
        self.maybe_reload(force=True)

    def maybe_reload(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_check < self.reload_interval:
            return
        self._last_check = now
        version = self.db.get_version()
        if force or version != self._version:
            self.ids, self.matrix = self.db.load_embeddings()
            self._version = version

    def match(self, embedding):
        """Gibt (person_id oder None, bester Ähnlichkeitswert) zurück."""
        if len(self.ids) == 0:
            return None, 0.0
        sims = self.matrix @ embedding  # Kosinus-Ähnlichkeit, da alle Vektoren normiert sind
        best = int(np.argmax(sims))
        score = float(sims[best])
        if score < self.threshold:
            return None, score
        return int(self.ids[best]), score
