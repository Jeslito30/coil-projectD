#!/usr/bin/env python3
"""Push-only public mirror of Team Dashboard v1 to a public Backblaze B2 bucket.

Why push-only: publishing the dashboard FROM the server would open an inbound port and break the
"only WireGuard open" requirement. This process only makes OUTBOUND HTTPS requests: every interval it
builds the same summary as server.py (read-only InfluxDB token, fixed queries) and uploads it as
summary.json, next to a static copy of the page. Faculty open the public B2 URL; the VM stays closed.

What becomes public: the six sensors' readings, statuses, last-hour series and delays, nothing else.
No token, host name or IP address is written to the bucket.

Environment:
  INFLUX_URL, INFLUX_ORG, INFLUX_TOKEN (read-only), INFLUX_BUCKET, ... as for server.py
  B2_KEY_ID, B2_APP_KEY  application key restricted to the mirror bucket (writeFiles only is enough)
  B2_BUCKET              name of the PUBLIC mirror bucket (never the backup bucket)
  PUBLISH_INTERVAL       seconds between uploads (default 60)
  B2_API_BASE            default https://api.backblazeb2.com (tests override it)
Run:  python3 publish_b2.py            loop forever (container/systemd)
      python3 publish_b2.py --once     one upload of assets + summary, then exit
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import server  # noqa: E402  (same directory; shares queries, registry and status rules)

MIRROR_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
              "connect-src 'self'; base-uri 'none'; form-action 'none'")


class B2Error(RuntimeError):
    pass


class B2:
    def __init__(self, key_id: str, app_key: str, bucket: str, api_base: str, timeout: float = 20.0):
        self.key_id, self.app_key, self.bucket_name = key_id, app_key, bucket
        self.api_base, self.timeout = api_base.rstrip("/"), timeout
        self.auth = None          # authorize_account result
        self.upload = None        # (upload_url, upload_token)
        self.bucket_id = None

    def _call(self, url: str, body: dict | None, token: str | None, basic: str | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = token
        if basic:
            headers["Authorization"] = "Basic " + basic
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read())

    def authorize(self) -> None:
        basic = base64.b64encode(f"{self.key_id}:{self.app_key}".encode()).decode()
        self.auth = self._call(self.api_base + "/b2api/v3/b2_authorize_account", None, None, basic)
        storage = self.auth["apiInfo"]["storageApi"]
        allowed = storage.get("allowed", {}).get("buckets") or []
        match = [b for b in allowed if b.get("name") == self.bucket_name]
        if match:
            self.bucket_id = match[0]["id"]
        else:
            listed = self._call(storage["apiUrl"] + "/b2api/v3/b2_list_buckets",
                                {"accountId": self.auth["accountId"], "bucketName": self.bucket_name},
                                self.auth["authorizationToken"])
            if not listed.get("buckets"):
                raise B2Error(f"bucket {self.bucket_name!r} not visible to this key")
            self.bucket_id = listed["buckets"][0]["bucketId"]
        self.upload = None

    def _upload_target(self):
        if self.auth is None:
            self.authorize()
        if self.upload is None:
            storage = self.auth["apiInfo"]["storageApi"]
            got = self._call(storage["apiUrl"] + "/b2api/v3/b2_get_upload_url", {"bucketId": self.bucket_id},
                             self.auth["authorizationToken"])
            self.upload = (got["uploadUrl"], got["authorizationToken"])
        return self.upload

    def put(self, name: str, data: bytes, content_type: str, cache_seconds: int) -> None:
        """Uploads one file; on an expired token or a busy pod it re-authorizes / gets a new URL once."""
        for attempt in (1, 2):
            url, token = self._upload_target()
            headers = {
                "Authorization": token,
                "X-Bz-File-Name": urllib.parse.quote(name),
                "Content-Type": content_type,
                "Content-Length": str(len(data)),
                "X-Bz-Content-Sha1": hashlib.sha1(data).hexdigest(),
                "X-Bz-Info-b2-cache-control": urllib.parse.quote(f"public, max-age={cache_seconds}"),
            }
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout):
                    return
            except urllib.error.HTTPError as exc:
                if attempt == 2 or exc.code not in (401, 408, 429, 500, 503):
                    raise B2Error(f"upload of {name} failed: HTTP {exc.code}") from None
                if exc.code == 401:
                    self.authorize()
                else:
                    self.upload = None
                    time.sleep(1)
            except urllib.error.URLError as exc:
                if attempt == 2:
                    raise B2Error(f"upload of {name} failed: {exc.reason}") from None
                self.upload = None
                time.sleep(1)

    def public_url(self, name: str) -> str:
        return f"{self.auth['apiInfo']['storageApi']['downloadUrl']}/file/{self.bucket_name}/{name}"


def mirror_index() -> bytes:
    html = (HERE / "static" / "index.html").read_text(encoding="utf-8")
    html = html.replace('<meta name="projectd-summary" content="/api/summary">',
                        '<meta name="projectd-summary" content="summary.json">\n'
                        '  <meta name="projectd-mode" content="public-mirror">\n'
                        f'  <meta http-equiv="Content-Security-Policy" content="{MIRROR_CSP}">')
    # Relative asset paths: the mirror lives under /file/<bucket>/ on the B2 download host.
    html = html.replace('href="/style.css"', 'href="style.css"').replace('src="/app.js"', 'src="app.js"')
    if 'content="summary.json"' not in html:
        raise RuntimeError("static/index.html is missing the projectd-summary meta tag")
    return html.encode("utf-8")


def publish_assets(b2: B2) -> None:
    b2.put("index.html", mirror_index(), "text/html; charset=utf-8", 300)
    b2.put("app.js", (HERE / "static" / "app.js").read_bytes(), "text/javascript; charset=utf-8", 300)
    b2.put("style.css", (HERE / "static" / "style.css").read_bytes(), "text/css; charset=utf-8", 300)


def publish_summary(b2: B2, influx: server.Influx, bucket: str) -> dict:
    summary = server.build_summary(influx, bucket, datetime.now(timezone.utc))
    summary["mirror"] = True
    b2.put("summary.json", json.dumps(summary).encode(), "application/json", 30)
    return summary


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ
    missing = [k for k in ("INFLUX_TOKEN", "B2_KEY_ID", "B2_APP_KEY", "B2_BUCKET") if not env.get(k)]
    if missing:
        print("Missing environment: " + ", ".join(missing), file=sys.stderr)
        return 2
    for key, value in server.SCHEMA.items():
        if not server.NAME_RE.match(value):
            print(f"Invalid {key} name {value!r}", file=sys.stderr)
            return 2
    influx = server.Influx(env.get("INFLUX_URL", "http://influxdb:8086"), env.get("INFLUX_ORG", "coil"), env["INFLUX_TOKEN"])
    b2 = B2(env["B2_KEY_ID"], env["B2_APP_KEY"], env["B2_BUCKET"], env.get("B2_API_BASE", "https://api.backblazeb2.com"))
    interval = max(30, int(env.get("PUBLISH_INTERVAL", "60")))
    bucket = server.SCHEMA["bucket"]

    def log(message: str) -> None:
        print(f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {message}", flush=True)

    try:
        publish_assets(b2)
        log(f"assets published: {b2.public_url('index.html')}")
    except (B2Error, urllib.error.URLError, OSError, KeyError) as exc:
        log(f"asset upload failed: {type(exc).__name__}: {exc}")
        if "--once" in argv:
            return 1
    assets_at = time.monotonic()
    while True:
        try:
            summary = publish_summary(b2, influx, bucket)
            log(f"summary published: {summary['counts']}")
        except (B2Error, urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            # Errors are logged without request details, so no token can end up in the journal.
            detail = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else f"{type(exc).__name__}: {exc}"
            log(f"publish failed: {detail}")
            if "--once" in argv:
                return 1
        if "--once" in argv:
            return 0
        if time.monotonic() - assets_at > 6 * 3600:   # refresh page assets every 6 h
            try:
                publish_assets(b2)
                assets_at = time.monotonic()
            except (B2Error, urllib.error.URLError, OSError) as exc:
                log(f"asset refresh failed: {type(exc).__name__}")
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
