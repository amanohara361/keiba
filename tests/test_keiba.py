"""ネットワークに繋がずに動くテスト。

netkeiba の HTML 構造が変わると予想が静かに壊れるため、
実際のページに近いフィクスチャでパーサを固定しておく。
"""

import os
import sys
from datetime import date, datetime

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer import RaceAnalyzer  # noqa: E402
import review  # noqa: E402
import scraper as scraper_module  # noqa: E402
from scraper import NetkeibaScraper, _parse_payouts  # noqa: E402
import store  # noqa: E402


RACE_LIST_HTML = """
<dl class="RaceList_DataList">
  <dd class="RaceList_Data">
    <li class="RaceList_DataItem">
      <a href="../race/shutuba.html?race_id=202405050811&amp;rf=race_list">
        <div class="Race_Num"><span class="Rnum">11</span>R</div>
        <div class="RaceList_ItemTitle">
          <span class="ItemTitle">天皇賞(秋)</span>
          <span class="Icon_GradeType Icon_GradeType1">G1</span>
        </div>
        <span class="RaceList_Itemtime">15:40</span>
      </a>
    </li>
    <li class="RaceList_DataItem">
      <a href="../race/shutuba.html?race_id=202405050810">
        <div class="RaceList_ItemTitle">
          <span class="ItemTitle">アルテミスステークス</span>
          <span class="Icon_GradeType Icon_GradeType3">GIII</span>
        </div>
        <span class="RaceList_Itemtime">14:25</span>
      </a>
    </li>
    <li class="RaceList_DataItem">
      <a href="../race/shutuba.html?race_id=202405050809">
        <div class="RaceList_ItemTitle">
          <span class="ItemTitle">3歳以上1勝クラス</span>
        </div>
        <span class="RaceList_Itemtime">13:50</span>
      </a>
    </li>
  </dd>
</dl>
"""

# 当日。馬体重も馬場状態も発表済み。
SHUTUBA_HTML = """
<div class="RaceData01">
  15:40発走 / 芝2000m (右 A) / 天候:雨 / 馬場:重
</div>
<table class="Shutuba_Table">
  <tr class="HorseList">
    <td class="Waku1">1</td>
    <td class="Umaban Umaban1">1</td>
    <td class="HorseInfo"><span><a href="https://db.netkeiba.com/horse/2019105219/">イクイノックス</a></span></td>
    <td class="Jockey"><a href="/jockey/result/recent/05339/">ルメール</a></td>
    <td class="Weight">484<small>(+2)</small></td>
  </tr>
  <tr class="HorseList">
    <td class="Waku2">2</td>
    <td class="Umaban Umaban2">2</td>
    <td class="HorseInfo"><span><a href="https://db.netkeiba.com/horse/2018104963/">パンサラッサ</a></span></td>
    <td class="Jockey"><a href="/jockey/result/recent/01014/">吉田豊</a></td>
    <td class="Weight">508<small>(-22)</small></td>
  </tr>
</table>
"""

# 前日。馬体重も馬場状態もまだ出ていない。
SHUTUBA_HTML_BEFORE_RACE_DAY = """
<div class="RaceData01">
  15:40発走 / 芝2000m (右 A)
</div>
<table class="Shutuba_Table">
  <tr class="HorseList">
    <td class="Waku1">1</td>
    <td class="Umaban Umaban1">1</td>
    <td class="HorseInfo"><span><a href="https://db.netkeiba.com/horse/2019105219/">イクイノックス</a></span></td>
    <td class="Jockey"><a href="/jockey/result/recent/05339/">ルメール</a></td>
    <td class="Weight">--</td>
  </tr>
</table>
"""

RESULT_HTML = """
<table class="race_table_01">
  <tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th></tr>
  <tr><td>1</td><td>1</td><td>2</td><td><a href="/horse/2018104963/">パンサラッサ</a></td></tr>
  <tr><td>2</td><td>1</td><td>1</td><td><a href="/horse/2019105219/">イクイノックス</a></td></tr>
  <tr><td>3</td><td>3</td><td>7</td><td><a href="/horse/2019102000/">ダノンベルーガ</a></td></tr>
</table>
<table class="pay_table_01">
  <tr><th class="tan">単勝</th><td class="txt_r">2</td><td class="txt_r">4,220円</td><td>12</td></tr>
  <tr><th class="fuku">複勝</th>
      <td class="txt_r">2<br>1<br>7</td>
      <td class="txt_r">980円<br>110円<br>150円</td>
      <td>11<br>1<br>3</td></tr>
</table>
"""


