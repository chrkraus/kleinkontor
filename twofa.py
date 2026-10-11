"""Zwei-Faktor-Anmeldung per TOTP (Authenticator-App) mit Wiederherstellungscodes."""
import hashlib
import io
import secrets
import time

import pyotp
import qrcode
import qrcode.image.svg

ISSUER = "Buchhaltung"


def new_secret():
    return pyotp.random_base32()


def provisioning_uri(secret, username, company):
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=f"{ISSUER} {company}".strip())


def qr_svg(uri):
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def check_code(secret, code, last_step=0):
    """Prüft einen 6-stelligen Code (±30 s Toleranz). Gibt den verwendeten Zeitschritt zurück oder None.
    Bereits verwendete Zeitschritte werden abgelehnt (kein zweimaliges Benutzen desselben Codes)."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != 6 or not secret:
        return None
    totp = pyotp.TOTP(secret)
    now = int(time.time()) // 30
    for step in (now - 1, now, now + 1):
        if step > (last_step or 0) and secrets.compare_digest(totp.at(step * 30), code):
            return step
    return None


def _hash(code):
    return hashlib.sha256(code.replace("-", "").replace(" ", "").lower().encode()).hexdigest()


def new_recovery_codes(n=8):
    """Gibt (Klartext-Codes zum einmaligen Anzeigen, gespeicherter Text mit Hashes) zurück."""
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    return codes, "\n".join(_hash(c) for c in codes)


def use_recovery_code(stored, code):
    """Prüft einen Wiederherstellungscode. Gibt (ok, neuer gespeicherter Text ohne den Code) zurück."""
    hashes = [h for h in (stored or "").split("\n") if h]
    h = _hash(code or "")
    if h in hashes:
        hashes.remove(h)
        return True, "\n".join(hashes)
    return False, stored


def remaining_recovery_codes(stored):
    return len([h for h in (stored or "").split("\n") if h])
