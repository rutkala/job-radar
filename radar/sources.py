"""Applicant-tracking sites: one public job list per company.

Each site has `url(slug)` and `parse(slug, payload)`. `parse` is pure, so it
is tested offline. Slugs come from data/companies/<site>.txt.
"""
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .text import job, plain


def _ms(ts):
    try:
        return datetime.fromtimestamp(int(ts) / 1000, timezone.utc).date().isoformat()
    except Exception:
        return ""


# ---- Greenhouse -----------------------------------------------------------
def greenhouse_url(slug):
    return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def greenhouse_parse(slug, raw):
    out = []
    for j in json.loads(raw).get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        offices = ", ".join(o.get("name", "") for o in j.get("offices") or [])
        out.append(job("greenhouse", slug, j.get("id"), j.get("title"),
                       f"{loc} | {offices}" if offices and offices != loc else loc,
                       j.get("absolute_url"), j.get("updated_at"), j.get("content")))
    return out


# ---- Lever (global and EU hosts) -----------------------------------------
def lever_url(slug):
    return f"https://api.lever.co/v0/postings/{slug}?mode=json"


def lever_eu_url(slug):
    return f"https://api.eu.lever.co/v0/postings/{slug}?mode=json"


def _lever(source):
    def parse(slug, raw):
        out = []
        for j in json.loads(raw):
            c = j.get("categories") or {}
            locs = c.get("allLocations") or [c.get("location", "")]
            wt = (j.get("workplaceType") or "").lower()
            out.append(job(source, slug, j.get("id"), j.get("text"),
                           ", ".join(x for x in locs if x) + (f" ({wt})" if wt else ""),
                           j.get("hostedUrl"), _ms(j.get("createdAt")),
                           (j.get("descriptionPlain") or "") + " " + (j.get("additionalPlain") or ""),
                           remote=True if wt == "remote" else None,
                           employment=c.get("commitment") or ""))
        return out
    return parse


# ---- Ashby ----------------------------------------------------------------
def ashby_url(slug):
    return f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


def ashby_parse(slug, raw):
    out = []
    for j in json.loads(raw).get("jobs", []):
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        comp = ((j.get("compensation") or {}).get("compensationTierSummary")) or ""
        out.append(job("ashby", slug, j.get("id"), j.get("title"),
                       ", ".join(x for x in locs if x), j.get("jobUrl"), j.get("publishedAt"),
                       (j.get("descriptionPlain") or j.get("descriptionHtml") or "") + " " + comp,
                       remote=True if j.get("isRemote") else None,
                       employment=j.get("employmentType") or ""))
    return out


# ---- Workable -------------------------------------------------------------
def workable_url(slug):
    return f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"


def workable_parse(slug, raw):
    out = []
    for j in json.loads(raw).get("jobs", []):
        loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
        out.append(job("workable", slug, j.get("shortcode"), j.get("title"), loc,
                       j.get("url") or j.get("shortlink"), j.get("published_on") or j.get("created_at"),
                       j.get("description"), remote=True if j.get("telecommuting") else None,
                       employment=j.get("employment_type") or ""))
    return out


# ---- Recruitee ------------------------------------------------------------
def recruitee_url(slug):
    return f"https://{slug}.recruitee.com/api/offers/"


def recruitee_parse(slug, raw):
    out = []
    for j in json.loads(raw).get("offers", []):
        loc = j.get("location") or ", ".join(x for x in (j.get("city"), j.get("country")) if x)
        out.append(job("recruitee", slug, j.get("id"), j.get("title"), loc,
                       j.get("careers_url"), j.get("published_at") or j.get("created_at"),
                       (j.get("description") or "") + " " + (j.get("requirements") or ""),
                       remote=True if j.get("remote") else None,
                       employment=j.get("employment_type_code") or ""))
    return out


# ---- SmartRecruiters (list only: no description) --------------------------
def smartrecruiters_url(slug):
    return f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"


def smartrecruiters_parse(slug, raw):
    out = []
    for j in json.loads(raw).get("content", []):
        l = j.get("location") or {}
        loc = ", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x)
        out.append(job("smartrecruiters", slug, j.get("id"), j.get("name"), loc,
                       f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                       j.get("releasedDate"), "", remote=True if l.get("remote") else None,
                       employment=(j.get("typeOfEmployment") or {}).get("label") or ""))
    return out