def soup_of(html):
    return BeautifulSoup(html, 'lxml')


# ----------------------------------------------------------------------
# 日付とグレード判定
# ----------------------------------------------------------------------

@pytest.mark.parametrize('today,expected_saturday', [
    ('2026-08-05', '2026-08-08'),  # 水曜 → 直後の土曜
    ('2026-08-07', '2026-08-08'),  # 金曜 → 翌日
    ('2026-08-08', '2026-08-08'),  # 土曜 → 当日
    ('2026-08-09', '2026-08-08'),  # 日曜 → 前日の土曜を含む今週末
])
def test_upcoming_weekend_dates(today, expected_saturday):
    saturday, sunday = NetkeibaScraper.upcoming_weekend_dates(date.fromisoformat(today))
    assert saturday.isoformat() == expected_saturday
    assert (sunday - saturday).days == 1


def test_grade_from_icon_class():
    """アイコンのクラスからグレードを読む。"""
    element = soup_of('<a><span class="Icon_GradeType Icon_GradeType2">G2</span></a>')
    assert NetkeibaScraper._extract_grade(element) == 'G2'


def test_grade_giii_is_not_misread_as_gi():
    """GIII を GI と誤判定しないこと（旧実装のバグ）。"""
    element = soup_of('<a>アルテミスステークス GIII</a>')
    assert NetkeibaScraper._extract_grade(element) == 'G3'


def test_non_graded_race_has_no_grade():
    element = soup_of('<a>3歳以上1勝クラス</a>')
    assert NetkeibaScraper._extract_grade(element) is None


# ----------------------------------------------------------------------
# パーサ
# ----------------------------------------------------------------------

def test_parse_race_list_extracts_grades_and_ids():
    races = NetkeibaScraper()._parse_race_list(soup_of(RACE_LIST_HTML), date(2026, 8, 8))
    by_id = {r['race_id']: r for r in races}

    assert set(by_id) == {'202405050811', '202405050810', '202405050809'}
    assert by_id['202405050811']['name'] == '天皇賞(秋)'
    assert by_id['202405050811']['grade'] == 'G1'
    assert by_id['202405050810']['grade'] == 'G3'
    assert by_id['202405050809']['grade'] is None
    assert by_id['202405050811']['date'] == '2026-08-08'
    # 相対パスではなく絶対 URL になっていること
    assert by_id['202405050811']['url'].startswith('https://race.netkeiba.com/')


def test_get_weekend_graded_races_filters_to_graded_only(monkeypatch):
    s = NetkeibaScraper()
    monkeypatch.setattr(s, 'fetch', lambda url, params=None: soup_of(RACE_LIST_HTML))

    graded, fetched = s.get_weekend_graded_races(date(2026, 8, 7))

    assert len(fetched) == 2  # 土日ぶん
    assert {r['grade'] for r in graded} == {'G1', 'G3'}
    assert all(r['grade'] in {'G1', 'G2', 'G3'} for r in graded)


def test_get_weekend_graded_races_raises_when_every_fetch_fails(monkeypatch):
    """取得に失敗したらダミーを返さず例外にすること（旧実装ではダミーを返していた）。"""
    def always_fail(url, params=None):
        raise scraper_module.ScrapeError('network down')

    s = NetkeibaScraper()
    monkeypatch.setattr(s, 'fetch', always_fail)

    with pytest.raises(scraper_module.ScrapeError):
        s.get_weekend_graded_races(date(2026, 8, 7))


def test_get_horses_for_race(monkeypatch):
    s = NetkeibaScraper()
    monkeypatch.setattr(s, 'fetch', lambda url, params=None: soup_of(SHUTUBA_HTML))
    monkeypatch.setattr(s, 'fetch_json', lambda url, params=None: {
        'data': {'odds': {'1': {
            '1': ['1.3', '1.4', '1'],
            '2': ['42.2', '45.0', '12'],
        }}}
    })

    horses = s.get_horses_for_race({
        'race_id': '202405050811', 'name': '天皇賞(秋)',
        'url': 'https://race.netkeiba.com/race/shutuba.html?race_id=202405050811',
    })

    assert [h['name'] for h in horses] == ['イクイノックス', 'パンサラッサ']
    assert horses[0]['horse_id'] == '2019105219'
    assert horses[0]['jockey'] == 'ルメール'
    assert horses[0]['odds'] == 1.3
    assert horses[1]['ninki'] == 12


