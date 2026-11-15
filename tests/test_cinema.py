from datetime import date, timedelta
from src.ui.cinema_stats import period_data
from src.config import ConfigManager


def test_v3_periods_use_dated_records_and_keep_zero_days():
    today=date.today().isoformat()
    old=(date.today()-timedelta(days=70)).isoformat()
    snapshot={"days":{today:3600,old:7200},
              "episodes":{(1,1):today,(1,2):old},
              "titles":[{"title":"Example","seconds":999999,"days":{today:3600,old:7200}}],
              "unique_episode_total":500}
    month=period_data(snapshot,30)
    assert len(month["series"])==30
    assert month["total"]==3600 and month["episodes"]==1
    assert month["titles"][0]["seconds"]==3600
    assert sum(v[1] for v in month["series"])==1
    quarter=period_data(snapshot,90)
    assert quarter["total"]==10800 and quarter["episodes"]==2
    year=period_data(snapshot,365)
    assert len(year["series"])==53
    assert sum(v[1] for v in year["series"])==3
    assert sum(v[2] for v in year["series"])==2


def test_layout_preferences_migrate_and_persist(tmp_path):
    path=tmp_path/"config.json"
    assert ConfigManager(path).data.ui_layout=="cinema"
    path.write_text('{"ui_layout":"classic"}')
    assert ConfigManager(path).data.ui_layout=="classic"
    path.write_text('{"ui_layout":"modern"}')
    cfg=ConfigManager(path)
    assert cfg.data.ui_layout=="modern"
    cfg.set(ui_layout="cinema")
    assert ConfigManager(path).data.ui_layout=="cinema"
    path.write_text('{}')
    assert ConfigManager(path).data.ui_layout=="modern"


def test_all_time_retains_undated_progress_without_inventing_chart_dates():
    from src.ui.cinema_stats import comparison_text
    today=date.today().isoformat()
    old=(date.today()-timedelta(days=800)).isoformat()
    snapshot={"days":{today:600,old:1200},"episodes":{(1,1):old,(1,2):today},
              "titles":[{"title":"Example","seconds":9000,"local_seconds":600,
                         "estimated_seconds":8400,"days":{today:600,old:1200},"estimated_days":{old:1200}}],
              "unique_episode_total":12}
    total=period_data(snapshot,None)
    assert total["total"]==9000 and total["episodes"]==12
    assert total["measured"]==600 and total["estimated"]==8400
    assert total["undated"]==7200
    assert total["bucket"]=="month" and len(total["series"])<30
    assert sum(row[1] for row in total["series"])==.5
    month=period_data(snapshot,30)
    assert month["total"]==600 and month["estimated"]==0
    assert "No activity" in comparison_text(0,0)
    assert "+50%" in comparison_text(90,60)
    empty=period_data({"days":{},"episodes":{},"titles":[],"unique_episode_total":0},None)
    assert empty["total"]==0 and len(empty["series"])==1


def test_resume_uses_measured_completion_and_does_not_reuse_previous_episode_url():
    from src.ui.playback import playback_state, resume_entry
    entry={"anilist_id":42,"title":"Example","episode":3,"url":"https://ani.pm/watch/3","episodes":12}
    history=[dict(entry,current_time=1350,duration=1400,last_watched=1)]
    state=playback_state(entry,history,[])
    result=resume_entry(entry,state)
    assert result["episode"]==4 and result["resume_kind"]=="next"
    assert result["url"]=="" and result["current_time"] is None
    assert entry["episode"]==3  # input remains immutable
    history[0]["current_time"]=300
    state=playback_state(entry,history,[dict(entry,progress=12)])
    assert resume_entry(entry,state)["episode"]==3  # partial replay wins
    history[0]["current_time"]=1400
    result=resume_entry(entry,playback_state(entry,history,[dict(entry,progress=12)]))
    assert result["resume_kind"]=="caught_up"
