"""Anbindung an Paperless-ngx über die REST-API."""
import re
import time

import requests

import environment


class PaperlessError(Exception):
    pass


class PaperlessClient:
    def __init__(self, url, token, timeout=20):
        if not url or not token:
            raise PaperlessError("Paperless ist nicht konfiguriert (URL und Token in den Einstellungen hinterlegen).")
        self.base = url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {token}",
            "Accept": "application/json",
        })

    def _request(self, method, path, **kwargs):
        try:
            r = self.session.request(method, f"{self.base}{path}", timeout=self.timeout, **kwargs)
        except requests.RequestException as e:
            raise PaperlessError(f"Paperless nicht erreichbar: {e}") from e
        if r.status_code in (401, 403):
            raise PaperlessError("Paperless hat die Anmeldung abgelehnt – Token prüfen.")
        if not r.ok:
            raise PaperlessError(f"Paperless-Fehler {r.status_code}: {r.text[:300]}")
        return r

    def test(self):
        r = self._request("GET", "/api/documents/", params={"page_size": 1})
        return r.json().get("count", 0)

    def document_url(self, doc_id):
        return f"{self.base}/documents/{doc_id}/details"

    def get_document(self, doc_id):
        return self._request("GET", f"/api/documents/{doc_id}/").json()

    def search(self, query, page_size=25):
        params = {"page_size": page_size, "ordering": "-created"}
        if query:
            params["query"] = query
        return self._request("GET", "/api/documents/", params=params).json().get("results", [])

    def download(self, doc_id, original=False):
        params = {"original": "true"} if original else None
        r = self._request("GET", f"/api/documents/{doc_id}/download/", params=params)
        return r.content, r.headers.get("Content-Type", "application/pdf"), r.headers.get("Content-Disposition", "")

    def _named_id(self, endpoint, name):
        if environment.IS_TEST:
            raise PaperlessError("Testumgebung: Änderungen in Paperless sind gesperrt.")
        """Sucht ein Tag/Korrespondent/Dokumenttyp per Name, legt ihn ggf. an."""
        results = self._request("GET", f"/api/{endpoint}/", params={"name__iexact": name}).json().get("results", [])
        if results:
            return results[0]["id"]
        return self._request("POST", f"/api/{endpoint}/", json={"name": name}).json()["id"]

    def upload(self, filename, content, title=None, created=None, tags=(), correspondent=None,
               document_type=None, mimetype="application/octet-stream"):
        """Lädt eine Datei hoch und gibt die Task-ID des Paperless-Consumers zurück."""
        if environment.IS_TEST:
            raise PaperlessError("Testumgebung: Hochladen nach Paperless ist gesperrt (nur Lesen erlaubt).")
        data = []
        if title:
            data.append(("title", title))
        if created:
            data.append(("created", created.isoformat()))
        for tag in tags:
            data.append(("tags", str(self._named_id("tags", tag))))
        if correspondent:
            data.append(("correspondent", str(self._named_id("correspondents", correspondent))))
        if document_type:
            data.append(("document_type", str(self._named_id("document_types", document_type))))
        r = self._request("POST", "/api/documents/post_document/",
                          files={"document": (filename, content, mimetype)}, data=data)
        task_id = r.json()
        if isinstance(task_id, dict):
            task_id = task_id.get("task_id")
        return str(task_id).strip('"')

    def task_result(self, task_id):
        """Gibt (status, document_id, message) des Upload-Tasks zurück."""
        tasks = self._request("GET", "/api/tasks/", params={"task_id": task_id}).json()
        if isinstance(tasks, dict):
            tasks = tasks.get("results", [])
        if not tasks:
            return "PENDING", None, ""
        t = tasks[0]
        # Ältere Versionen: STATUS in Großbuchstaben, related_document + result (Text).
        # Neuere Versionen: status klein, related_document_ids + result_data.
        status = str(t.get("status") or "PENDING").upper()
        doc = t.get("related_document")
        if not doc and t.get("related_document_ids"):
            doc = t["related_document_ids"][0]
        result = t.get("result") or t.get("result_data") or ""
        if not doc and isinstance(result, dict):
            doc = result.get("document_id") or result.get("id")
        if isinstance(result, dict):
            result = result.get("message") or result.get("error") or str(result)
        if not doc and status == "SUCCESS":
            m = re.search(r"document id (\d+)", str(result), re.IGNORECASE)
            doc = m.group(1) if m else None
        return status, int(doc) if doc else None, str(result)

    def wait_for_task(self, task_id, timeout=15):
        deadline = time.time() + timeout
        while True:
            status, doc_id, msg = self.task_result(task_id)
            if status in ("SUCCESS", "FAILURE") or time.time() > deadline:
                return status, doc_id, msg
            time.sleep(1)