# ---- Teamtailor (RSS) -----------------------------------------------------
def teamtailor_url(slug):
    return f"https://{slug}.teamtailor.com/jobs.rss"


def teamtailor_parse(slug, raw):
    out = []
    root = ET.fromstring(raw)
    for it in root.iter("item"):
        def t(name):
            e = it.find(name)
            return e.text if e is not None and e.text else ""
        locs = []
        for e in it.iter():
            tag = e.tag.split("}")[-1]
            if tag in ("city", "country", "name") and e.text and e.text not in locs:
                locs.append(e.text)
        rs = ""
        for e in it.iter():
            if e.tag.split("}")[-1] == "remoteStatus" and e.text:
                rs = e.text.lower()
        out.append(job("teamtailor", slug, t("guid") or t("link"), t("title"),
                       ", ".join(locs) + (f" ({rs})" if rs else ""), t("link"),
                       _rfc822(t("pubDate")), t("description"),
                       remote=True if rs in ("fully", "remote") else None))
    return out


def _rfc822(s):
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).date().isoformat()
    except Exception:
        return ""


# ---- Personio (XML) -------------------------------------------------------
def personio_url(slug):
    return f"https://{slug}.jobs.personio.de/xml?language=en"


def personio_parse(slug, raw):
    out = []
    root = ET.fromstring(raw)
    for p in root.iter("position"):
        def t(name):
            e = p.find(name)
            return e.text if e is not None and e.text else ""
        desc = " ".join((v.text or "") for v in p.iter("value"))
        out.append(job("personio", slug, t("id"), t("name"), t("office"),
                       f"https://{slug}.jobs.personio.de/job/{t('id')}?language=en",
                       t("createdAt"), desc))
    return out


# ---- Traffit (Polish; public list) ---------------------------------------
def traffit_url(slug):
    return f"https://{slug}.traffit.com/public/job_posts/published"


def traffit_parse(slug, raw):
    out = []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get("data") or data.get("items") or []
    for j in data:
        adv = j.get("advert") or {}
        opts = j.get("options") or {}
        vals = {}
        for v in adv.get("values") or []:
            vals[v.get("field_id")] = v.get("value")
        loc = opts.get("job_location") or ""
        if isinstance(loc, str) and loc.startswith("{"):
            try:
                l = json.loads(loc)
                loc = ", ".join(x for x in (l.get("locality"), l.get("country")) if x)
            except Exception:
                pass
        remote = str(opts.get("remote") or "") in ("1", "true", "True")
        out.append(job("traffit", slug, j.get("id"), adv.get("name"), loc,
                       j.get("url"), j.get("valid_start") or j.get("awarded_at"),
                       " ".join(plain(x) for x in vals.values() if isinstance(x, str)),
                       remote=True if remote else None))
    return out


SITES = {
    "greenhouse": (greenhouse_url, greenhouse_parse, {}),
    "lever": (lever_url, _lever("lever"), {}),
    "lever_eu": (lever_eu_url, _lever("lever_eu"), {}),
    "ashby": (ashby_url, ashby_parse, {}),
    "workable": (workable_url, workable_parse, {}),
    "recruitee": (recruitee_url, recruitee_parse, {}),
    "smartrecruiters": (smartrecruiters_url, smartrecruiters_parse, {}),
    "teamtailor": (teamtailor_url, teamtailor_parse, {}),
    "personio": (personio_url, personio_parse, {}),
    "traffit": (traffit_url, traffit_parse,
                {"X-Request-Page-Size": "100", "X-Request-Current-Page": "1"}),
}

SLUG = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,59}$", re.I)


# ---- BambooHR -------------------------------------------------------------
def bamboohr_url(slug):
    return f"https://{slug}.bamboohr.com/careers/list"


