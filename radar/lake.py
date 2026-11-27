"""Job lake: keep every job read, not only the matches.

Each run compares what it read with the state of the previous runs and writes only the
differences to Cloudflare R2 (S3-compatible):

    changes/date=YYYY-MM-DD/run-HHMM-NNN.jsonl.gz   one line per new, changed or closed job
    state/jobs.json.gz                              key -> [hash, first_seen, last_run, unit]

The first run writes every job as "new" (the initial load); later runs write only the day's
changes. A job is marked "closed" only when its company was read successfully in this run and
the job was missing, so a company that was skipped (time budget, slow site) closes nothing.

Without R2 keys (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY) everything is written
to the local folder lake/ instead, which is useful for tests.
"""
import datetime as dt
import gzip
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.parse
import urllib.request

PART_ROWS = 40000          # rows per change file (keeps each upload well under 100 MB)
FIELDS = ("source", "company", "id", "title", "location", "remote", "url", "posted", "text", "employment")


# ---- storage: R2 (S3 API, signed with AWS Signature V4) or a local folder ----------
class Store:
    def __init__(self, local_dir="lake"):
        self.account = os.environ.get("R2_ACCOUNT_ID", "").strip()
        self.key_id = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
        self.secret = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
        self.bucket = os.environ.get("R2_BUCKET", "job-radar").strip() or "job-radar"
        self.remote = bool(self.account and self.key_id and self.secret)
        self.local_dir = local_dir
        self.host = f"{self.account}.r2.cloudflarestorage.com"

    def where(self):
        return f"R2 bucket {self.bucket}" if self.remote else f"local folder {self.local_dir}/"

    def _request(self, method, key, body=b""):
        now = dt.datetime.now(dt.timezone.utc)
        amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        path = "/" + urllib.parse.quote(f"{self.bucket}/{key}", safe="/-_.~")
        payload = hashlib.sha256(body).hexdigest()
        headers = {"host": self.host, "x-amz-content-sha256": payload, "x-amz-date": amz_date}
        signed = ";".join(sorted(headers))
        canonical = "\n".join([method, path, "",
                               "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)),
                               signed, payload])
        scope = f"{day}/auto/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                             hashlib.sha256(canonical.encode()).hexdigest()])
        k = f"AWS4{self.secret}".encode()
        for part in (day, "auto", "s3", "aws4_request"):
            k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
        headers["authorization"] = (f"AWS4-HMAC-SHA256 Credential={self.key_id}/{scope}, "
                                    f"SignedHeaders={signed}, Signature={sig}")
        req = urllib.request.Request(f"https://{self.host}{path}", data=body if method == "PUT" else None,
                                     method=method, headers={k: v for k, v in headers.items() if k != "host"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise FileNotFoundError(key) from e
                if attempt == 3 or e.code < 500:
                    raise RuntimeError(f"R2 {method} {key}: HTTP {e.code} {e.read()[:300]!r}") from e
            except (urllib.error.URLError, TimeoutError):
                if attempt == 3:
                    raise
            time.sleep(5 * (attempt + 1))

    def put(self, key, body):
        if self.remote:
            self._request("PUT", key, body)
        else:
            path = os.path.join(self.local_dir, key)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(body)

    def get(self, key):
        """Bytes of an object, or None when it does not exist."""
        try:
            if self.remote:
                return self._request("GET", key)
            with open(os.path.join(self.local_dir, key), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None


# ---- the lake ----------------------------------------------------------------------
def job_key(source, unit, j):
    return f"{source}|{unit}|{j.get('id')}"


def fingerprint(j):
    raw = "\x1f".join(str(j.get(f) or "") for f in ("title", "location", "text", "employment", "remote", "url"))
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


class Lake:
    def __init__(self, store=None, now=None):
        self.store = store or Store()
        self.now = now or dt.datetime.now(dt.timezone.utc)
        self.day = self.now.date().isoformat()
        self.run = self.now.strftime("%Y-%m-%dT%H%M")
        self.lock = threading.Lock()
        self.units_ok = set()          # "source|company" read successfully in this run
        self.counts = {"new": 0, "changed": 0, "unchanged": 0, "closed": 0}
        self.part, self.rows, self.buf, self.files = 0, 0, [], []
        self.error = ""
        raw = self.store.get("state/jobs.json.gz")
        self.state = json.loads(gzip.decompress(raw)) if raw else {}
        self.initial = not self.state

    def add(self, source, unit, jobs, closable=True):
        """Record the jobs of one company (or one feed) that was read successfully.
        closable=False for feeds that show only their latest jobs: missing jobs are not closed."""
        u = f"{source}|{unit}"
        with self.lock:
            if closable:
                self.units_ok.add(u)
            for j in jobs:
                key, h = job_key(source, unit, j), fingerprint(j)
                old = self.state.get(key)
                if old and old[0] == h:
                    old[2] = self.run
                    self.counts["unchanged"] += 1
                    continue
                event = "changed" if old else "new"
                first = old[1] if old else self.day
                self.state[key] = [h, first, self.run, u]
                self.counts[event] += 1
                rec = {f: j.get(f) for f in FIELDS}
                rec.update({"event": event, "key": key, "first_seen": first, "seen": self.run})
                self._write(rec)

    def _write(self, rec):
        self.buf.append(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
        self.rows += 1
        if len(self.buf) >= PART_ROWS:
            self._flush()

    def _flush(self):
        if not self.buf:
            return
        self.part += 1
        key = f"changes/date={self.day}/run-{self.now.strftime('%H%M')}-{self.part:03d}.jsonl.gz"
        body = gzip.compress(("\n".join(self.buf) + "\n").encode("utf-8"), 6)
        self.buf = []
        try:
            self.store.put(key, body)
            self.files.append((key, len(body)))
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"[:200]
            raise

    def finish(self):
        """Write closed jobs, the last change file and the new state. Returns a summary line."""
        try:
            with self.lock:
                for key, (h, first, last, unit) in list(self.state.items()):
                    if unit in self.units_ok and last != self.run:
                        src, comp, jid = key.split("|", 2)
                        self._write({"event": "closed", "key": key, "source": src, "company": comp,
                                     "id": jid, "first_seen": first, "seen": self.run})
                        del self.state[key]
                        self.counts["closed"] += 1
                self._flush()
                self.store.put("state/jobs.json.gz",
                               gzip.compress(json.dumps(self.state, separators=(",", ":")).encode(), 6))
        except Exception as e:
            self.error = self.error or f"{type(e).__name__}: {e}"[:200]
        return self.summary()

    def summary(self):
        c = self.counts
        size = sum(n for _, n in self.files) / 1e6
        head = "initial load" if self.initial else "daily changes"
        line = (f"Job lake ({head}, {self.store.where()}): {c['new']} new, {c['changed']} changed, "
                f"{c['closed']} closed, {c['unchanged']} unchanged; {len(self.state)} open jobs known; "
                f"{len(self.files)} file(s), {size:.1f} MB.")
        return line + (f" ERROR: {self.error}" if self.error else "")
