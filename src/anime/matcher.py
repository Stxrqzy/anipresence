"""Fuzzy matching between a website-extracted title and AniList candidates.

AniList's own SEARCH_MATCH ranking is usually good enough on its own; this
layer adds a sanity check so a garbage extraction can't produce a wrong RPC.
"""
from rapidfuzz import fuzz

from .titles import normalize_search


def _candidate_strings(media):
    """All strings we can score a search term against."""
    out = []
    for attr in ("title_romaji", "title_english", "title_native"):
        value = getattr(media, attr) or ""
        if value:
            out.append(value)
    out.extend(media.synonyms or [])
    return out


def best_match(media_list, search_title, min_score=60.0):
    """Pick the best AniList entry for a title.

    Returns ``(media, confidence)`` where confidence is 0..100. If nothing
    scores above a floor we still return the top AniList result (with a low
    score) so the RPC degrades gracefully instead of disappearing.
    """
    if not media_list:
        return None, 0.0

    target = normalize_search(search_title)
    if not target:
        return media_list[0], 0.0

    best, best_score = None, 0.0
    for media in media_list:
        score = 0.0
        for cand in _candidate_strings(media):
            c = normalize_search(cand)
            if not c:
                continue
            s = max(
                fuzz.ratio(target, c),
                fuzz.token_set_ratio(target, c),
                fuzz.partial_ratio(target, c),
            )
            # Exact substring containment is a very strong signal.
            if target in c or c in target:
                s = max(s, 95.0)
            score = max(score, s)
        if score > best_score:
            best, best_score = media, score

    if best is None:
        best, best_score = media_list[0], 0.0
    return best, best_score
