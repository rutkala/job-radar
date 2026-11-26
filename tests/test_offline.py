"""Offline checks: parsing of each site's format and the profile rules. Run: python -m tests.test_offline"""
import json

from radar import sources as S
from radar.discover import slug_from
from radar.match import Profile, language
from radar.text import job

P = Profile()


def J(title, location, text="", remote=None):
    return job("t", "acme", "1", title, location, "https://x/1", "2026-11-20", text, remote)


BI = ("You own the roadmap for our Power BI reporting product, gather requirements from stakeholders "
      "and work with analytics teams on dashboards and KPIs. ") * 3


def test_parsers():
    g = S.greenhouse_parse("acme", json.dumps({"jobs": [{"id": 1, "title": "Product Owner, BI",
        "location": {"name": "Remote - Poland"}, "absolute_url": "u", "updated_at": "2026-11-20T10:00:00Z",
        "content": "&lt;p&gt;Power BI &amp;amp; analytics&lt;/p&gt;"}]}))
    assert g[0]["title"] == "Product Owner, BI" and g[0]["text"] == "Power BI & analytics", g
    l = S.SITES["lever"][1]("acme", json.dumps([{"id": "a", "text": "Solutions Consultant",
        "categories": {"location": "Warsaw", "allLocations": ["Warsaw", "Remote"]}, "workplaceType": "remote",
        "hostedUrl": "u", "createdAt": 1795000000000, "descriptionPlain": "analytics"}]))
    assert l[0]["remote"] is True and "Warsaw" in l[0]["location"] and l[0]["posted"].startswith("2026"), l
    a = S.ashby_parse("acme", json.dumps({"jobs": [{"id": "x", "title": "BI Lead", "location": "Europe",
        "isRemote": True, "jobUrl": "u", "publishedAt": "2026-11-01T00:00:00Z", "descriptionPlain": "d"}]}))
    assert a[0]["remote"] is True and a[0]["location"] == "Europe"
    w = S.workable_parse("acme", json.dumps({"jobs": [{"shortcode": "S1", "title": "Head of BI", "city": "Kraków",
        "country": "Poland", "telecommuting": True, "url": "u", "published_on": "2026-11-02", "description": "<b>d</b>"}]}))
    assert w[0]["location"] == "Kraków, Poland" and w[0]["text"] == "d"
    r = S.recruitee_parse("acme", json.dumps({"offers": [{"id": 5, "title": "Data Product Owner", "location": "Wrocław, Poland",
        "remote": True, "careers_url": "u", "published_at": "2026-11-03 10:00:00 UTC", "description": "x", "requirements": "y"}]}))
    assert r[0]["text"] == "x y"
    s = S.smartrecruiters_parse("acme", json.dumps({"content": [{"id": "9", "name": "Presales Consultant",
        "location": {"city": "Warsaw", "country": "pl", "remote": True}, "releasedDate": "2026-11-04T00:00:00Z"}]}))
    assert s[0]["url"].endswith("/acme/9") and s[0]["remote"] is True
    t = S.teamtailor_parse("acme", b"""<?xml version="1.0"?><rss xmlns:tt="https://teamtailor.com/locations"><channel>
        <item><title>AI Adoption Manager</title><link>https://a/1</link><guid>1</guid><pubDate>Tue, 03 Nov 2026 10:00:00 +0000</pubDate>
        <description>&lt;p&gt;hello&lt;/p&gt;</description><remoteStatus>fully</remoteStatus>
        <tt:locations><tt:location><tt:city>Warsaw</tt:city><tt:country>Poland</tt:country></tt:location></tt:locations></item></channel></rss>""")
    assert t[0]["remote"] is True and "Warsaw" in t[0]["location"] and t[0]["posted"] == "2026-11-03", t
    p = S.personio_parse("acme", b"""<workzag-jobs><position><id>7</id><name>BI Consultant</name><office>Remote</office>
        <createdAt>2026-11-05T00:00:00+00:00</createdAt><jobDescriptions><jobDescription><name>n</name><value><![CDATA[<p>desc</p>]]></value>
        </jobDescription></jobDescriptions></position></workzag-jobs>""")
    assert p[0]["title"] == "BI Consultant" and p[0]["text"] == "desc", p
    tr = S.traffit_parse("acme", json.dumps([{"id": 3, "url": "u", "valid_start": "2026-11-06 00:00:00",
        "advert": {"name": "Konsultant Power BI", "values": [{"field_id": "description", "value": "<p>opis</p>"}]},
        "options": {"job_location": "{\"locality\":\"Warszawa\",\"country\":\"Polska\"}", "remote": "1"}}]))
    assert tr[0]["location"] == "Warszawa, Polska" and tr[0]["remote"] is True and tr[0]["text"] == "opis", tr


