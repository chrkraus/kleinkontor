"""Erkennung der Umgebung: produktiv (Standard) oder Testumgebung (BUCHHALTUNG_ENV=test).

In der Testumgebung gilt:
- alle E-Mails gehen an BUCHHALTUNG_TEST_MAIL (bzw. die Absenderadresse), nie an Kunden
- nichts wird nach Paperless hochgeladen (nur lesen)
- keine Hintergrundaufgaben (keine automatischen Erinnerungen)
- deutliche Kennzeichnung in Oberfläche und PDF
"""
import os

ENV = os.environ.get("BUCHHALTUNG_ENV", "prod").strip().lower()
IS_TEST = ENV == "test"
TEST_MAIL = os.environ.get("BUCHHALTUNG_TEST_MAIL", "").strip()
