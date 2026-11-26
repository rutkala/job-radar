"""Small polite HTTP helper (standard library only)."""
import gzip
import json
import time
import urllib.error
import urllib.request

UA = "job-radar/1.0 (personal job search; contact via GitHub rutkala)"


class Gone(Exception):
    """The address does not exist (404/410): the company left this site."""


class Throttled(Exception):
    """The site asked us to slow down (429)."""


class Refused(Exception):
    """This one address is not public (401/403)."""


def get(url, timeout=25, retries=2, data=None, headers=None):
    hdrs = {"User-Agent": UA, "Accept-Encoding": "gzip", "Accept": "*/*"}
    hdrs.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise Gone(url)
            if e.code == 429:
                raise Throttled(f"429 {url}")
            if e.code in (401, 403):
                raise Refused(url)
            last = e
        except Exception as e:  # timeouts, resets
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise last


def get_json(url, **kw):
    return json.loads(get(url, **kw).decode("utf-8", "replace"))