def test_matching():
    ok = lambda j: P.evaluate(j) is not None
    # fits
    assert ok(J("Product Owner (BI Platform)", "Wrocław, Poland (remote)", BI))
    assert ok(J("Senior Solutions Consultant", "Remote - EMEA", BI))
    assert ok(J("AI Adoption Lead", "Remote, Poland"))
    assert ok(J("Head of Business Intelligence", "Europe", BI, remote=True))
    assert ok(J("Konsultant Power BI", "Warszawa", "Praca zdalna. Power BI, wymagania, stakeholders."))
    # does not fit
    assert not ok(J("Product Manager, Payments", "Remote - Europe", "We build payment rails for merchants. " * 5))
    assert not ok(J("Junior Power BI Developer", "Warsaw"))
    assert not ok(J("Software Engineer, Analytics", "Remote - Poland", BI))
    assert not ok(J("Solutions Consultant", "Remote - United States", BI))
    assert not ok(J("Solutions Consultant", "New York, NY", BI))
    assert not ok(J("Product Owner BI", "Remote - Europe", BI + " Must be authorized to work in the United States."))
    assert not ok(J("Product Owner BI (m/w/d)", "Remote, Europe",
                    "Wir suchen für unser Team eine Person mit Erfahrung in Power BI und Analytics. Sie arbeiten mit "
                    "den Fachbereichen und sind für die Roadmap verantwortlich. Wir bieten eine flexible Arbeit und "
                    "die Möglichkeit, mit einem starken Team zu wachsen. Ihre Aufgaben sind nicht nur technisch." * 2))
    # flags and ranking
    m = P.evaluate(J("Power BI Consultant", "Remote - Europe", BI + " Fluent German is required."))
    assert m is None or any("German" in f for f in m["flags"]), m
    hi = P.evaluate(J("Product Owner (BI Platform)", "Remote, Poland", BI))
    lo = P.evaluate(J("Power BI Consultant", "Warsaw", BI))
    assert P.evaluate(J("Power BI Developer", "Warsaw", BI)) is None  # hands-on, office: below the bar
    assert hi["score"] > lo["score"] and "office days" in lo["flags"], (hi, lo)
    bare = P.evaluate(J("Head of Analytics", "Remote", "analytics team"))
    assert bare and bare["where"] == "Remote, region not stated", bare


def test_misc():
    assert language("Wir suchen und bieten für Sie eine Stelle mit der Möglichkeit " * 8) == "de"
    assert language("We are looking for a person who will work with our team and the business " * 6) == "en"
    assert slug_from("https://jobs.lever.co/Acme-Corp/123", "path") == "acme-corp"
    assert slug_from("https://boards.greenhouse.io/embed/job_board?for=acme", "path") == "acme"
    assert slug_from("https://acme.recruitee.com/o/x", "sub") == "acme"
    assert slug_from("https://www.teamtailor.com/en/", "sub") == ""
    assert slug_from("https://acme.jobs.personio.de/job/1", "sub") == "acme"
    from radar.discover import parse_lastround, parse_open_jobs
    d = parse_lastround("ats_vendor,company_name,board_slug,last_crawled\nlever,Acme,Acme-Co,2026-08-01\nworkday,X,x,2026-08-01\n")
    assert d == {"lever": {"acme-co"}}, d
    o = parse_open_jobs(json.dumps([{"name": "A", "platform": "ashby", "slug": "a-co"}, {"name": "W", "platform": "workday", "tenant": "w"}]))
    assert o == {"ashby": {"a-co"}}, o


if __name__ == "__main__":
    test_parsers(); test_matching(); test_misc()
    print("all offline checks passed")
