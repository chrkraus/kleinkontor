"""Absicherung für den Betrieb hinter einem Tunnel/Proxy (z. B. Cloudflare Tunnel)."""
import os
from urllib.parse import urlparse

from flask import abort, request
from flask.sessions import SecureCookieSessionInterface

# Adressen, deren Proxy-Header (Client-IP, Protokoll) übernommen werden. Standard: nur dieser Rechner.
# Weitere (z. B. ein Reverse-Proxy-Container) per BUCHHALTUNG_TRUSTED_PROXIES="172.18.0.2,172.18.0.3".
LOCAL = {"127.0.0.1", "::1"} | {a.strip() for a in os.environ.get("BUCHHALTUNG_TRUSTED_PROXIES", "").split(",") if a.strip()}


class TrustLocalProxy:
    """WSGI-Middleware: Nur Anfragen, die von diesem Rechner selbst kommen (cloudflared läuft lokal),
    dürfen Client-IP und Protokoll per Header setzen. Direkte Zugriffe aus dem Heimnetz können
    diese Header deshalb nicht fälschen."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if environ.get("REMOTE_ADDR") in LOCAL:
            client = (environ.get("HTTP_CF_CONNECTING_IP")
                      or environ.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip())
            if client:
                environ["REMOTE_ADDR"] = client
            proto = environ.get("HTTP_X_FORWARDED_PROTO", "").split(",")[0].strip().lower()
            if proto in ("http", "https"):
                environ["wsgi.url_scheme"] = proto
            elif environ.get("HTTP_CF_CONNECTING_IP"):
                environ["wsgi.url_scheme"] = "https"     # über Cloudflare immer HTTPS
        else:
            # Direkter Zugriff: vom Client mitgeschickte Proxy-Header verwerfen
            for key in ("HTTP_X_FORWARDED_FOR", "HTTP_X_FORWARDED_PROTO", "HTTP_X_FORWARDED_HOST",
                        "HTTP_CF_CONNECTING_IP"):
                environ.pop(key, None)
        return self.app(environ, start_response)


class SchemeAwareSessionInterface(SecureCookieSessionInterface):
    """Sitzungs-Cookie mit „Secure“-Flag, sobald die Seite über HTTPS aufgerufen wird.
    Über http://192.168.x.x im Heimnetz funktioniert die Anmeldung weiterhin."""

    def get_cookie_secure(self, app):
        return request.is_secure


def check_same_origin():
    """CSRF-Schutz: Formulare (POST) nur annehmen, wenn sie von dieser App stammen."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source or source == "null":
        if source == "null":
            abort(403)
        return   # alte Browser/Werkzeuge ohne Header – SameSite-Cookie schützt zusätzlich
    if urlparse(source).netloc.lower() != request.host.lower():
        abort(403)


CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; frame-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'self'")


def add_headers(response):
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.is_secure:
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.endpoint in ("password_reset", "login_2fa", "twofa_setup"):
        h["Referrer-Policy"] = "no-referrer"
        h["Cache-Control"] = "no-store"
    return response
