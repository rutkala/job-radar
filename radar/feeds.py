"""Job boards that publish one public feed for all their jobs."""
import json
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET

from .http import get
from .text import job, plain


def remotive():
    data = json.loads(get("https://remotive.com/api/remote-jobs"))
    return [job("remotive", j.get("company_name"), j.get("id"), j.get("title"),
                j.get("candidate_required_location"), j.get("url"),
                j.get("publication_date"), j.get("description"), remote=True)
            for j in data.get("jobs", [])]


def remoteok():
    data = json.loads(get("https://remoteok.com/api"))
    return [job("remoteok", j.get("company"), j.get("id"), j.get("position"),
                j.get("location") or "Remote", j.get("url"), j.get("date"),
                (j.get("description") or "") + " " + " ".join(j.get("tags") or []), remote=True)
            for j in data if isinstance(j, dict) and j.get("position")]


def arbeitnow():
    out = []
    for page in range(1, 6):
        data = json.loads(get(f"https://www.arbeitnow.com/api/job-board-api?page={page}"))
        rows = data.get("data", [])
        if not rows:
            break
        for j in rows:
            out.append(job("arbeitnow", j.get("company_name"), j.get("slug"), j.get("title"),
                           j.get("location"), j.get("url"), "", j.get("description"),
                           remote=True if j.get("remote") else None))
    return out


def himalayas():
    out = []
    for offset in range(0, 1000, 100):
        data = json.loads(get(f"https://himalayas.app/jobs/api?limit=100&offset={offset}"))
        rows = data.get("jobs", [])
        if not rows:
            break
        for j in rows:
            loc = ", ".join(j.get("locationRestrictions") or []) or "Worldwide"
            out.append(job("himalayas", j.get("companyName"), j.get("guid") or j.get("applicationLink"),
                           j.get("title"), loc, j.get("applicationLink") or j.get("guid"),
                           "", (j.get("description") or j.get("excerpt") or ""), remote=True))
    return out


def jobicy():
    out = []
    for tag in ("data", "business", "product", "sales"):
        data = json.loads(get(f"https://jobicy.com/api/v2/remote-jobs?count=50&industry={tag}"))
        for j in data.get("jobs", []):
            out.append(job("jobicy", j.get("companyName"), j.get("id"), j.get("jobTitle"),
                           j.get("jobGeo"), j.get("url"), j.get("pubDate"),
                           j.get("jobDescription") or j.get("jobExcerpt"), remote=True))
    return out


def weworkremotely():
    root = ET.fromstring(get("https://weworkremotely.com/remote-jobs.rss"))
    out = []
    for it in root.iter("item"):
        def t(name):
            e = it.find(name)
            return e.text if e is not None and e.text else ""
        title = t("title")
        company, _, role = title.partition(": ")
        out.append(job("weworkremotely", company if role else "", t("guid") or t("link"),
                       role or title, t("region") or "Remote", t("link"), "", t("description"),
                       remote=True))
    return out


FEEDS = {
    "remotive": remotive,
    "remoteok": remoteok,
    "arbeitnow": arbeitnow,
    "himalayas": himalayas,
    "jobicy": jobicy,
    "weworkremotely": weworkremotely,
}


# ---- Hacker News: monthly "Who is hiring?" and "Freelancer? Seeking freelancer?" ----
def hackernews():
    stories = json.loads(get("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=6"))
    out = []
    for s in stories.get("hits", []):
        title = s.get("title") or ""
        if not re.search(r"who is hiring|seeking freelancer", title, re.I):
            continue
        freelance = "freelancer" in title.lower()
        item = json.loads(get(f"https://hn.algolia.com/api/v1/items/{s['objectID']}", timeout=90))
        posted = (item.get("created_at") or "")[:10]
        for c in item.get("children") or []:
            text = plain(c.get("text") or "")
            if not text or (freelance and not text.upper().startswith("SEEKING FREELANCER")):
                continue
            head = text.split("  ")[0][:200] if "  " in text[:200] else text[:160]
            first = re.split(r"(?<=[a-z0-9)])\s+(?=[A-Z][a-z])", head, maxsplit=1)[0] if "|" not in head else head
            company = first.split("|")[0].strip()[:60]
            out.append(job("hackernews", company, c.get("id"), first, first,
                           f"https://news.ycombinator.com/item?id={c.get('id')}", posted, text,
                           remote=True if re.search(r"\bremote\b", first, re.I) else None,
                           employment="freelance" if freelance else ""))
    return out


# ---- Adzuna (needs free keys: ADZUNA_APP_ID, ADZUNA_APP_KEY) -----------------
ADZUNA_COUNTRIES = ["pl", "gb", "nl", "de", "at", "ch", "fr", "it", "es", "be"]
SEARCH_WORDS = ["power bi", "business intelligence", "microsoft fabric", "bi product owner",
                "analytics consultant", "people analytics", "ai adoption"]


def adzuna():
    app_id, key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and key):
        raise RuntimeError("no ADZUNA_APP_ID / ADZUNA_APP_KEY secret: skipped")
    out = []
    for country in ADZUNA_COUNTRIES:
        for word in SEARCH_WORDS:
            q = urllib.parse.urlencode({"app_id": app_id, "app_key": key, "what_phrase": word,
                                        "results_per_page": 50, "max_days_old": 60})
            try:
                data = json.loads(get(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1?{q}"))
            except Exception:
                continue
            for j in data.get("results", []):
                loc = (j.get("location") or {}).get("display_name") or ""
                if country != "pl":
                    loc = f"{loc} ({country.upper()})"
                out.append(job("adzuna", (j.get("company") or {}).get("display_name"), j.get("id"),
                               j.get("title"), loc, j.get("redirect_url"), j.get("created"),
                               j.get("description"), employment=j.get("contract_time") or ""))
    return out


# ---- Jooble (needs a free key: JOOBLE_KEY) ------------------------------------
def jooble():
    key = os.environ.get("JOOBLE_KEY")
    if not key:
        raise RuntimeError("no JOOBLE_KEY secret: skipped")
    out = []
    for loc in ("Poland", "Remote"):
        for word in SEARCH_WORDS:
            body = json.dumps({"keywords": word, "location": loc, "page": "1"}).encode()
            try:
                data = json.loads(get(f"https://jooble.org/api/{key}", data=body,
                                      headers={"Content-Type": "application/json"}))
            except Exception:
                continue
            for j in data.get("jobs", []):
                out.append(job("jooble", j.get("company"), j.get("id"), j.get("title"),
                               j.get("location"), j.get("link"), j.get("updated"),
                               j.get("snippet"), employment=j.get("type") or ""))
    return out


FEEDS.update({"hackernews": hackernews, "adzuna": adzuna, "jooble": jooble})
