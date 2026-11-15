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