def test_odds_failure_does_not_break_horses(monkeypatch):
    """オッズ API が死んでも出馬表は返ること。"""
    s = NetkeibaScraper()
    monkeypatch.setattr(s, 'fetch', lambda url, params=None: soup_of(SHUTUBA_HTML))
    monkeypatch.setattr(s, 'fetch_json', lambda url, params=None: None)

    horses = s.get_horses_for_race({
        'race_id': 'x', 'name': 'テスト',
        'url': 'https://race.netkeiba.com/race/shutuba.html?race_id=x',
    })

    assert len(horses) == 2
    assert horses[0]['odds'] is None


def test_parse_payouts():
    payouts = _parse_payouts(soup_of(RESULT_HTML))
    assert payouts['単勝'] == [{'combination': '2', 'yen': 4220}]
    assert payouts['複勝'] == [
        {'combination': '2', 'yen': 980},
        {'combination': '1', 'yen': 110},
        {'combination': '7', 'yen': 150},
    ]


def test_get_race_result(monkeypatch):
    s = NetkeibaScraper()
    monkeypatch.setattr(s, 'fetch', lambda url, params=None: soup_of(RESULT_HTML))

    result = s.get_race_result('202405050811')

    assert result['finishing_order'][0] == {'rank': 1, 'umaban': '2', 'name': 'パンサラッサ'}
    assert result['payouts']['単勝'][0]['yen'] == 4220


def test_get_race_result_returns_none_before_the_race(monkeypatch):
    s = NetkeibaScraper()
    monkeypatch.setattr(s, 'fetch', lambda url, params=None: soup_of('<html><body></body></html>'))
    assert s.get_race_result('202405050811') is None


def test_resolve_encoding_prefers_meta_over_host_default():
    class Response:
        headers = {'Content-Type': 'text/html'}
        content = b'<html><head><meta charset="EUC-JP">'
        url = 'https://race.netkeiba.com/x'
        apparent_encoding = 'utf-8'

    assert NetkeibaScraper()._resolve_encoding(Response()).lower() == 'euc-jp'


def test_resolve_encoding_falls_back_to_host_hint():
    class Response:
        headers = {'Content-Type': 'text/html'}
        content = b'<html><head><title>x</title>'
        url = 'https://db.netkeiba.com/horse/123/'
        apparent_encoding = 'windows-1252'

    assert NetkeibaScraper()._resolve_encoding(Response()) == 'euc-jp'


# ----------------------------------------------------------------------
# 評価ロジック
# ----------------------------------------------------------------------

def make_entry(analyzer, name, jockey, sire, runs, wins, top3, odds=None, ninki=None):
    horse = {
        'name': name, 'jockey': jockey, 'umaban': '1',
        'odds': odds, 'ninki': ninki, 'horse_id': '1',
    }
    details = {
        'sire': sire, 'broodmare_sire': '', 'past_runs': runs,
        'wins': wins, 'top3': top3, 'recent_ranks': [1, 2, 3][:min(3, runs)],
    }
    return analyzer.evaluate_horse(horse, details)


def test_strong_horse_scores_above_weak_horse():
    a = RaceAnalyzer()
    strong = make_entry(a, '強い馬', 'ルメール', 'ディープインパクト', 10, 5, 9)
    weak = make_entry(a, '弱い馬', '無名騎手', '無名種牡馬', 10, 0, 1)
    assert strong['total_score'] > weak['total_score']


def test_missing_data_does_not_crash():
    a = RaceAnalyzer()
    entry = a.evaluate_horse({'name': '不明馬'}, {})
    assert entry['total_score'] > 0
    assert '戦績データなし (+0)' in entry['reasons']


def test_value_pick_is_flagged_when_unpopular_but_highly_rated():
    a = RaceAnalyzer()
    best = make_entry(a, '本命馬', 'ルメール', 'キズナ', 12, 8, 11, odds=2.0, ninki=1)
    # 評価2位なのに9番人気、オッズも高い → 妙味あり かつ 穴候補
    underrated = make_entry(a, '過小評価馬', '無名騎手', 'キズナ', 12, 6, 10,
                            odds=25.0, ninki=9)
    filler = make_entry(a, '人気馬', '無名騎手', '無名種牡馬', 3, 0, 0,
                        odds=1.8, ninki=2)

    analysis = a.analyze_race([filler, underrated, best])

    assert analysis['top_picks'][0]['horse']['name'] == '本命馬'
    assert analysis['top_picks'][1]['is_value'] is True
    assert analysis['longshot']['horse']['name'] == '過小評価馬'


