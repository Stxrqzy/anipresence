"""Resolve anime clicks to the user's preferred streaming search."""
from urllib.parse import quote, urlsplit

SITES = {
    "ani.pm": "https://ani.pm/anime?q={query}",
    "Crunchyroll": "https://www.crunchyroll.com/search?q={query}",
    "Netflix": "https://www.netflix.com/search?q={query}",
}


def valid_search_template(template):
    parsed = urlsplit(template)
    return parsed.scheme in ("http", "https") and bool(parsed.hostname) and "{query}" in template


def anime_url(entry, site="ani.pm", custom=""):
    template = custom if site == "Custom" and valid_search_template(custom) else SITES.get(site, SITES["ani.pm"])
    # Preserve the exact episode when it already belongs to the selected site.
    url = str(entry.get("url") or "")
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    target = (urlsplit(template).hostname or "").removeprefix("www.")
    if urlsplit(url).scheme in ("http", "https") and host == target:
        return url
    title = entry.get("title_english") or entry.get("title") or ""
    return template.replace("{query}", quote(title, safe=""))
