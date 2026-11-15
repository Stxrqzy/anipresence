"""V3 presentation of recorded progress without guessing episode URLs."""
from ..core.media_identity import MediaIdentity


def episode_number(value):
    try:
        number=float(value or 0)
        return int(number) if number.is_integer() and number>0 else 0
    except (ValueError,TypeError):
        return 0


def playback_state(entry, history, library):
    resolver=MediaIdentity([entry,*history,*library])
    identity=resolver.key(entry)
    rows=[e for e in history if resolver.key(e)==identity]
    member=next((e for e in library if resolver.key(e)==identity),{})
    progress=episode_number(member.get("progress"))
    total=episode_number(entry.get("episodes") or entry.get("episodes_total") or member.get("episodes"))
    states={}
    for e in sorted(rows,key=lambda e:e.get("last_watched") or 0):
        number=episode_number(e.get("episode"))
        if not number:continue
        duration=e.get("duration") or 0
        current=e.get("current_time")
        fraction=min(1,max(0,current/duration)) if duration>0 and current is not None else None
        states[number]={"fraction":fraction,"watched":bool(fraction is not None and fraction>=.9),
                        "entry":e}
    return {"episodes":states,"progress":progress,"total":total}


def resume_entry(entry, state):
    result=dict(entry)
    number=episode_number(entry.get("episode"))
    if not number:
        local=[(n,row) for n,row in state["episodes"].items() if row.get("fraction") is not None and row["fraction"]>0]
        if local:
            number,latest=max(local,key=lambda item:item[1]["entry"].get("last_watched") or 0)
            result.update({key:latest["entry"].get(key) for key in ("url","current_time","duration","episode_title")})
    number=number or max(1,state["progress"]+1)
    watched=state["episodes"].get(number,{})
    # An explicit partial local replay takes precedence over the list baseline.
    fraction=watched.get("fraction")
    done=watched.get("watched") or (number<=state["progress"] and fraction is None)
    result["resume_kind"]=entry.get("resume_kind") or ("next" if not entry.get("episode") and state["progress"] and fraction is None else "resume")
    if done:
        number=max(number,state["progress"])+1
        while state["episodes"].get(number,{}).get("watched"):number+=1
        if state["total"] and number>state["total"]:
            result["resume_kind"]="caught_up"
            return result
        result.update(episode=number,url="",current_time=None,duration=None,episode_title="",
                      episode_art="",episode_description="",resume_kind="next")
    else:
        result["episode"]=number
        if result["resume_kind"]=="next":
            result.update(url="",current_time=None,duration=None,episode_title="",episode_art="",episode_description="")
    return result
