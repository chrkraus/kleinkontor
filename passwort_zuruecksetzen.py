"""Setzt das Passwort eines Benutzers zurück bzw. legt einen Admin an.

Aufruf auf dem Server:  .venv/bin/python passwort_zuruecksetzen.py [benutzername]
"""
import getpass
import sys

from werkzeug.security import generate_password_hash

from app import app
from models import AuditLog, User, db

if __name__ == "__main__":
    with app.app_context():
        names = [u.username + (" (Admin)" if u.is_admin else "") + ("" if u.active else " – deaktiviert")
                 for u in User.query.order_by(User.username)]
        print("Benutzer:", ", ".join(names) or "keine")
        username = (sys.argv[1] if len(sys.argv) > 1 else input("Benutzername: ")).strip().lower()
        pw = getpass.getpass("Neues Passwort (mind. 8 Zeichen): ")
        if len(pw) < 8 or pw != getpass.getpass("Wiederholen: "):
            raise SystemExit("Abgebrochen: zu kurz oder Eingaben verschieden.")
        u = User.query.filter_by(username=username).first()
        if u is None:
            u = User(username=username, is_admin=True, active=True, password_hash="")
            db.session.add(u)
            print(f"Neuer Admin „{username}“ wird angelegt.")
        u.password_hash = generate_password_hash(pw)
        u.active = True
        db.session.add(AuditLog(username="konsole", action="Passwort zurückgesetzt", object_type="user",
                                details=username))
        db.session.commit()
        print(f"Passwort für „{username}“ gesetzt; Konto ist aktiv.")