def test_longshot_excludes_the_honmei_to_avoid_double_betting():
    """◎ には単勝を買うので、同じ馬を穴候補にはしない。"""
    a = RaceAnalyzer()
    # スコア1位なのに10番人気で高オッズ＝穴条件を満たすが、◎なので除外される
    top = make_entry(a, '本命かつ人気薄', 'ルメール', 'キズナ', 12, 8, 11,
                     odds=30.0, ninki=10)
    other = make_entry(a, '平凡馬', '無名騎手', '無名種牡馬', 3, 0, 0,
                       odds=2.0, ninki=1)

    analysis = a.analyze_race([top, other])

    assert analysis['top_picks'][0]['horse']['name'] == '本命かつ人気薄'
    assert analysis['top_picks'][0]['is_value'] is True
    assert analysis['longshot'] is None


def test_no_longshot_when_odds_are_unavailable():
    """人気・オッズが未発表なら妙味判定はしない。"""
    a = RaceAnalyzer()
    entries = [make_entry(a, f'馬{i}', '騎手', '種牡馬', 5, 1, 2) for i in range(5)]
    analysis = a.analyze_race(entries)

    assert analysis['longshot'] is None
    assert all(e['is_value'] is False for e in analysis['top_picks'])


def test_analyze_race_ranks_and_limits_picks():
    a = RaceAnalyzer()
    entries = [make_entry(a, f'馬{i}', '騎手', '種牡馬', 10, i, i) for i in range(6)]
    analysis = a.analyze_race(entries, top_n=4)

    assert len(analysis['top_picks']) == 4
    assert analysis['field_size'] == 6
    assert [e['score_rank'] for e in analysis['top_picks']] == [1, 2, 3, 4]


# ----------------------------------------------------------------------
# 答え合わせと集計
# ----------------------------------------------------------------------

def sample_prediction_race():
    return {
        'race_id': '202405050811',
        'name': '天皇賞(秋)',
        'picks': [
            {'mark': '◎', 'umaban': '1', 'name': 'イクイノックス'},
            {'mark': '○', 'umaban': '2', 'name': 'パンサラッサ'},
            {'mark': '▲', 'umaban': '7', 'name': 'ダノンベルーガ'},
            {'mark': '△', 'umaban': '9', 'name': '着外馬'},
        ],
        'longshot': {'mark': '穴', 'umaban': '2', 'name': 'パンサラッサ'},
    }


def test_review_race_computes_stake_and_return():
    race = review.review_race(sample_prediction_race(), {
        'race_id': '202405050811',
        'finishing_order': [
            {'rank': 1, 'umaban': '2', 'name': 'パンサラッサ'},
            {'rank': 2, 'umaban': '1', 'name': 'イクイノックス'},
            {'rank': 3, 'umaban': '7', 'name': 'ダノンベルーガ'},
        ],
        'payouts': _parse_payouts(soup_of(RESULT_HTML)),
    })

    assert race['settled'] is True
    # ◎に単勝100 + 4頭に複勝100ずつ + 穴に単勝100 = 600円
    assert race['staked'] == 600
    # 複勝: 1番=110, 2番=980, 7番=150, 9番=0 / 穴(2番)の単勝=4220
    assert race['returned'] == 110 + 980 + 150 + 4220
    assert race['honmei_won'] is False      # ◎は2着
    assert race['honmei_placed'] is True
    assert race['longshot_hit'] is True


def test_review_race_marks_unsettled_when_result_missing():
    race = review.review_race(sample_prediction_race(), None)
    assert race['settled'] is False


def test_review_race_records_a_total_miss():
    race = review.review_race({
        'race_id': 'x',
        'name': 'テスト',
        'picks': [{'mark': '◎', 'umaban': '11', 'name': '外れ馬'}],
        'longshot': None,
    }, {
        'race_id': 'x',
        'finishing_order': [{'rank': 1, 'umaban': '2', 'name': '勝ち馬'}],
        'payouts': _parse_payouts(soup_of(RESULT_HTML)),
    })

    assert race['staked'] == 200
    assert race['returned'] == 0
    assert race['honmei_won'] is False
    assert race['honmei_placed'] is False


