"""Find which companies use each applicant-tracking site.

No site publishes its customer list. Common Crawl (a free, public archive of
the web) records every address it has seen, so the company part of addresses
such as jobs.lever.co/<company> gives the list. New companies are appended to
data/companies/<site>.txt; nothing is ever removed here.
"""
import argparse
import datetime as dt
import json
import os
import re
import time
from urllib.parse import parse_qs, quote, urlparse

from .http import Throttled, get
from .sources import SLUG

# site -> list of (pattern, kind). kind "path": company is the first path part;
# "sub": company is the subdomain.
PATTERNS = {
    "greenhouse": [("boards.greenhouse.io/*", "path"), ("job-boards.greenhouse.io/*", "path"),
                   ("job-boards.eu.greenhouse.io/*", "path")],
    "lever": [("jobs.lever.co/*", "path")],
    "lever_eu": [("jobs.eu.lever.co/*", "path")],
    "ashby": [("jobs.ashbyhq.com/*", "path")],
    "workable": [("apply.workable.com/*", "path")],
    "smartrecruiters": [("jobs.smartrecruiters.com/*", "path"), ("careers.smartrecruiters.com/*", "path")],
    "recruitee": [("recruitee.com", "sub")],
    "teamtailor": [("teamtailor.com", "sub")],
    "personio": [("jobs.personio.de", "sub"), ("jobs.personio.com", "sub")],
    "traffit": [("traffit.com", "sub")],
}
RESERVED = set("""www api app apps jobs job careers career embed static assets help support blog docs cdn
status auth login signup robots.txt sitemap.xml favicon.ico js css images img v0 v1 public en de pl fr
o j api-docs widget oauth sso admin mail email go info about legal privacy terms search company companies
integrations partners pricing resources developers developer hire hiring talent""".split())


def slug_from(url, kind):
    u = urlparse(url if "://" in url else "http://" + url)
    if kind == "sub":
        s = (u.hostname or "").split(".")[0]
    else:
        parts = [p for p in u.path.split("/") if p]
        s = parts[0] if parts else ""
        if s == "embed":  # greenhouse embed/job_board?for=<company>
            s = (parse_qs(u.query).get("for") or [""])[0]
    s = s.lower()
    return s if SLUG.match(s) and s not in RESERVED else ""


def cc_get(url):
    for attempt in range(5):
        try:
            return get(url, timeout=60, retries=0)
        except Throttled:
            time.sleep(20 * (attempt + 1))
        except Exception:
            time.sleep(8 * (attempt + 1))
    return b""


def harvest(api, pattern, kind, max_pages):
    q = f"{api}?url={quote(pattern, safe='*')}&output=json&fl=url"
    if kind == "sub":
        q += "&matchType=domain"
    try:
        total = json.loads(cc_get(q + "&showNumPages=true")).get("pages", 0)
    except Exception:
        total = 0
    if not total:
        return set(), 0, 0
    # Large sites: read a different window of pages each week, so all pages get covered over time.
    week = dt.date.today().isocalendar()[1]
    start = (week * max_pages) % total if total > max_pages else 0
    pages = [(start + i) % total for i in range(min(total, max_pages))]
    found = set()
    for p in pages:
        body = cc_get(f"{q}&page={p}").decode("utf-8", "replace")
        for line in body.splitlines():
            try:
                s = slug_from(json.loads(line)["url"], kind)
            except Exception:
                continue
            if s:
                found.add(s)
        time.sleep(1.5)
    return found, len(pages), total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default=",".join(PATTERNS))
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--indexes", type=int, default=2, help="how many recent crawls to read")
    a = ap.parse_args()
    info = json.loads(get("https://index.commoncrawl.org/collinfo.json", timeout=60))
    apis = [c["cdx-api"] for c in info[: a.indexes]]
    os.makedirs("data/companies", exist_ok=True)
    for site in [s for s in a.sites.split(",") if s]:
        path = f"data/companies/{site}.txt"
        known = set()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                known = {l.strip().lower() for l in f if l.strip() and not l.startswith("#")}
        before = len(known)
        for api in apis:
            for pattern, kind in PATTERNS[site]:
                found, read, total = harvest(api, pattern, kind, a.max_pages)
                known |= found
                print(f"{site}: {pattern} in {api.rsplit('/', 1)[-1]}: {len(found)} companies "
                      f"from {read} of {total} pages", flush=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(known)) + "\n")
        print(f"{site}: {before} -> {len(known)} companies", flush=True)


if __name__ == "__main__":
    main()
