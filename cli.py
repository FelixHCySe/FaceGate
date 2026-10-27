"""Verwaltung über die Kommandozeile.

  python cli.py set-password
  python cli.py add "Max Muster" --birthdate 2001-05-14 --permissions tuer,labor --photos fotos/max/*.jpg
  python cli.py add "Max Muster" --camera 5        (5 Fotos über die Kamera aufnehmen)
  python cli.py list
  python cli.py delete 3
"""
import argparse
import getpass
import sys
import time

import cv2

import config
from database import Database


def cmd_set_password(_args):
    from werkzeug.security import generate_password_hash
    pw = getpass.getpass("Neues Admin-Passwort: ")
    if len(pw) < 10:
        sys.exit("Bitte mindestens 10 Zeichen verwenden.")
    if pw != getpass.getpass("Wiederholen: "):
        sys.exit("Passwörter stimmen nicht überein.")
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.ADMIN_HASH_PATH.write_text(generate_password_hash(pw))
    config.ADMIN_HASH_PATH.chmod(0o600)
    print("Passwort gespeichert.")


def cmd_add(args):
    from recognizer import FaceEngine
    db = Database(config.DB_PATH)
    engine = FaceEngine(config.DETECTOR_MODEL, config.RECOGNIZER_MODEL,
                        config.DETECTION_SCORE, config.DETECT_WIDTH)
    images = [(p, cv2.imread(p)) for p in args.photos]
    if args.camera:
        from hardware import Camera
        cam = Camera(config.CAMERA_SOURCE, config.FRAME_WIDTH, config.FRAME_HEIGHT)
        print(f"Nehme {args.camera} Fotos auf – Kopf zwischen den Fotos leicht drehen …")
        time.sleep(1.5)
        for i in range(args.camera):
            images.append((f"Kamera {i + 1}", cam.read()))
            time.sleep(0.8)
        cam.close()

    vectors = []
    for label, img in images:
        if img is None:
            print(f"  ✗ {label}: nicht lesbar")
            continue
        vec, err = engine.embed_single(img)
        print(f"  {'✗' if err else '✓'} {label}{': ' + err if err else ''}")
        if vec is not None:
            vectors.append(vec)
    if not vectors:
        sys.exit("Keine verwertbaren Gesichter – Person wurde nicht angelegt.")

    person_id = db.add_person(args.name, args.birthdate, args.permissions)
    for vec in vectors:
        db.add_embedding(person_id, vec)
    print(f"{args.name} (ID {person_id}) mit {len(vectors)} Gesichtsprobe(n) angelegt.")


def cmd_list(_args):
    db = Database(config.DB_PATH)
    for p in db.list_persons():
        status = "aktiv" if p["active"] else "gesperrt"
        print(f"{p['id']:>3}  {p['name']:<25} Alter {p['age'] if p['age'] is not None else '-':>3}  "
              f"{status:<8} Proben {p['samples']:>2}  Rechte: {', '.join(p['permissions']) or '-'}")


def cmd_delete(args):
    db = Database(config.DB_PATH)
    db.delete_person(args.id)
    print("Gelöscht.")


def main():
    parser = argparse.ArgumentParser(description="FaceGate Verwaltung")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("set-password").set_defaults(func=cmd_set_password)
    add = sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("--birthdate", help="YYYY-MM-DD")
    add.add_argument("--permissions", default="", help="kommagetrennt, z. B. tuer,labor")
    add.add_argument("--photos", nargs="*", default=[])
    add.add_argument("--camera", type=int, default=0, help="Anzahl Fotos über die Kamera")
    add.set_defaults(func=cmd_add)
    sub.add_parser("list").set_defaults(func=cmd_list)
    delete = sub.add_parser("delete")
    delete.add_argument("id", type=int)
    delete.set_defaults(func=cmd_delete)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