def test_accuracy_report_summarises_by_method_version(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(store, 'RESULTS_DIR', str(tmp_path / 'results'))
    monkeypatch.setattr(store, 'ACCURACY_PATH', str(tmp_path / 'accuracy.md'))

    store._write_json(str(tmp_path / 'results' / '2026-08-08.json'), {
        'method_version': 'v2',
        'races': [
            {'settled': True, 'honmei_won': True, 'honmei_placed': True,
             'staked': 600, 'returned': 900, 'longshot_bet': True, 'longshot_hit': False},
            {'settled': True, 'honmei_won': False, 'honmei_placed': True,
             'staked': 500, 'returned': 300, 'longshot_bet': False, 'longshot_hit': False},
            {'settled': False},
        ],
    })

    path, by_version = store.write_accuracy_report()

    assert by_version['v2']['races'] == 2      # 未確定ぶんは数えない
    assert by_version['v2']['staked'] == 1100
    assert by_version['v2']['returned'] == 1200
    assert by_version['v2']['win_hits'] == 1
    assert by_version['v2']['place_hits'] == 2

    report = open(path, encoding='utf-8').read()
    assert 'v2' in report
    assert '109.1%' in report  # 1200/1100


def test_save_and_load_predictions_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'PREDICTIONS_DIR', str(tmp_path / 'predictions'))

    races = [sample_prediction_race()]
    path = store.save_predictions(races, 'v3', '2026-08-08')
    loaded = store.load_predictions(path)

    assert loaded['method_version'] == 'v3'
    assert loaded['races'][0]['name'] == '天皇賞(秋)'
    # 日本語がエスケープされずに保存されていること
    assert '天皇賞' in open(path, encoding='utf-8').read()


def test_save_predictions_merges_into_one_file_per_weekend(tmp_path, monkeypatch):
    """土曜の朝に出した日曜ぶんの予想を、直前予想の保存で消さないこと。"""
    monkeypatch.setattr(store, 'PREDICTIONS_DIR', str(tmp_path / 'predictions'))

    saturday = {'race_id': '111', 'name': '土曜の重賞', 'date': '2026-08-08',
                'picks': [{'mark': '◎', 'umaban': '1', 'name': '朝の本命'}]}
    sunday = {'race_id': '222', 'name': '日曜の重賞', 'date': '2026-08-09',
              'picks': [{'mark': '◎', 'umaban': '3', 'name': '日曜の本命'}]}

    store.save_predictions([saturday, sunday], 'v3', '2026-08-08')

    # 直前予想で土曜ぶんだけ差し替える
    updated_saturday = dict(saturday, picks=[{'mark': '◎', 'umaban': '5', 'name': '直前の本命'}])
    path = store.save_predictions([updated_saturday], 'v3', '2026-08-08')

    races = {r['race_id']: r for r in store.load_predictions(path)['races']}
    assert len(races) == 2                                    # 日曜ぶんが残っている
    assert races['111']['picks'][0]['name'] == '直前の本命'    # 土曜ぶんは更新された
    assert races['222']['picks'][0]['name'] == '日曜の本命'


# ----------------------------------------------------------------------
# 通し実行
# ----------------------------------------------------------------------

class FakeScraper:
    """ネットワークの代わりにフィクスチャを返すスクレイパー。"""

    def __init__(self, *args, **kwargs):
        self.calls = []

    def get_weekend_graded_races(self, today=None):
        races = NetkeibaScraper()._parse_race_list(soup_of(RACE_LIST_HTML), date(2026, 8, 8))
        graded = [r for r in races if r['grade'] in {'G1', 'G2', 'G3'}]
        return graded, [date(2026, 8, 8), date(2026, 8, 9)]

    def get_race_card(self, race):
        self.calls.append(race['race_id'])
        s = NetkeibaScraper()
        s.fetch = lambda url, params=None: soup_of(SHUTUBA_HTML)
        s.fetch_json = lambda url, params=None: {
            'data': {'odds': {'1': {'1': ['1.3', '1.4', '1'], '2': ['42.2', '45.0', '12']}}}
        }
        return s.get_race_card(race)

    def get_horse_details(self, horse_id):
        return {'sire': 'キタサンブラック', 'broodmare_sire': '', 'past_runs': 8,
                'wins': 4, 'top3': 7, 'recent_ranks': [1, 1, 2, 3, 1]}


