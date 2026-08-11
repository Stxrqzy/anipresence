"""Title normalization helpers shared by detection and matching."""

import re

# Tokens that are almost always junk in an extracted slug (site chrome, sub/dub
# tags, language labels). We deliberately keep words like 'the'/'a' because they
# can be part of real titles and removing them hurts AniList search.
_STOP_TOKENS = {
    "watch", "watching", "stream", "streaming", "online", "free", "hd",
    "sub", "dub", "subbed", "dubbed", "english", "japanese", "episode",
    "ep", "episodes", "season", "s", "crunchyroll", "gogoanime", "9anime",
    "aniwave",
}


def normalize_search(title):
    """Normalize a string for cache keys and fuzzy scoring.

    Lowercases, strips parentheticals and punctuation, and collapses
    whitespace. Keeps CJK characters intact.
    """
    s = (title or "").lower()
    s = re.sub(r"\([^)]*\)", " ", s)                      # drop "(...) Notes"
    s = re.sub(r"[^a-z0-9\u3040-\u30ff\u4e00-\u9fff\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_slug(slug):
    """Turn a URL slug into a human-readable title.

    Examples:
        'my-hero-academia-episode-12'      -> 'My Hero Academia'
        'one-piece-1089'                   -> 'One Piece'
        'dance-dance-danseur-12'           -> 'Dance Dance Danseur'
        '86'                               -> '86'   (pure-number title)
    """
    s = (slug or "").strip().strip("/")
    s = re.sub(r"[._]+", "-", s)
    # Strip season/episode markers as units: '-season-5', '-episode-3', '-s2'.
    s = re.sub(r"-(?:episode|ep)-?(\d+)\b", " ", s, flags=re.I)
    s = re.sub(r"-(?:season|s)-?(\d+)\b", " ", s, flags=re.I)

    tokens = [t for t in re.split(r"[\s\-]+", s) if t]
    if len(tokens) == 1 and tokens[0].isdigit():
        return tokens[0]  # numeric-only title like "86"

    # Trailing numeric tokens are almost always episode numbers.
    while tokens and tokens[-1].isdigit():
        tokens.pop()

    kept = [t for t in tokens if t.lower() not in _STOP_TOKENS]
    if not kept:
        kept = tokens
    return " ".join(kept).strip()


def parse_episode(text):
    """Extract an episode number from arbitrary text (page title, slug, ...)."""
    if not text:
        return None
    m = re.search(r"\b(?:episode|ep)\.?\s*(\d{1,4})\b", text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\bS(\d{1,3})\s*[:\s_.-]*\s*E(\d{1,4})\b", text, re.I)
    if m:
        return int(m.group(2))
    m = re.search(r"\b第\s*(\d+)\s*[話話集]\b", text)
    if m:
        return int(m.group(1))
    return None


def strip_trailing_season(text):
    """Remove a trailing 'Season 2' / 'S2' marker from a title string."""
    if not text:
        return ""
    return re.sub(r"\s*[–—-]\s*(?:Season\s*\d+|S\d+)\s*$", "", text, flags=re.I).strip()


def significant_tokens(title):
    """Meaningful tokens (skips site chrome, single letters, numbers)."""
    tokens = re.findall(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff]+", title or "")
    out = []
    for token in tokens:
        low = token.lower()
        if low in _STOP_TOKENS or len(low) <= 1 or low.isdigit():
            continue
        out.append(token)
    return out


def search_terms(title):
    """Candidate AniList search strings, most specific first.

    AniList search is strict (an extra trailing word can return zero results),
    so we try progressively shorter prefixes of the meaningful title tokens:
        'Frieren Beyond Journeys End' ->
            ['Frieren Beyond Journeys End',
             'Frieren Beyond Journeys',
             'Frieren Beyond',
             'Frieren']
    The caller stops at the first term that yields results, then the fuzzy
    matcher scores those candidates against the *full* extracted title.
    """
    terms = [(title or "").strip()]
    stripped = re.sub(r"\([^)]*\)", "", title or "").strip()
    if stripped and stripped.lower() != terms[0].lower():
        terms.append(stripped)
    tokens = significant_tokens(stripped or title)
    if len(tokens) >= 3:
        terms.append(" ".join(tokens[:3]))
    if len(tokens) >= 2:
        terms.append(" ".join(tokens[:2]))
    if tokens:
        terms.append(tokens[0])

    seen, ordered = set(), []
    for term in terms:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(term)
    return ordered
