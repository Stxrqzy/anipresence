from src.anime import browse
from src.ui.quick_search import search_index, local_matches


def test_recommendations_deduplicate_exclude_library_and_filter_adult(monkeypatch):
    browse._query_cache.clear()
    calls=[]
    class Client:
        def __init__(self):
            self._session=type("Session",(),{"close":lambda _:None})()
        def _query(self,query,variables):
            calls.append(variables)
            def node(identity,adult=False):
                return {"rating":10,"mediaRecommendation":{"id":identity,"type":"ANIME","isAdult":adult,
                        "title":{"english":f"Anime {identity}"}}}
            return {"seed0":{"title":{"english":"Seed"},"recommendations":{"nodes":[node(1),node(8),node(9,True)]}},
                    "seed1":{"title":{"english":"Other seed"},"recommendations":{"nodes":[node(8)]}}}
    monkeypatch.setattr(browse,"AniListClient",Client)
    library=[{"anilist_id":i,"title":f"Seed {i}","status":"COMPLETED","progress":12} for i in (1,2)]
    result=browse.recommendations(library)
    assert [e["anilist_id"] for e in result["entries"]]==[8]
    assert "Seed, Other seed" in result["entries"][0]["suggestion_reason"]
    result["entries"].clear()
    assert len(browse.recommendations(library)["entries"])==1
    assert len(calls)==1
    assert [e["anilist_id"] for e in browse.recommendations(library,False)["entries"]]==[8,9]
    assert len(calls)==2
    assert not browse.recommendations([])["entries"]
    browse._query_cache.clear()


def test_catalogue_cache_is_bounded_and_returns_independent_results(monkeypatch):
    browse._query_cache.clear()
    calls=[]
    monkeypatch.setattr(browse,"_catalogue",lambda filters,page:(calls.append((filters,page)) or {"entries":[{"title":"Example"}]}))
    first=browse.catalogue({"search":"Example","adult":False})
    first["entries"].clear()
    assert browse.catalogue({"search":"Example","adult":False})["entries"]
    assert len(calls)==1
    browse.catalogue({"search":"Example","adult":None})
    assert len(calls)==2
    for i in range(60):browse.catalogue({"search":str(i)})
    assert len(browse._query_cache)<=48
    browse._query_cache.clear()


def test_quick_search_matches_aliases_and_merges_library_history():
    library=[{"anilist_id":1,"title":"English title","aliases":["日本語","Romaji name"],"progress":4}]
    history=[{"anilist_id":1,"title":"English title","episode":5,"url":"https://ani.pm/watch/5"},
             {"anilist_id":1,"title":"English title","episode":3}]
    index=search_index(library,history)
    assert len(index)==1
    assert local_matches(index,"日本語")[0]["episode"]==5
    assert local_matches(index,"romaji NAME")[0]["search_source"]=="Library · History"
    assert not local_matches(index,"unrelated")