def test_main_end_to_end_writes_predictions(tmp_path, monkeypatch, capsys):
    import main

    monkeypatch.setattr(main, 'NetkeibaScraper', FakeScraper)
    monkeypatch.setattr(store, 'PREDICTIONS_DIR', str(tmp_path / 'predictions'))

    exit_code = main.main(['--today', '2026-08-07T08:00', '--no-email'])
    assert exit_code == 0

    saved = sorted((tmp_path / 'predictions').iterdir())
    assert len(saved) == 1

    payload = store.load_predictions(str(saved[0]))
    assert payload['method_version'] == RaceAnalyzer().method_version
    # 重賞2件（G1 と G3）ぶんの予想が入っていること
    assert {r['name'] for r in payload['races']} == {'天皇賞(秋)', 'アルテミスステークス'}

    race = payload['races'][0]
    assert [p['mark'] for p in race['picks']] == ['◎', '○']  # 出走2頭ぶん
    assert race['picks'][0]['score'] > 0

    output = capsys.readouterr().out
    assert '中央競馬 重賞予想 — 朝の予想' in output
    assert '◎' in output


def test_main_returns_error_when_race_list_cannot_be_fetched(monkeypatch):
    import main

    class BrokenScraper(FakeScraper):
        def get_weekend_graded_races(self, today=None):
            raise scraper_module.ScrapeError('netkeiba unreachable')

    monkeypatch.setattr(main, 'NetkeibaScraper', BrokenScraper)
    assert main.main(['--no-email', '--no-save']) == 1


def test_main_succeeds_quietly_when_no_graded_races(monkeypatch):
    """重賞が無い週末は異常ではないので 0 で終わること。"""
    import main

    class QuietScraper(FakeScraper):
        def get_weekend_graded_races(self, today=None):
            return [], [date(2026, 8, 8), date(2026, 8, 9)]

    monkeypatch.setattr(main, 'NetkeibaScraper', QuietScraper)
    assert main.main(['--no-email', '--no-save']) == 0


def test_main_reports_failure_when_no_shutuba_published(monkeypatch):
    """重賞はあるのに1件も予想が作れなければ失敗扱いにすること。"""
    import main

    class NoEntriesScraper(FakeScraper):
        def get_race_card(self, race):
            return {'horses': [], 'conditions': {}}

    monkeypatch.setattr(main, 'NetkeibaScraper', NoEntriesScraper)
    assert main.main(['--today', '2026-08-07T08:00', '--no-email', '--no-save']) == 1


# ----------------------------------------------------------------------
# 当日情報（馬場状態・馬体重・発走時刻）
# ----------------------------------------------------------------------

def test_start_time_is_parsed_from_race_list():
    races = NetkeibaScraper()._parse_race_list(soup_of(RACE_LIST_HTML), date(2026, 8, 8))
    by_id = {r['race_id']: r for r in races}
    assert by_id['202405050811']['start_time'] == '15:40'
    assert by_id['202405050810']['start_time'] == '14:25'


def test_conditions_are_parsed_on_race_day():
    conditions = scraper_module._parse_conditions(soup_of(SHUTUBA_HTML))
    assert conditions['going'] == '重'
    assert conditions['weather'] == '雨'
    assert conditions['surface'] == '芝'
    assert conditions['distance'] == 2000


def test_conditions_are_empty_before_race_day():
    """前日は馬場状態が出ていないので None のままになること。"""
    conditions = scraper_module._parse_conditions(soup_of(SHUTUBA_HTML_BEFORE_RACE_DAY))
    assert conditions['going'] is None
    assert conditions['weather'] is None
    assert conditions['distance'] == 2000  # コースは前日でも分かる


@pytest.mark.parametrize('cell_html,expected', [
    ('<td class="Weight">484<small>(+2)</small></td>', (484, 2)),
    ('<td class="Weight">508<small>(-22)</small></td>', (508, -22)),
    ('<td class="Weight">460<small>(0)</small></td>', (460, 0)),
    ('<td class="Weight">--</td>', (None, None)),          # 当日朝まで未発表
    ('<td class="Weight">計不</td>', (None, None)),         # 計量不能
])
def test_parse_weight(cell_html, expected):
    cell = soup_of(cell_html).select_one('td.Weight')
    assert scraper_module._parse_weight(cell) == expected


def test_race_card_returns_horses_and_conditions(monkeypatch):
    s = NetkeibaScraper()
    monkeypatch.setattr(s, 'fetch', lambda url, params=None: soup_of(SHUTUBA_HTML))
    monkeypatch.setattr(s, 'fetch_json', lambda url, params=None: None)

    card = s.get_race_card({'race_id': 'x', 'name': 'テスト',
                            'url': 'https://race.netkeiba.com/race/shutuba.html?race_id=x'})

    assert card['conditions']['going'] == '重'
    assert card['horses'][0]['weight'] == 484
    assert card['horses'][0]['weight_diff'] == 2
    assert card['horses'][1]['weight_diff'] == -22


