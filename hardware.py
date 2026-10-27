"""Kamera und Türrelais. Beide fallen automatisch auf eine Simulation zurück,
damit man das Programm auch am Laptop testen kann."""
import logging
import threading

import cv2

log = logging.getLogger("facegate.hw")


class Camera:
    def __init__(self, source="auto", width=1280, height=720):
        self.picam = None
        self.cap = None
        if source in ("auto", "picamera"):
            try:
                from picamera2 import Picamera2
                self.picam = Picamera2()
                # "RGB888" liefert bei libcamera Pixel in BGR-Reihenfolge – genau wie OpenCV es erwartet
                cfg = self.picam.create_video_configuration(
                    main={"size": (width, height), "format": "RGB888"})
                self.picam.configure(cfg)
                self.picam.start()
                log.info("Pi-Kamera gestartet (%dx%d)", width, height)
                return
            except Exception as exc:  # noqa: BLE001
                self.picam = None
                if source == "picamera":
                    raise RuntimeError(f"Pi-Kamera nicht verfügbar: {exc}") from exc
                log.warning("Pi-Kamera nicht verfügbar (%s) – versuche USB-Kamera", exc)
        target = 0 if source in ("auto", "usb") else source
        self.cap = cv2.VideoCapture(target)
        if isinstance(target, int):
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if not self.cap.isOpened():
            raise RuntimeError(f"Keine Kamera/Videoquelle gefunden: {source}")
        log.info("Videoquelle geöffnet: %s", target)

    def read(self):
        if self.picam is not None:
            return self.picam.capture_array("main")
        ok, frame = self.cap.read()
        if not ok:
            # Videodatei zu Ende -> von vorn (praktisch zum Testen)
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()
        return frame if ok else None

    def close(self):
        if self.picam is not None:
            self.picam.stop()
        if self.cap is not None:
            self.cap.release()


class Door:
    """Schaltet ein Relais für eine bestimmte Zeit ein (Türöffner)."""

    def __init__(self, pin, active_high=True, unlock_seconds=5.0):
        self.unlock_seconds = unlock_seconds
        self._lock = threading.Lock()
        self._timer = None
        self.is_open = False
        try:
            from gpiozero import OutputDevice
            self.relay = OutputDevice(pin, active_high=active_high, initial_value=False)
            log.info("Relais an GPIO%d bereit", pin)
        except Exception as exc:  # noqa: BLE001
            self.relay = None
            log.warning("Kein GPIO verfügbar (%s) – Tür wird nur simuliert", exc)

    def open(self):
        with self._lock:
            if self.relay is not None:
                self.relay.on()
            if not self.is_open:
                log.info("Tür geöffnet")
            self.is_open = True
            if self._timer is not None:
                self._timer.cancel()  # Zeit verlängern, solange die Person davor steht
            self._timer = threading.Timer(self.unlock_seconds, self.close)
            self._timer.daemon = True
            self._timer.start()

    def close(self):
        with self._lock:
            if self.relay is not None:
                self.relay.off()
            if self.is_open:
                log.info("Tür geschlossen")
            self.is_open = False

    def cleanup(self):
        if self._timer is not None:
            self._timer.cancel()
        self.close()
        if self.relay is not None:
            self.relay.close()
