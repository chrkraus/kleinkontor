"""Anmeldung, Rechte, CSRF-Schutz und Proxy-Vertrauen."""
import auth
import security
from conftest import login


def test_every_endpoint_has_a_permission(app):
    endpoints = {r.endpoint for r in app.url_map.iter_rules()}
    assert endpoints <= set(auth.ENDPOINTS), endpoints - set(auth.ENDPOINTS)


def test_login_required(app):
    r = app.test_client().get("/rechnungen")
    assert r.status_code == 302 and "/anmelden" in r.headers["Location"]


def test_read_only_user_cannot_change(app):
    c = login(app, "leser", ip="10.0.0.2")
    assert c.get("/rechnungen").status_code == 200
    assert c.post("/kunden/1", data={"company": "X"}).status_code == 403
    assert c.get("/einstellungen").status_code == 302


def test_csrf_foreign_origin_rejected(admin):
    r = admin.post("/kunden/neu", data={"company": "X"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_proxy_headers_only_trusted_from_localhost():
    seen = {}

    def inner(environ, start_response):
        seen.update(ip=environ["REMOTE_ADDR"], scheme=environ["wsgi.url_scheme"])
        return []
    mw = security.TrustLocalProxy(inner)
    mw({"REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": "1.2.3.4", "HTTP_X_FORWARDED_PROTO": "https",
        "wsgi.url_scheme": "http"}, None)
    assert seen == {"ip": "1.2.3.4", "scheme": "https"}
    mw({"REMOTE_ADDR": "192.168.1.50", "HTTP_X_FORWARDED_FOR": "1.2.3.4", "HTTP_CF_CONNECTING_IP": "6.6.6.6",
        "HTTP_X_FORWARDED_PROTO": "https", "wsgi.url_scheme": "http"}, None)
    assert seen == {"ip": "192.168.1.50", "scheme": "http"}


def test_security_headers(admin):
    h = admin.get("/").headers
    assert h["X-Frame-Options"] == "SAMEORIGIN" and "default-src 'self'" in h["Content-Security-Policy"]