def bamboohr_parse(slug, raw):
    out = []
    for j in json.loads(raw).get("result", []):
        l = j.get("location") or {}
        loc = ", ".join(x for x in (l.get("city"), l.get("state"), l.get("country")) if x)
        remote = bool(j.get("isRemote")) or str(j.get("locationType") or "") == "1"
        out.append(job("bamboohr", slug, j.get("id"), j.get("jobOpeningName"),
                       loc + (" (remote)" if remote else ""),
                       f"https://{slug}.bamboohr.com/careers/{j.get('id')}", "",
                       f"{j.get('departmentLabel') or ''}", remote=True if remote else None,
                       employment=j.get("employmentStatusLabel") or ""))
    return out


# ---- Breezy HR ------------------------------------------------------------
def breezy_url(slug):
    return f"https://{slug}.breezy.hr/json"


def breezy_parse(slug, raw):
    out = []
    for j in json.loads(raw):
        l = j.get("location") or {}
        loc = l.get("name") or ", ".join(x for x in ((l.get("city") or ""), ((l.get("country") or {}).get("name") or "")) if x)
        remote = bool(l.get("is_remote"))
        out.append(job("breezy", slug, j.get("id"), j.get("name"), loc + (" (remote)" if remote else ""),
                       j.get("url"), j.get("published_date"), j.get("description") or "",
                       remote=True if remote else None, employment=(j.get("type") or {}).get("name") or ""))
    return out


# ---- Pinpoint -------------------------------------------------------------
def pinpoint_url(slug):
    return f"https://{slug}.pinpointhq.com/postings.json"


def pinpoint_parse(slug, raw):
    out = []
    for j in json.loads(raw).get("data", []):
        l = j.get("location") or {}
        wt = str(j.get("workplace_type") or "").lower()
        loc = l.get("name") or ", ".join(x for x in (l.get("city"), l.get("province")) if x)
        out.append(job("pinpoint", slug, j.get("id"), j.get("title"), loc + (f" ({wt})" if wt else ""),
                       j.get("url"), "", (j.get("description") or "") + " " + (j.get("key_responsibilities") or ""),
                       remote=True if wt == "remote" else None, employment=j.get("employment_type_text") or ""))
    return out


SITES.update({
    "bamboohr": (bamboohr_url, bamboohr_parse, {}),
    "breezy": (breezy_url, breezy_parse, {}),
    "pinpoint": (pinpoint_url, pinpoint_parse, {}),
})


# ---- Workday (search, then details for likely matches) ---------------------
# Company entries look like "tenant/wd3/SiteName" (the site name is case-sensitive).
WORKDAY = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,60}/wd\d{1,3}/[A-Za-z0-9_\-]{1,80}$")
WORKDAY_SEARCHES = ["analytics", "business intelligence", "power bi", "product owner", "reporting"]


def slug_ok(site, slug):
    return bool(WORKDAY.match(slug)) if site == "workday" else bool(SLUG.match(slug))


def workday_fetch(slug, profile, get):
    """Search the company's Workday site with a few words, then read details of jobs whose
    title fits a role. Returns parsed jobs (only those worth evaluating)."""
    tenant, wd, site = slug.split("/")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}"
    seen, out = set(), []
    for word in WORKDAY_SEARCHES:
        body = json.dumps({"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": word}).encode()
        data = json.loads(get(f"{api}/jobs", data=body,
                              headers={"Content-Type": "application/json", "Accept": "application/json"}))
        for p in data.get("jobPostings") or []:
            path = p.get("externalPath") or ""
            title = p.get("title") or ""
            if not path or path in seen:
                continue
            seen.add(path)
            if not (profile.role_of(title) or profile.pt_field.search(title)):
                continue
            text, loc, remote, posted = "", p.get("locationsText") or "", None, ""
            try:
                info = json.loads(get(f"{api}{path}", headers={"Accept": "application/json"})).get("jobPostingInfo") or {}
                text = info.get("jobDescription") or ""
                locs = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
                loc = ", ".join(x for x in locs if x) or loc
                rt = str(info.get("remoteType") or "")
                if "remote" in rt.lower():
                    remote, loc = True, f"{loc} ({rt})"
                posted = info.get("startDate") or ""
            except Exception:
                pass
            out.append(job("workday", tenant, path.rsplit("/", 1)[-1], title, loc,
                           f"{base}/{site}{path}", posted, text, remote=remote,
                           employment=p.get("timeType") or ""))
    return out


FETCH = {"workday": workday_fetch}
