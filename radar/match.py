"""Decide whether a job fits the profile, and how well. Pure code, no AI."""
import re
import tomllib

STOP = {
    "en": "the and for with you our are will that this your have from work team",
    "pl": "oraz dla jest się nie przez które jako pracy lub także naszych wymagania oferujemy",
    "de": "und der die das für mit wir sie ihre einen eine nicht auch wird oder sowie bei",
    "fr": "les des pour vous nous avec une dans est sur que notre votre aux",
    "nl": "het een voor van met jij wij onze naar ook bij zijn wordt je",
    "es": "los las para con una del que por nuestro como más sus",
    "it": "per con che del una gli nel alla sono nostro delle anche",
    "pt": "para com uma que dos nos como você nossa mais são",
    "sv": "och för med att som det vi dig vår har eller till",
    "da": "og for med til som det vi dig vores har eller af",
    "no": "og for med til som det vi deg vår har eller av",
    "fi": "ja on että sekä tai meillä sinulla kanssa myös olet",
    "cs": "pro nebo jsme jako které práce naše budete také máte",
}
STOP = {k: set(v.split()) for k, v in STOP.items()}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def language(text):
    """Best guess of the posting language from common words; '' if unsure."""
    words = [w.lower() for w in _WORD.findall(text[:4000])]
    if len(words) < 40:
        return ""
    best, hits = "", 0
    for lang, stop in STOP.items():
        n = sum(1 for w in words if w in stop)
        if n > hits:
            best, hits = lang, n
    return best if hits / len(words) >= 0.05 else ""


