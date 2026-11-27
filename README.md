# job-radar

A private job search radar. Code reads the open jobs of thousands of companies every day and keeps
only the ones that fit `profile.toml`. The result is one short list: `reports/latest.md`.

## How it works

1. **Discover** (weekly): finds which companies use each applicant-tracking site and adds them to
   `data/companies/<site>.txt`. The sources are two public web archives (Common Crawl and the
   Internet Archive) and two public directories: the LastRound AI ATS Company Directory (CC BY 4.0)
   and ConorsCode/open-jobs-data (MIT).
2. **Collect** (daily): reads each company's public job list, plus six job-board feeds.
3. **Filter** (same run): keeps jobs whose title matches a role in `profile.toml`, that are open to
   someone in Poland, and that are written in English or Polish. Each kept job gets a score.
4. **Report**: `reports/latest.md` lists what is new today and what is still open. A dated copy is kept.

No AI and no secrets are used. Reading the short list with AI is a separate, optional step.

## Sources

| Kind | Sites |
|---|---|
| Applicant-tracking sites | Greenhouse, Lever (global and EU), Ashby, Workable, Recruitee, SmartRecruiters, Teamtailor, Personio, Traffit, BambooHR, Breezy, Pinpoint |
| Corporate career sites | Workday (searched with a few words per company, details read only for likely matches) |
| Job-board feeds | Remotive, Remote OK, Arbeitnow, Himalayas, Jobicy, We Work Remotely, Hacker News hiring threads |
| Aggregators (need free keys) | Adzuna (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`), Jooble (`JOOBLE_KEY`): add them under Settings → Secrets → Actions |

Not covered: LinkedIn (its terms forbid automated collection) and boards without a public feed
(Just Join IT, No Fluff Jobs, Pracuj.pl).

## Use

- **Change what it looks for:** edit `profile.toml` (roles, words, locations, minimum score).
- **Run now:** Actions → Radar → Run workflow. Set "Companies per site" to 50 for a quick trial.
- **Grow the company lists now:** Actions → Discover companies → Run workflow.
- **Add a company by hand:** add its name on the site (the part of the address) to the matching file
  in `data/companies/`.
- **Check the code offline:** `python -m tests.test_offline`.

## Limits

- Large sites are read in rotation. Each run has a time budget per site, and companies not reached
  today come first tomorrow.
- A company that no longer uses a site is retried once a month after three failures.
- The location rule is strict on purpose: a job tied to another country is dropped, even if remote.
- Access is polite: one identified client, a few requests at a time, and a source is left alone for
  the rest of the run when it asks to slow down.

## Second list: parallel work

Besides full roles, the report has a list of work that can run alongside a current contract:
part-time, freelance with flexible hours, other-timezone work (US hours, only when the posting is
open to people abroad), fractional or interim, short-term contracts up to 12 months, jobs of up to a
month, and audits or assessments. The signals live in `profile.toml` under `[[parallel]]`; each match
shows which signals were found and the words that triggered them.

## Job lake: every job, stored once, then daily changes

Besides the matches, each run records **every job it reads** in a Cloudflare R2 bucket
(default name `job-radar`). The first run writes all jobs (the initial load); after that only
new, changed and closed jobs are written.

```
changes/date=YYYY-MM-DD/run-HHMM-NNN.jsonl.gz   one JSON line per event: new / changed / closed
state/jobs.json.gz                              fingerprint, first seen, last seen of every open job
```

A job is closed only when its company was read successfully in that run and the job was gone,
so companies skipped for time do not close anything. Feeds that show only their latest jobs
never close jobs.

Setup: create the bucket in Cloudflare R2, create an R2 API token with Object Read & Write for
that bucket, and add three repository secrets: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY` (optional variable `R2_BUCKET` for another bucket name).
Without them the lake is skipped and the radar works as before.

Reading it with DuckDB (current open jobs = the latest event per job, unless closed):

```sql
INSTALL httpfs; LOAD httpfs;
CREATE SECRET r2 (TYPE r2, KEY_ID '...', SECRET '...', ACCOUNT_ID '...');
CREATE VIEW events AS SELECT * FROM read_json_auto('r2://job-radar/changes/*/*.jsonl.gz', hive_partitioning = true);
CREATE VIEW open_jobs AS
  SELECT * FROM events QUALIFY row_number() OVER (PARTITION BY key ORDER BY seen DESC) = 1 AND event <> 'closed';
SELECT source, count(*) FROM open_jobs GROUP BY 1 ORDER BY 2 DESC;
```
