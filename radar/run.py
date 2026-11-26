"""Daily run: read job lists, keep what fits the profile, write the report."""
import argparse
import datetime as dt
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import feeds, sources
from .http import Gone, Throttled, get
from .match import Profile
from .text import plain

TODAY = dt.date.today()
DAY = TODAY.toordinal()


def load_slugs(site):
    path = f"data/companies/{site}.txt"
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return sorted({l.strip().lower() for l in f if l.strip() and not l.startswith("#")})


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
    url_of, parse, headers = sources.SITES[site]
    state = load_json(f"data/state/{site}.json", {})  # slug -> [last_day, fails]
    slugs = [s for s in load_slugs(site) if sources.SLUG.match(s)]
    # Dead addresses are retried once a month; the rest go oldest-checked first.
    due = [s for s in slugs if state.get(s, [0, 0])[1] < 3 or DAY - state[s][0] >= 30]
    due.sort(key=lambda s: state.get(s, [0, 0])[0])
    if limit:
        due = due[:limit]
    st = stats[site] = {"companies": len(slugs), "checked": 0, "jobs": 0, "matches": 0,
                        "gone": 0, "errors": 0, "stopped": ""}
    deadline = time.time() + budget_s
    stop = threading.Event()

    def one(slug):
        if stop.is_set() or time.time() > deadline:
            return slug, None, "skip"
        try:
            return slug, parse(slug, get(url_of(slug), headers=headers)), ""
        except Gone:
            return slug, None, "gone"
        except Throttled as e:
            stop.set()
            st["stopped"] = str(e)[:80]
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
                m = profile.evaluate(j)
                if m:
                    st["matches"] += 1
                    found.append((j, m))
    if time.time() > deadline and not st["stopped"]:
        st["stopped"] = "time budget reached; the rest is checked on the next runs"
    save_json(f"data/state/{site}.json", state)


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
        m = profile.evaluate(j)
        if m:
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
    ap.add_argument("--sites", default=",".join(sources.SITES))
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
        key = j["url"] or j["id"]
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
    new_rows = by_score(new_ids)
    open_rows = by_score([k for k in matches if k not in set(new_ids)])[:100]
    lines = [f"# Job radar, {TODAY.isoformat()}", "",
             f"**New today: {len(new_rows)}.** Still open from earlier days: {len(open_rows)}.", "",
             "## New today", "", table(new_rows) if new_rows else "Nothing new today.", "",
             "## Still open", "", table(open_rows) if open_rows else "None.", "",
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
    print(f"jobs read: {total}; matches: {len(found)}; new: {len(new_rows)}")
    for name, s in sorted(stats.items()):
        print(name, s)


if __name__ == "__main__":
    main()