class Profile:
    def __init__(self, path="profile.toml"):
        with open(path, "rb") as f:
            c = tomllib.load(f)
        rx = lambda p: re.compile(p, re.I)
        self.min_score = c["min_score"]
        self.keep_days = c["keep_days"]
        self.max_age_days = c.get("max_age_days", 120)
        self.skip_companies = {x.lower() for x in c.get("skip_companies", [])}
        self.roles = [(r["name"], r["weight"], r["needs_context"], rx(r["title"])) for r in c["role"]]
        self.exclude = rx(c["exclude"]["title"])
        self.context = rx(c["context"]["any"])
        self.strong = rx(c["context"]["strong"])
        self.bonus = [(n, v[0], rx(v[1])) for n, v in c["bonus"].items()]
        L = c["location"]
        self.poland, self.europe, self.remote = rx(L["poland"]), rx(L["europe"]), rx(L["remote"])
        self.remote_from = rx(L["remote_from"])
        self.pts = {k: L[k] for k in ("poland_remote", "poland_remote_in_text", "europe_remote",
                                      "poland_office", "unclear")}
        M = c["limits"]
        self.foreign = set(M["foreign_languages"])
        self.lang_penalty = M["language_penalty"]
        self.req_lang = rx(M["required_language"])
        self.blocked = rx(M["blocked"])
        pt = c["parttime"]
        self.pt_base = pt["base"]
        self.pt_field, self.pt_exclude = rx(pt["field"]), rx(pt["exclude"])
        self.pt_americas, self.pt_abroad = rx(pt["americas"]), rx(pt["abroad"])
        self.signals = [(p["name"], p["points"], p["where"], rx(p["pattern"]), p.get("requires", ""))
                        for p in c["parallel"]]
        self._lang_ctx = re.compile(
            r"(fluent|fluency|native|proficien\w+|business[- ]level|c1|c2|required|mandatory|must|speaker)", re.I)

    def role_of(self, title):
        """Best role whose title pattern matches, ignoring context. None if excluded."""
        if self.exclude.search(title):
            return None
        best = None
        for name, weight, needs, pat in self.roles:
            if pat.search(title) and (best is None or weight > best[1]):
                best = (name, weight, needs)
        return best

    def place(self, job):
        """Return (points, label) or None when the job is not open to Poland."""
        loc, title, text = job["location"], job["title"], job["text"]
        head = f"{loc} {title}"
        is_remote = bool(job.get("remote")) or bool(self.remote.search(head))
        if self.poland.search(head):
            if is_remote:
                return self.pts["poland_remote"], "Poland, remote"
            if self.remote.search(text):
                return self.pts["poland_remote_in_text"], "Poland, remote mentioned in the text"
            return self.pts["poland_office"], "Poland, office or hybrid"
        if is_remote and self.europe.search(loc):
            return self.pts["europe_remote"], "Remote, Europe or worldwide"
        if is_remote and not self.remote.sub("", loc).strip(" ,-()|/"):
            if self.poland.search(text) or self.europe.search(text):
                return self.pts["europe_remote"] - 10, "Remote, region not stated"
            return self.pts["unclear"], "Remote, region not stated"
        if self.remote_from.search(text):
            return self.pts["europe_remote"] - 5, "Text says remote from Poland or Europe"
        if not loc.strip():
            if self.remote.search(text) and (self.poland.search(text) or self.europe.search(text)):
                return self.pts["unclear"], "Location unclear, remote mentioned"
            return None
        return None  # a named place outside Poland, or remote tied to another country

    def signals_of(self, job):
        """Parallel-work signals found in a job: list of (name, points, matched text)."""
        head = f"{job['title']} | {job.get('employment', '')}"
        text = job["text"]
        hits = []
        for name, pts, where, pat, _ in self.signals:
            m = (where in ("title", "both") and pat.search(head)) or \
                (where in ("text", "both") and pat.search(text))
            if m:
                hits.append((name, pts, m.group(0)))
        names = {h[0] for h in hits}
        keep = []
        reqs = {n: r for n, _, _, _, r in self.signals}
        for h in hits:
            r = reqs.get(h[0])
            if r and not any(n.startswith(r) for n in names if n != h[0]):
                continue
            keep.append(h)
        return keep

    def parttime(self, job):
        """Work that can run in parallel to the current contract. Return a match dict, or None."""
        title, text = job["title"], job["text"]
        if self.pt_exclude.search(title) or not self.pt_field.search(title):
            return None
        if not (self.context.search(title) or self.strong.search(text)):
            return None
        hits = self.signals_of(job)
        if not hits:
            return None
        if self.blocked.search(text) or (text and language(text) in self.foreign):
            return None
        where = self.place(job)
        names = {h[0] for h in hits}
        if not where and "other timezone (late-afternoon work)" in names:
            loc = job["location"]
            remote = job.get("remote") or self.remote.search(f"{loc} {title}")
            if remote and self.pt_americas.search(loc) and self.pt_abroad.search(text):
                where = (self.pts["unclear"], "Remote, other timezone, open to people abroad")
        if not where:
            return None
        best = max(hits, key=lambda h: h[1])
        score = self.pt_base + where[0] + best[1] + min(5 * (len(hits) - 1), 10)
        why, extra = [], 0
        for n, pts, pat in self.bonus:
            if pat.search(title) or pat.search(text):
                extra += pts
                why.append(n)
        score += min(extra, 30)
        flags = ["; ".join(f"{n}: \"{m.strip()[:40].lower()}\"" for n, _, m in hits)]
        for m in self.req_lang.finditer(text):
            if self._lang_ctx.search(text[max(0, m.start() - 60): m.end() + 60]):
                flags.append(f"may require {m.group(0).title()}")
                score -= self.lang_penalty
                break
        if score < self.min_score:
            return None
        kind = ", ".join(sorted(names - {"freelance / contract (in text)"} or names))
        return {"score": score, "role": f"Parallel: {kind}", "where": where[1], "why": why,
                "flags": flags, "list": "parttime"}

    def evaluate(self, job):
        """Return a match dict, or None."""
        role = self.role_of(job["title"])
        if not role:
            return None
        name, weight, needs = role
        title, text = job["title"], job["text"]
        if needs and not self.context.search(title) and not self.strong.search(text):
            return None
        if self.blocked.search(text):
            return None
        if text and language(text) in self.foreign:
            return None
        where = self.place(job)
        if not where:
            return None
        score = weight + where[0]
        why = []
        extra = 0
        for n, pts, pat in self.bonus:
            if pat.search(title) or pat.search(text):
                extra += pts
                why.append(n)
        score += min(extra, 30)
        flags = []
        for m in self.req_lang.finditer(text):
            window = text[max(0, m.start() - 60): m.end() + 60]
            if self._lang_ctx.search(window):
                flags.append(f"may require {m.group(0).title()}")
                score -= self.lang_penalty
                break
        if re.search(r"\bdach\b|\(m/w/d\)", title, re.I):
            flags.append("German-speaking market")
            score -= 15
        if where[1].startswith("Poland, office"):
            flags.append("office days")
        if score < self.min_score:
            return None
        return {"score": score, "role": name, "where": where[1], "why": why, "flags": flags}
