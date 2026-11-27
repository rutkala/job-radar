"""Daily run: read job lists, keep what fits the profile, write the report."""
import argparse
import datetime as dt
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import feeds, sources
from .http import Gone, Refused, Throttled, get
from .match import Profile
from .text import plain

# Requests in parallel per site (default: --workers). Lower for sites that ask to slow down.
PACE = {"greenhouse": 4, "workable": 2, "personio": 2, "recruitee": 3, "workday": 4, "bamboohr": 3}
TODAY = dt.date.today()
DAY = TODAY.toordinal()


def load_slugs(site):
    path = f"data/companies/{site}.txt"
    if not os.path.exists(path):
        return []
    keep_case = site == "workday"
    with open(path, encoding="utf-8") as f:
        return sorted({(l.strip() if keep_case else l.strip().lower())
                       for l in f if l.strip() and not l.startswith("#")})


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def enrich_smartrecruiters(job):
    """SmartRecruiters lists have no description: fetch it for likely matches only."""
    _, slug, jid = job["id"].split(":", 2)
    try:
        d = json.loads(get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{jid}"))
        secs = (d.get("jobAd") or {}).get("sections") or {}
        job["text"] = plain(" ".join((v or {}).get("text", "") for v in secs.values()))
    except Exception:
        pass


def run_site(site, profile, budget_s, workers, limit, stats, found):
    url_of, parse, headers = sources.SITES.get(site, (None, None, {}))
    fetcher = sources.FETCH.get(site)
    state = load_json(f"data/state/{site}.json", {})  # slug -> [last_day, fails]
    slugs = [s for s in load_slugs(site) if sources.slug_ok(site, s)]
    # Dead addresses are retried once a month; the rest go oldest-checked first.
    due = [s for s in slugs if state.get(s, [0, 0])[1] < 3 or DAY - state[s][0] >= 30]
    due.sort(key=lambda s: state.get(s, [0, 0])[0])
    if limit:
        due = due[:limit]
    st = stats[site] = {"companies": len(slugs), "checked": 0, "jobs": 0, "matches": 0,
                        "gone": 0, "errors": 0, "stopped": ""}
    deadline = time.time() + budget_s
    stop = threading.Event()
    pace = {"pause_until": 0.0, "throttles": 0, "sample": site == "traffit"}
    lock = threading.Lock()
    workers = min(workers, PACE.get(site, workers))

    def one(slug):
        wait = pace["pause_until"] - time.time()
        if wait > 0:
            time.sleep(wait)
        if stop.is_set() or time.time() > deadline:
            return slug, None, "skip"
        try:
            if fetcher:
                return slug, fetcher(slug, profile, get), ""
            raw = get(url_of(slug), headers=headers)
            if pace["sample"] and len(raw) > 60:
                pace["sample"] = False
                print(f"sample {site}/{slug}: {raw[:2500]!r}", flush=True)
            return slug, parse(slug, raw), ""
        except Gone:
            return slug, None, "gone"
        except Refused:
            return slug, None, "gone"
        except Throttled:
            # The site asked us to slow down: everyone waits, and the company is retried next run.
            with lock:
                pace["throttles"] += 1
                pace["pause_until"] = time.time() + 60
                if pace["throttles"] >= 40:
                    stop.set()
                    st["stopped"] = "asked to slow down 40 times; the rest waits for the next run"
            return slug, None, "skip"
        except Exception:
            return slug, None, "error"

    with ThreadPoolExecutor(workers) as pool:
        for fut in as_completed([pool.submit(one, s) for s in due]):
            slug, jobs, err = fut.result()
            if err == "skip":
                continue
            last, fails = state.get(slug, [0, 0])
            if err:
                state[slug] = [DAY, fails + 1]
                st["gone" if err == "gone" else "errors"] += 1
                continue
            state[slug] = [DAY, 0]
            st["checked"] += 1
            st["jobs"] += len(jobs)
            for j in jobs:
                if site == "smartrecruiters" and profile.role_of(j["title"]):
                    enrich_smartrecruiters(j)
                for m in both(profile, j):
                    st["matches"] += 1
                    found.append((j, m))
    if pace["throttles"] and not st["stopped"]:
        st["stopped"] = f"slowed down {pace['throttles']} times"
    if time.time() > deadline and not st["stopped"]:
        st["stopped"] = "time budget reached; the rest is checked on the next runs"
    save_json(f"data/state/{site}.json", state)


def fresh(job, profile):
    """False when the posting date is known and older than the profile allows."""
    try:
        age = (TODAY - dt.date.fromisoformat(job["posted"][:10])).days
    except Exception:
        return True
    return age <= profile.max_age_days


def both(profile, j):
    """The role match and/or the parallel-work match for a job (0, 1 or 2 results)."""
    if not fresh(j, profile) or j["company"].lower() in profile.skip_companies:
        return []
    out = []
    m = profile.evaluate(j)
    if m:
        m["list"] = "roles"
        out.append(m)
    p = profile.parttime(j)
    if p:
        out.append(p)
    return out


def run_feed(name, profile, stats, found):
    st = stats[name] = {"companies": 0, "checked": 1, "jobs": 0, "matches": 0,
                        "gone": 0, "errors": 0, "stopped": ""}
    try:
        jobs = feeds.FEEDS[name]()
    except Exception as e:
        st["errors"], st["stopped"] = 1, f"{type(e).__name__}: {e}"[:80]
        return
    st["jobs"] = len(jobs)
    for j in jobs:
        for m in both(profile, j):
            st["matches"] += 1
            found.append((j, m))


def cell(s):
    return str(s).replace("|", "/").replace("\n", " ")


def table(rows):
    out = ["| Score | Role | Company | Where | Posted | Notes |", "|---|---|---|---|---|---|"]
    for r in rows:
        notes = "; ".join(r["flags"]) if r["flags"] else ""
        out.append(f"| {r['score']} | [{cell(r['title'])}]({r['url']}) | {cell(r['company'])} "
                   f"| {cell(r['where'])}: {cell(r['location'])[:60]} | {r['posted']} | {cell(notes)} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default=",".join(list(sources.SITES) + list(sources.FETCH)))
    ap.add_argument("--feeds", default=",".join(feeds.FEEDS))
    ap.add_argument("--budget-min", type=float, default=30)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="companies per site (0 = all due)")
    a = ap.parse_args()

    profile = Profile()
    stats, found = {}, []
    threads = []
    for site in [s for s in a.sites.split(",") if s]:
        t = threading.Thread(target=run_site, args=(site, profile, a.budget_min * 60, a.workers,
                                                    a.limit, stats, found))
        t.start()
        threads.append(t)
    for name in [f for f in a.feeds.split(",") if f]:
        run_feed(name, profile, stats, found)
    for t in threads:
        t.join()

    # Merge with earlier matches; a job is "new" the first day it is seen.
    matches = load_json("data/matches.json", {})
    new_ids = []
    for j, m in found:
        key = (j["url"] or j["id"]) + ("#parttime" if m["list"] == "parttime" else "")
        rec = matches.get(key)
        if not rec:
            new_ids.append(key)
            rec = {"first_seen": TODAY.isoformat()}
        rec.update({"last_seen": TODAY.isoformat(), "title": j["title"], "company": j["company"],
                    "location": j["location"], "url": j["url"], "posted": j["posted"],
                    "source": j["source"], "snippet": j["text"][:400], **m})
        matches[key] = rec
    cutoff = (TODAY - dt.timedelta(days=profile.keep_days)).isoformat()
    matches = {k: v for k, v in matches.items() if v["last_seen"] >= cutoff}
    save_json("data/matches.json", matches)

    by_score = lambda ids: sorted((matches[i] for i in ids if i in matches), key=lambda r: -r["score"])
    seen_pairs, dupes = set(), []
    for k, v in sorted(matches.items(), key=lambda kv: (-kv[1]["score"], kv[0])):
        pair = (v["company"].lower(), v["title"].lower(), v.get("list", "roles"))
        if pair in seen_pairs:
            dupes.append(k)
        seen_pairs.add(pair)
    for k in dupes:
        del matches[k]
    save_json("data/matches.json", matches)
    new_set = set(new_ids)
    pick = lambda kind, new: by_score([k for k, v in matches.items()
                                       if v.get("list", "roles") == kind and (k in new_set) == new])[:100]
    new_rows, open_rows = pick("roles", True), pick("roles", False)
    pt_new, pt_open = pick("parttime", True), pick("parttime", False)
    show = lambda rows, empty: table(rows) if rows else empty
    lines = [f"# Job radar, {TODAY.isoformat()}", "",
             f"**Roles: {len(new_rows)} new, {len(open_rows)} still open. "
             f"Parallel work: {len(pt_new)} new, {len(pt_open)} still open.**", "",
             "## Roles: new today", "", show(new_rows, "Nothing new today."), "",
             "## Parallel work (alongside the current contract): new today", "", show(pt_new, "Nothing new today."), "",
             "## Roles: still open", "", show(open_rows, "None."), "",
             "## Parallel work: still open", "", show(pt_open, "None."), "",
             "## What was read", "",
             "| Source | Companies known | Checked today | Jobs read | Matches | Gone | Errors | Note |",
             "|---|---|---|---|---|---|---|---|"]
    for name, s in sorted(stats.items()):
        lines.append(f"| {name} | {s['companies']} | {s['checked']} | {s['jobs']} | {s['matches']} "
                     f"| {s['gone']} | {s['errors']} | {cell(s['stopped'])} |")
    report = "\n".join(lines) + "\n"
    os.makedirs("reports", exist_ok=True)
    for path in ("reports/latest.md", f"reports/{TODAY.isoformat()}.md"):
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
    total = sum(s["jobs"] for s in stats.values())
    print(f"jobs read: {total}; matches: {len(found)}; new: {len(new_rows)} roles, {len(pt_new)} parallel")
    for name, s in sorted(stats.items()):
        print(name, s)


if __name__ == "__main__":
    main()