# ----------------------------------------------------------------------
# 当日情報をスコアに反映する
# ----------------------------------------------------------------------

def test_heavy_going_boosts_mud_suited_sire():
    """雨で馬場が渋ったら、道悪向きの血統に加点されること。"""
    a = RaceAnalyzer()
    horse = {'name': 'パワー型', 'jockey': '騎手', 'umaban': '1'}
    details = {'sire': 'ゴールドシップ', 'past_runs': 10, 'wins': 2, 'top3': 5,
               'recent_ranks': [2, 3, 1]}

    good = a.evaluate_horse(horse, details, {'going': '良'})
    heavy = a.evaluate_horse(horse, details, {'going': '重'})

    assert heavy['total_score'] > good['total_score']
    assert any('道悪(重)向きの血統' in r for r in heavy['reasons'])
    assert not any('道悪' in r for r in good['reasons'])


def test_non_mud_sire_gets_no_boost_on_heavy_going():
    a = RaceAnalyzer()
    horse = {'name': 'スピード型', 'jockey': '騎手', 'umaban': '1'}
    details = {'sire': 'ロードカナロア', 'past_runs': 10, 'wins': 2, 'top3': 5,
               'recent_ranks': [2, 3, 1]}

    good = a.evaluate_horse(horse, details, {'going': '良'})
    heavy = a.evaluate_horse(horse, details, {'going': '重'})

    assert heavy['total_score'] == good['total_score']


def test_large_weight_swing_is_penalised():
    """馬体重が大きく増減した馬は減点されること。"""
    a = RaceAnalyzer()
    details = {'sire': 'キズナ', 'past_runs': 10, 'wins': 3, 'top3': 6, 'recent_ranks': [1, 2]}

    steady = a.evaluate_horse({'name': '順調', 'jockey': '騎手', 'weight_diff': 2}, details, {})
    mild = a.evaluate_horse({'name': 'やや増', 'jockey': '騎手', 'weight_diff': 16}, details, {})
    big = a.evaluate_horse({'name': '大幅減', 'jockey': '騎手', 'weight_diff': -24}, details, {})

    assert steady['total_score'] > mild['total_score'] > big['total_score']
    assert any('馬体重-24kg' in r for r in big['reasons'])


def test_weight_is_ignored_before_it_is_published():
    """前日の時点（馬体重 None）では減点も加点もしないこと。"""
    a = RaceAnalyzer()
    details = {'sire': 'キズナ', 'past_runs': 10, 'wins': 3, 'top3': 6, 'recent_ranks': [1, 2]}

    unpublished = a.evaluate_horse({'name': '前日', 'jockey': '騎手', 'weight_diff': None},
                                   details, {})
    steady = a.evaluate_horse({'name': '当日', 'jockey': '騎手', 'weight_diff': 2}, details, {})

    assert unpublished['total_score'] == steady['total_score']
    assert not any('馬体重' in r for r in unpublished['reasons'])


# ----------------------------------------------------------------------
# 配信対象の絞り込みと変化の検知
# ----------------------------------------------------------------------

def jst(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=store.JST)


@pytest.mark.parametrize('now,expected', [
    (jst(2026, 8, 8, 8, 0), True),    # 朝：まだ先
    (jst(2026, 8, 8, 15, 0), True),   # 40分前：間に合う
    (jst(2026, 8, 8, 15, 35), False),  # 5分前：届いても遅い
    (jst(2026, 8, 8, 16, 0), False),  # 発走済み
])
def test_is_upcoming(now, expected):
    import main
    race = {'date': '2026-08-08', 'start_time': '15:40'}
    assert main.is_upcoming(race, now) is expected


def test_is_upcoming_includes_race_with_unknown_start_time():
    """発走時刻が取れなかったら、落とさず配信する側に倒す。"""
    import main
    assert main.is_upcoming({'date': '2026-08-08', 'start_time': None},
                            jst(2026, 8, 8, 15, 0)) is True


def test_update_mode_only_covers_todays_remaining_races():
    import main
    races = [
        {'race_id': '1', 'date': '2026-08-08', 'start_time': '15:40'},  # 今日これから
        {'race_id': '2', 'date': '2026-08-08', 'start_time': '10:00'},  # 今日もう終わった
        {'race_id': '3', 'date': '2026-08-09', 'start_time': '15:40'},  # 明日
    ]
    now = jst(2026, 8, 8, 11, 0)

    morning = [r['race_id'] for r in main.select_races(races, 'morning', now)]
    update = [r['race_id'] for r in main.select_races(races, 'update', now)]

    assert morning == ['1', '3']  # 朝は週末ぶん全部（発走済みは除く）
    assert update == ['1']        # 追加配信は当日のこれからのぶんだけ


