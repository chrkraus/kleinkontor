"""Belegerkennung, EÜR-Zuordnung, Testmodus beim Mailversand, Datensicherung, Zwei-Faktor."""
import os
import sqlite3
import zipfile

import pyotp

import backup
import environment
import euer_form
import mailer
import twofa
from models import Settings, Transaction, db
from receipts import guess_category, parse_amount


def test_parse_amount_prefers_total():
    text = "Zwischensumme 158,82 €\nMwSt. 19 % 30,18 €\nGesamtbetrag 189,00 €"
    assert parse_amount(text)[0] == 18900
    assert parse_amount("Rechnungsbetrag netto 8,40\nRechnungsbetrag brutto 10,00")[0] == 1000
    assert parse_amount("ohne Betrag")[0] is None


def test_category_ignores_word_parts(ctx):
    text = "Bitte scannen Sie den Code mit Ihrer Bankingsoftware."
    assert guess_category("Stadtverwaltung", text, "Gewerbeanmeldung") == "Versicherungen / Beiträge"
    assert guess_category("", text, "") != "Software / Lizenzen"


def test_euer_lines_per_form_year(ctx):
    db.session.add(Transaction(kind="expense", date=__import__("datetime").date(2026, 3, 1),
                               category="Waren / Material", gross_amount=10000, vat_rate=0))
    db.session.commit()
    rows = {r["key"]: r["line"] for r in euer_form.compute(2026, True)["expenses"]}
    assert rows["goods"] == euer_form.FIELDS["goods"][2][2026] == 29
    assert euer_form.FIELDS["goods"][2][2025] == 27


def test_test_mode_redirects_mail(ctx, monkeypatch):
    sent = {}

    class FakeServer:
        def send_message(self, msg, from_addr, to_addrs):
            sent.update(to=to_addrs, subject=msg["Subject"], body=msg.get_body(("plain",)).get_content())
            return {}

        def quit(self):
            pass
    monkeypatch.setattr(mailer, "_connect", lambda s: FakeServer())
    monkeypatch.setattr(environment, "IS_TEST", True)
    monkeypatch.setattr(environment, "TEST_MAIL", "test@example.de")
    mailer.send(Settings.get(), ["kunde@example.de"], ["chef@example.de"], "Rechnung", "Hallo", [])
    assert sent["to"] == ["test@example.de"]
    assert sent["subject"].startswith("[TEST]") and "kunde@example.de" in sent["body"]


def test_backup_roundtrip(app, tmp_path):
    src = tmp_path / "quelle.db"
    con = sqlite3.connect(src)
    con.execute("create table t (x)"); con.execute("insert into t values (42)"); con.commit(); con.close()
    path, warning = backup.create_backup(str(tmp_path), str(src), keep=3)
    assert warning is None and oct(os.stat(path).st_mode)[-3:] == "600"
    zipfile.ZipFile(path).extract("buchhaltung.db", tmp_path / "wieder")
    assert sqlite3.connect(tmp_path / "wieder" / "buchhaltung.db").execute("select x from t").fetchone() == (42,)


def test_totp_rejects_reused_code():
    secret = twofa.new_secret()
    code = pyotp.TOTP(secret).now()
    step = twofa.check_code(secret, code)
    assert step and twofa.check_code(secret, code, last_step=step) is None


def test_recovery_codes_are_single_use():
    codes, stored = twofa.new_recovery_codes(2)
    ok, stored = twofa.use_recovery_code(stored, codes[0].upper())
    assert ok and twofa.remaining_recovery_codes(stored) == 1
    assert twofa.use_recovery_code(stored, codes[0])[0] is False
