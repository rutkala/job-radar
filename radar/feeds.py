"""Job boards that publish one public feed for all their jobs."""
import json
import xml.etree.ElementTree as ET

from .http import get
from .text import job


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
