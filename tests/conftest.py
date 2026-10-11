"""Gemeinsame Test-Fixtures. Die App läuft mit einem temporären Datenordner – nie mit echten Daten."""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["BUCHHALTUNG_DATA"] = tempfile.mkdtemp(prefix="buchhaltung-test-")
os.environ.pop("BUCHHALTUNG_ENV", None)
os.environ.pop("BUCHHALTUNG_SCHEDULER", None)

import app as appmodule  # noqa: E402  (erst nach dem Setzen des Datenordners importieren)
from models import Customer, Settings, User, db  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

PASSWORD = "geheim123"


@pytest.fixture(scope="session")
def app():
    a = appmodule.app
    a.config["TESTING"] = True
    with a.app_context():
        s = Settings.get()
        s.company_name, s.owner_name = "Muster GmbH", "Max Muster"
        s.street, s.zip_code, s.city = "Hauptstr. 1", "12345", "Berlin"
        s.tax_number, s.iban, s.bic = "12/345/67890", "DE02120300000000202051", "BYLADEM1001"
        s.small_business, s.einvoice_enabled = True, True
        db.session.add(User(username="admin", display_name="Admin", password_hash=generate_password_hash(PASSWORD),
                            is_admin=True, active=True))
        db.session.add(User(username="leser", password_hash=generate_password_hash(PASSWORD), active=True,
                            permissions="documents_view,bookkeeping_view"))
        db.session.add(Customer(customer_number="K1", company="Kunde AG", name="Erika Beispiel", street="Weg 2",
                                zip_code="54321", city="Köln", country="Deutschland", email="kunde@example.de"))
        db.session.commit()
    return a


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(appmodule.time, "sleep", lambda s: None)


def login(app, username="admin", ip="10.0.0.1"):
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = ip
    r = c.post("/anmelden", data={"username": username, "password": PASSWORD})
    assert r.status_code == 302, r.data[:300]
    return c


@pytest.fixture
def admin(app):
    return login(app)


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield


def make_invoice(client, items, small_business=True, finalize=True, skonto=None, service_date="01.10.2026"):
    data = {"kind": "invoice", "customer_id": "1", "issue_date": "2026-10-01", "due_date": "2026-10-15",
            "service_date": service_date, "small_business": "1" if small_business else "0",
            "item_description": [i[0] for i in items], "item_quantity": [i[1] for i in items],
            "item_unit": ["Stk."] * len(items), "item_price": [i[2] for i in items],
            "item_vat": [i[3] if len(i) > 3 else "0" for i in items], "item_product_id": [""] * len(items),
            "action": "finalize" if finalize else "save"}
    if skonto:
        data.update(skonto_on="1", skonto_percent=skonto[0], skonto_days=skonto[1])
    r = client.post("/dokument/speichern", data=data)
    assert r.status_code == 302, r.data[:300]
    from models import Document
    return Document.query.order_by(Document.id.desc()).first()
