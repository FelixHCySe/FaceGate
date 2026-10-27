"""Web-Dashboard: Live-Bild, Personenverwaltung und Zugangsprotokoll."""
import csv
import io
import secrets
import threading
import time
from datetime import date
from functools import wraps

import cv2
import numpy as np
from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash

import config
from recognizer import FaceEngine


def load_secret_key():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not config.SECRET_KEY_PATH.exists():
        config.SECRET_KEY_PATH.write_text(secrets.token_hex(32))
        config.SECRET_KEY_PATH.chmod(0o600)
    return config.SECRET_KEY_PATH.read_text().strip()


def admin_hash():
    return config.ADMIN_HASH_PATH.read_text().strip() if config.ADMIN_HASH_PATH.exists() else None


def create_app(db, state):
    app = Flask(__name__)
    app.secret_key = load_secret_key()
    app.config.update(
        MAX_CONTENT_LENGTH=config.MAX_UPLOAD_MB * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
    )

    # Eigene Modell-Instanz fürs Registrieren (OpenCV-Netze nicht zwischen Threads teilen)
    engine_holder = {"engine": None}
    engine_lock = threading.Lock()
    failed_logins = {"count": 0}

    def get_engine():
        if engine_holder["engine"] is None:
            engine_holder["engine"] = FaceEngine(config.DETECTOR_MODEL, config.RECOGNIZER_MODEL,
                                                 config.DETECTION_SCORE, config.DETECT_WIDTH)
        return engine_holder["engine"]

    def add_samples(person_id, images):
        """Berechnet Embeddings aus Bildern. Gibt (Anzahl ok, Liste Fehler) zurück."""
        ok, errors = 0, []
        with engine_lock:
            engine = get_engine()
            for label, img in images:
                if img is None:
                    errors.append(f"{label}: Bild konnte nicht gelesen werden")
                    continue
                vec, err = engine.embed_single(img)
                if err:
                    errors.append(f"{label}: {err}")
                    continue
                db.add_embedding(person_id, vec)
                ok += 1
        return ok, errors

    def uploaded_images():
        images = []
        for f in request.files.getlist("photos"):
            if not f or not f.filename:
                continue
            data = np.frombuffer(f.read(), dtype=np.uint8)
            images.append((f.filename, cv2.imdecode(data, cv2.IMREAD_COLOR)))
        return images

    # ---------- Sicherheit: Login + CSRF ----------
    def login_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not session.get("admin"):
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapper

    @app.before_request
    def csrf_protect():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        if request.method == "POST":
            token = request.form.get("csrf", "")
            if not secrets.compare_digest(token, session["csrf"]):
                abort(400, "Ungültiges CSRF-Token – Seite neu laden")

    @app.context_processor
    def inject_globals():
        return {"csrf_token": session.get("csrf", ""), "door_permission": config.DOOR_PERMISSION}

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    @app.route("/login", methods=["GET", "POST"])
    def login():
        pw_hash = admin_hash()
        if request.method == "POST":
            if pw_hash and check_password_hash(pw_hash, request.form.get("password", "")):
                session.clear()
                session["admin"] = True
                session["csrf"] = secrets.token_hex(16)
                failed_logins["count"] = 0
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))
            failed_logins["count"] += 1
            time.sleep(min(2 ** failed_logins["count"], 30))  # bremst Passwort-Raten aus
            flash("Falsches Passwort", "error")
        return render_template("login.html", no_password=pw_hash is None)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------- Übersicht & Live-Bild ----------
    @app.route("/")
    @login_required
    def index():
        return render_template("index.html", log=db.recent_log(12), persons=db.list_persons())

    @app.route("/stream")
    @login_required
    def stream():
        def generate():
            last = None
            while True:
                with state.lock:
                    jpeg = state.jpeg
                if jpeg is not None and jpeg is not last:
                    last = jpeg
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                time.sleep(0.08)
        return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.route("/api/status")
    @login_required
    def api_status():
        with state.lock:
            return jsonify(door_open=state.door_open, fps=round(state.fps, 1), faces=state.faces)

    # ---------- Personen ----------
    @app.route("/persons")
    @login_required
    def persons():
        return render_template("persons.html", persons=db.list_persons())

    def form_values():
        name = request.form.get("name", "").strip()
        birthdate = request.form.get("birthdate", "").strip() or None
        if birthdate:
            try:
                if date.fromisoformat(birthdate) > date.today():
                    raise ValueError
            except ValueError:
                return None, "Ungültiges Geburtsdatum"
        if not name:
            return None, "Name fehlt"
        return (name, birthdate, request.form.get("permissions", "")), None

    @app.route("/persons/new", methods=["GET", "POST"])
    @login_required
    def person_new():
        if request.method == "POST":
            values, err = form_values()
            if err:
                flash(err, "error")
                return render_template("person_form.html", person=None, form=request.form)
            person_id = db.add_person(*values)
            images = uploaded_images()
            if request.form.get("from_camera"):
                images.append(("Kamerabild", state.snapshot_raw()))
            ok, errors = add_samples(person_id, images)
            for e in errors:
                flash(e, "error")
            flash(f"{values[0]} angelegt – {ok} Gesichtsprobe(n) gespeichert", "ok" if ok else "error")
            return redirect(url_for("person_edit", person_id=person_id))
        return render_template("person_form.html", person=None, form={})

    @app.route("/persons/<int:person_id>", methods=["GET", "POST"])
    @login_required
    def person_edit(person_id):
        person = db.get_person(person_id)
        if person is None:
            abort(404)
        if request.method == "POST":
            values, err = form_values()
            if err:
                flash(err, "error")
            else:
                db.update_person(person_id, *values, active=bool(request.form.get("active")))
                flash("Gespeichert", "ok")
            return redirect(url_for("person_edit", person_id=person_id))
        return render_template("person_form.html", person=person, form={})

    @app.route("/persons/<int:person_id>/samples", methods=["POST"])
    @login_required
    def person_samples(person_id):
        if db.get_person(person_id) is None:
            abort(404)
        if request.form.get("reset"):
            db.clear_embeddings(person_id)
            flash("Alle Gesichtsproben gelöscht", "ok")
        else:
            images = uploaded_images()
            if request.form.get("from_camera"):
                images.append(("Kamerabild", state.snapshot_raw()))
            ok, errors = add_samples(person_id, images)
            for e in errors:
                flash(e, "error")
            if ok:
                flash(f"{ok} Gesichtsprobe(n) hinzugefügt", "ok")
        return redirect(url_for("person_edit", person_id=person_id))

    @app.route("/persons/<int:person_id>/delete", methods=["POST"])
    @login_required
    def person_delete(person_id):
        db.delete_person(person_id)
        flash("Person und alle Gesichtsdaten gelöscht", "ok")
        return redirect(url_for("persons"))

    # ---------- Protokoll ----------
    @app.route("/log")
    @login_required
    def access_log():
        return render_template("log.html", log=db.recent_log(500))

    @app.route("/log.csv")
    @login_required
    def access_log_csv():
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")
        writer.writerow(["Zeit", "Name", "Ähnlichkeit", "Zutritt", "Hinweis"])
        for e in db.recent_log(100000):
            writer.writerow([e["ts"], e["name"], e["score"], "ja" if e["granted"] else "nein", e["note"]])
        return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=zugangsprotokoll.csv"})

    return app