def test_detect_changes_reports_going_odds_and_honmei():
    import main
    previous = {
        'conditions': {'going': '良'},
        'picks': [
            {'mark': '◎', 'name': '馬A', 'odds': 2.0, 'is_value': False},
            {'mark': '○', 'name': '馬B', 'odds': 20.0, 'is_value': False},
        ],
    }
    current = {
        'conditions': {'going': '重'},
        'picks': [
            {'mark': '◎', 'name': '馬B', 'odds': 8.0, 'is_value': True,
             'score_rank': 1, 'ninki': 5},
            {'mark': '○', 'name': '馬A', 'odds': 2.1, 'is_value': False},
        ],
    }

    changes = main.detect_changes(previous, current)
    joined = ' / '.join(changes)

    assert '馬場が 良 → 重 に変わりました' in changes
    assert '◎が 馬A → 馬B に替わりました' in changes
    assert '馬B のオッズが 20.0 → 8.0 倍に下落' in joined
    assert '馬B が新たに妙味ありになりました' in changes
    # 2.0→2.1 はわずかな動きなので報告しない
    assert '馬A のオッズ' not in joined


def test_detect_changes_is_empty_without_a_previous_send():
    import main
    assert main.detect_changes(None, {'picks': [], 'conditions': {}}) == []


def test_final_mode_email_shows_changes(tmp_path, monkeypatch, capsys):
    """直前配信で、前回からの変化が本文に出ること。"""
    import main

    monkeypatch.setattr(main, 'NetkeibaScraper', FakeScraper)
    monkeypatch.setattr(store, 'PREDICTIONS_DIR', str(tmp_path / 'predictions'))

    # 朝の予想を作っておく（馬場・オッズがまだ違う状態を模す）
    store.save_predictions([{
        'race_id': '202405050811', 'name': '天皇賞(秋)', 'date': '2026-08-08',
        'conditions': {'going': '良'},
        'picks': [{'mark': '◎', 'name': 'イクイノックス', 'odds': 1.3, 'is_value': False}],
    }], 'v3', '2026-08-08')

    assert main.main(['--mode', 'final', '--today', '2026-08-08T14:30', '--no-email']) == 0

    output = capsys.readouterr().out
    assert '直前の最終予想' in output
    assert '◆ 前回からの変化' in output
    assert '馬場が 良 → 重 に変わりました' in output
    assert '馬場:重' in output


def test_odds_request_uses_action_update(monkeypatch):
    """action=init はキャッシュされた古い値を返すため update を使うこと。

    直前配信で朝のオッズを掴んだままになる不具合を防ぐ。
    """
    captured = {}

    s = NetkeibaScraper()
    monkeypatch.setattr(s, 'fetch', lambda url, params=None: soup_of(SHUTUBA_HTML))

    def fake_json(url, params=None):
        captured.update(params or {})
        return {'status': 'result', 'data': {'official_datetime': '15:38',
                'odds': {'1': {'01': ['2.9', '3.1', '1'], '02': ['-3.0', '0', '0']}}}}

    monkeypatch.setattr(s, 'fetch_json', fake_json)
    card = s.get_race_card({'race_id': 'x', 'name': 'テスト',
                            'url': 'https://race.netkeiba.com/race/shutuba.html?race_id=x'})

    assert captured['action'] == 'update'
    assert card['horses'][0]['odds'] == 2.9
    # -3.0 は出走取消。オッズとして扱わないこと。
    assert card['horses'][1]['scratched'] is True
    assert card['horses'][1]['odds'] is None


def test_scratched_horse_is_excluded_from_picks(monkeypatch, tmp_path, capsys):
    """取消馬を予想に載せないこと。"""
    import main

    class ScratchScraper(FakeScraper):
        def get_race_card(self, race):
            card = FakeScraper.get_race_card(self, race)
            card['horses'][1]['scratched'] = True
            return card

    monkeypatch.setattr(main, 'NetkeibaScraper', ScratchScraper)
    monkeypatch.setattr(store, 'PREDICTIONS_DIR', str(tmp_path / 'predictions'))

    assert main.main(['--today', '2026-08-07T08:00', '--no-email']) == 0
    assert 'パンサラッサ' not in capsys.readouterr().out
