import html
import re

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def plain(s, limit=6000):
    """HTML or text -> single-line plain text, cut to `limit` characters."""
    if not s:
        return ""
    s = html.unescape(html.unescape(str(s)))
    s = _TAG.sub(" ", s)
    return _WS.sub(" ", s).strip()[:limit]


def job(source, company, jid, title, location, url, posted="", text="", remote=None, employment=""):
    return {
        "source": source,
        "company": str(company or ""),
        "id": f"{source}:{company}:{jid}",
        "title": plain(title, 200),
        "location": plain(location, 200),
        "remote": remote,
        "url": url or "",
        "posted": str(posted or "")[:10],
        "text": plain(text),
        "employment": plain(employment, 60),
    }
