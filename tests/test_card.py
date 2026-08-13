#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""カード作成（方針B の取得側）の検証。ネットワークには接続しない。

ここで確かめるのは「第14章の1次スクリーニングを条文どおりに実装したか」と
「ページの見た目が変わってもレースを取りこぼさないか」の2点。
実際のページ構造は Actions 側の `card.py probe` で確認する
（サンドボックスからは netkeiba に到達できない）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date  # noqa: E402

import card  # noqa: E402
import form as form_module  # noqa: E402


RACE_LIST = '''
<dl class="RaceList_DataList">
<dd><ul>
<li class="RaceList_DataItem">
  <a href="../race/shutuba.html?race_id=202601010601&amp;rf=race_list">
    <div class="Race_Num"><span>1</span>R</div>
    <span class="RaceList_Itemtime">09:50</span>
    <span class="ItemTitle">2歳新馬</span>
    <span class="ItemLong">芝・右 1500m</span>
    <span class="RaceList_Itemtime">12頭</span>
  </a>
</li>
<li class="RaceList_DataItem">
  <a href="../race/shutuba.html?race_id=202601010611&amp;rf=race_list">
    <span class="RaceList_Itemtime">15:25</span>
    <span class="Icon_GradeType Icon_GradeType5 Icon_GradePos01"></span>
    <span class="ItemTitle">UHB賞</span>
    <span class="ItemLong">芝・右 1200m</span>
    <span class="RaceList_Itemtime">16頭</span>
  </a>
</li>
<li class="RaceList_DataItem">
  <a href="../race/shutuba.html?race_id=202604020607&amp;rf=race_list">
    <span class="RaceList_Itemtime">15:35</span>
    <span class="Icon_GradeType Icon_GradeType3 Icon_GradePos01"></span>
    <span class="Icon_GradeType Icon_GradeType13"></span>
    <span class="ItemTitle">レパードステークス</span>
    <span class="ItemLong">ダ・左 1800m</span>
    <span class="RaceList_Itemtime">12頭</span>
  </a>
</li>
</ul></dd>
</dl>
'''


# ----------------------------------------------------------------------
# レース一覧
# ----------------------------------------------------------------------

def test_その日のレースを一覧できる():
    races = card.parse_race_list(RACE_LIST)
    assert len(races) == 3
    uhb = [r for r in races if r['race_id'] == '202601010611'][0]
    assert uhb['venue'] == '札幌'
    assert uhb['race_no'] == 11
    assert uhb['name'] == 'UHB賞'
    assert uhb['start_time'] == '15:25'
    assert uhb['surface'] == '芝'
    assert uhb['distance'] == 1200
    assert uhb['field_size'] == 16


def test_場とR番号はrace_idから引くのでHTMLに依存しない():
    """netkeiba は div の階層をよく変える。race_id との対応は変わらない。"""
    races = card.parse_race_list(
        '<li class="RaceList_DataItem"><a href="?race_id=202604020607"></a></li>')
    assert races[0]['venue'] == '新潟'
    assert races[0]['race_no'] == 7


def test_ダは表記をダートに揃える():
    races = card.parse_race_list(RACE_LIST)
    leopard = [r for r in races if r['race_id'] == '202604020607'][0]
    assert leopard['surface'] == 'ダート'
    assert leopard['grade'] == 'G3'


def test_クラス表示のアイコンだけを読む():
    """同じ塊に Icon_GradeType13（別の意味）が入る。位置で選び分ける。"""
    races = card.parse_race_list(RACE_LIST)
    leopard = [r for r in races if r['race_id'] == '202604020607'][0]
    assert leopard['grade'] == 'G3'


def test_16から18は重賞ではなくクラス():
    """浜名湖特別（2勝クラス）をG2と読み違えて素通りさせた実例がある。"""
    block = ('<li class="RaceList_DataItem"><a href="?race_id=202607020606">'
             '<span class="ItemTitle">浜名湖特別</span>'
             '<span class="Icon_GradeType Icon_GradeType17 Icon_GradePos01"></span>'
             '</a></li>')
    race = card.parse_race_list(block)[0]
    assert race['grade'] == '2勝'
    assert card.screen(race)['score'] == 0      # 重賞のような加点はしない


def test_知らないアイコン番号は重賞に化けさせない():
    block = ('<li class="RaceList_DataItem"><a href="?race_id=202607020606">'
             '<span class="Icon_GradeType Icon_GradeType99 Icon_GradePos01"></span>'
             '</a></li>')
    assert card.parse_race_list(block)[0]['grade'] is None


def test_特別戦はアイコンでしかクラスが分からない():
    """「3歳以上2勝クラス」は名前から読めるが、特別戦は名前に入らない。"""
    assert card.race_class({'name': '3歳以上2勝クラス', 'grade': None}) == '2勝'
    assert card.race_class({'name': '富良野特別', 'grade': None}) is None
    assert card.race_class({'name': '富良野特別', 'grade': '2勝'}) == '2勝'


def test_同じレースを二重に数えない():
    races = card.parse_race_list(RACE_LIST + RACE_LIST)
    assert len(races) == 3


# ----------------------------------------------------------------------
# 1次スクリーニング（第14章）
# ----------------------------------------------------------------------

def make_race(name='テスト賞', grade=None, surface='芝'):
    return {'race_id': '202601010611', 'name': name, 'grade': grade,
            'surface': surface, 'venue': '札幌', 'race_no': 11}


def test_2歳新馬戦は無条件で除外する():
    result = card.screen(make_race(name='2歳新馬'))
    assert result['excluded'] is not None
    assert '新馬' in result['excluded']


def test_障害レースは無条件で除外する():
    result = card.screen(make_race(name='障害3歳以上未勝利', surface='障'))
    assert result['excluded'] is not None


def test_重賞は優先度が上がる():
    result = card.screen(make_race(grade='G3'))
    assert result['excluded'] is None
    assert result['score'] > 0


def test_ハンデ戦は優先度が上がる():
    plain = card.screen(make_race(name='オープン特別'))
    handicap = card.screen(make_race(name='オープン特別ハンデ'))
    assert handicap['score'] > plain['score']


def test_未勝利は優先度が下がる():
    result = card.screen(make_race(name='3歳未勝利'))
    assert result['score'] < 0
    assert result['excluded'] is None       # 除外ではなく優先度を下げるだけ


def test_1番人気が1倍台前半なら大きく下げる():
    """第8章「1倍台の圧倒的1番人気は見送り」と整合させる。"""
    race = make_race(grade='G3')
    strong = card.screen(race, {'favourite': 1.3, 'spread': 8.0})
    normal = card.screen(race, {'favourite': 4.2, 'spread': 8.0})
    assert strong['score'] < normal['score']
    assert any('1倍台' in r for r in strong['reasons'])


def test_上位人気が割れていれば優先度が上がる():
    race = make_race(grade='G3')
    split = card.screen(race, {'favourite': 4.2, 'spread': 2.5})
    clear = card.screen(race, {'favourite': 4.2, 'spread': 9.0})
    assert split['score'] > clear['score']


def test_候補は点数の高い順に選ぶ():
    races = [
        {**make_race(name='3歳未勝利'), 'start_time': '10:00',
         'screening': card.screen(make_race(name='3歳未勝利'))},
        {**make_race(grade='G3'), 'start_time': '15:35',
         'screening': card.screen(make_race(grade='G3'))},
        {**make_race(name='2歳新馬'), 'start_time': '09:50',
         'screening': card.screen(make_race(name='2歳新馬'))},
    ]
    chosen = card.select_candidates(races, limit=2)
    assert [r['name'] for r in chosen] == ['テスト賞', '3歳未勝利']
    # 除外されたレースは候補に入らない
    assert all(r['screening']['excluded'] is None for r in chosen)


# ----------------------------------------------------------------------
# カードの鮮度
# ----------------------------------------------------------------------

def test_夕方以降なら翌日を対象にする():
    """前夜に走らせるため。金曜21時に金曜（開催なし）のカードを作らない。"""
    from datetime import datetime
    import bets
    friday_night = datetime(2026, 8, 14, 21, 7, tzinfo=bets.JST)
    assert card.target_day(friday_night) == date(2026, 8, 15)


def test_夕方より前なら当日を対象にする():
    """予備の01:07と、朝の取り直しの両方がこの枝を通る。"""
    from datetime import datetime
    import bets
    assert card.target_day(datetime(2026, 8, 15, 1, 7, tzinfo=bets.JST)) == date(2026, 8, 15)
    assert card.target_day(datetime(2026, 8, 15, 7, 0, tzinfo=bets.JST)) == date(2026, 8, 15)


def test_作りたてのカードは作り直さない(tmp_path, monkeypatch):
    """定時実行を二重にかけても取得が二度走らないための逃げ道。"""
    from datetime import datetime, timedelta
    import bets

    monkeypatch.setattr(card, 'CARDS_DIR', str(tmp_path))
    day = date(2026, 8, 15)
    made = datetime(2026, 8, 14, 21, 30, tzinfo=bets.JST)
    card.save_card({'date': day.isoformat(),
                    'generated_at': made.isoformat(), 'races': []})

    now = made + timedelta(hours=4)
    assert card.card_age_hours(day, now=now) == pytest.approx(4.0)


def test_カードが無ければ鮮度は分からない(tmp_path, monkeypatch):
    monkeypatch.setattr(card, 'CARDS_DIR', str(tmp_path))
    assert card.card_age_hours(date(2026, 8, 15)) is None


# ----------------------------------------------------------------------
# 馬ごとの事実（近走・血統）
# ----------------------------------------------------------------------

HORSE_PAGE = '''
<table class="db_h_race_results nk_tb_common">
<tr><th>日付</th><th>開催</th><th>天 気</th><th>R</th><th>レース名</th><th>頭 数</th>
    <th>枠 番</th><th>馬 番</th><th>オ ッ ズ</th><th>人 気</th><th>着 順</th><th>騎手</th>
    <th>斤 量</th><th>距離</th><th>馬 場</th><th>馬場 指数</th><th>タイム</th><th>着差</th>
    <th>上 が り 指 数</th><th>通過</th><th>ペース</th><th>上り</th><th>馬体重</th>
    <th>勝ち馬 (2着馬)</th></tr>
<tr><td>2026/07/19</td><td>2札幌2</td><td>晴</td><td>11</td><td>羊ヶ丘特別</td><td>14</td>
    <td>3</td><td>5</td><td>8.4</td><td>4</td><td>1</td><td>横山武</td>
    <td>54.0</td><td>芝1200</td><td>良</td><td></td><td>1:08.4</td><td>-0.2</td>
    <td></td><td>3-3</td><td>34.1-34.3</td><td>34.2</td><td>424(-2)</td>
    <td>(ウマニ)</td></tr>
<tr><td>2026/06/07</td><td>3東京4</td><td>雨</td><td>9</td><td>むらさき賞</td><td>18</td>
    <td>8</td><td>17</td><td>21.0</td><td>9</td><td>12</td><td>戸崎</td>
    <td>55.0</td><td>芝1400</td><td>重</td><td></td><td>1:22.9</td><td>1.4</td>
    <td></td><td>15-14</td><td>35.0-36.1</td><td>36.8</td><td>426(+2)</td>
    <td>ウマサン</td></tr>
</table>
<table class="blood_table detail">
<tr><td rowspan="16"><a href="/horse/ped/2015104123/">キズナ</a> 2010 青鹿毛</td>
    <td rowspan="8"><a href="/horse/ped/2005103228/">ディープインパクト</a> 2002</td>
    <td rowspan="4"><a href="/horse/ped/1999110099/">サンデーサイレンス</a> 1986</td></tr>
<tr><td rowspan="8"><a href="/horse/ped/1996100011/">キャットクイル</a> 1990</td></tr>
<tr><td rowspan="16"><a href="/horse/2012104555/">ウマノハハ</a> 2012 鹿毛</td>
    <td rowspan="8"><a href="/horse/ped/2003100234/">クロフネ</a> 1998</td>
    <td rowspan="4"><a href="/horse/ped/1993100022/">フレンチデピュティ</a> 1992</td></tr>
<tr><td rowspan="8"><a href="/horse/ped/2000100111/">ウマノソボ</a> 2000</td></tr>
</table>
'''


def test_近走を新しい順に読む():
    runs = form_module.parse_recent_runs(HORSE_PAGE)
    assert len(runs) == 2
    assert runs[0]['date'] == '2026/07/19'
    assert runs[0]['rank'] == 1
    assert runs[0]['jockey'] == '横山武'


def test_距離は馬場と数字に割る():
    runs = form_module.parse_recent_runs(HORSE_PAGE)
    assert runs[0]['surface'] == '芝'
    assert runs[0]['distance'] == 1200
    assert runs[1]['distance'] == 1400


def test_馬体重は増減と分けて持つ():
    runs = form_module.parse_recent_runs(HORSE_PAGE)
    assert runs[0]['weight'] == 424
    assert runs[0]['weight_diff'] == -2
    assert runs[1]['weight_diff'] == 2


def test_通過順と上がりを残す():
    """脚質（第8章「逃げ・先行馬を優先」）と不利の判定に要る。"""
    runs = form_module.parse_recent_runs(HORSE_PAGE)
    assert runs[0]['passing'] == '3-3'
    assert runs[0]['last3f'] == 34.2


def test_列の順が変わっても見出しで引く():
    """netkeiba は列を入れ替える。位置で読むと静かに間違った値が入る。"""
    swapped = HORSE_PAGE.replace(
        '<th>着 順</th><th>騎手</th>', '<th>騎手</th><th>着 順</th>').replace(
        '<td>1</td><td>横山武</td>', '<td>横山武</td><td>1</td>')
    runs = form_module.parse_recent_runs(swapped)
    assert runs[0]['rank'] == 1
    assert runs[0]['jockey'] == '横山武'


def test_見出しに空白が混ざっていても読む():
    """実物の見出しは「着 順」「オ ッ ズ」と1文字ずつ割れている。"""
    runs = form_module.parse_recent_runs(HORSE_PAGE)
    assert runs[0]['ninki'] == 4
    assert runs[0]['odds'] == 8.4
    assert runs[0]['field_size'] == 14


def test_馬場指数を馬場と取り違えない():
    """「馬 場」と「馬場 指数」は空白を詰めると紛らわしい。"""
    record = form_module.parse_going_record(HORSE_PAGE)
    assert record == {'良': [1, 0, 0, 0], '重': [0, 0, 0, 1]}


def test_近走の件数を絞れる():
    assert len(form_module.parse_recent_runs(HORSE_PAGE, limit=1)) == 1


def test_父と母父を取る():
    """第5章のダート適性・第12章の血統分析が使う。"""
    pedigree = form_module.parse_pedigree(HORSE_PAGE)
    assert pedigree['sire'] == 'キズナ'
    assert pedigree['dam'] == 'ウマノハハ'
    assert pedigree['broodmare_sire'] == 'クロフネ'


def test_血統表が無ければ空で返す():
    assert form_module.parse_pedigree('<html></html>') == {}


def test_競走成績が無ければ空で返す():
    assert form_module.parse_recent_runs('<html></html>') == []


# ----------------------------------------------------------------------
# 馬名検索（交流重賞のJRA所属馬。地方の公式データにはhorse_idが無い）
# ----------------------------------------------------------------------
#
# 2026-08-13にActionsのprobeで確認した実物の形を縮めて使う
# （data/probe/last.txt、「ラッキーキッド」「キッド」で実測）。

SINGLE_MATCH_PAGE = '''
<title>ラッキーキッド (Lucky Kid) | 競走馬データ - netkeiba</title>
<link rel="canonical" href="https://db.netkeiba.com/horse/2023101148/" />
<meta property="og:url" content="https://db.netkeiba.com/horse/2023101148/" />
'''

LIST_PAGE = '''
<title>馬名[キッド]の競走馬検索結果｜競馬データベース - netkeiba.com</title>
<tr>
<td nowrap="nowrap"><input type="checkbox" name="horse_id[]" value="1993101014" id="chk_horse"></td>
<td class="xml txt_l w_human" nowrap="nowrap"><a href="/horse/1993101014/" title="キッド">キッド</a></td>
</tr><tr>
<td nowrap="nowrap"><input type="checkbox" name="horse_id[]" value="2021105146" id="chk_horse"></td>
<td class="bml txt_l w_human" nowrap="nowrap"><a href="/horse/2021105146/" title="キッドストン">キッドストン</a></td>
</tr><tr>
<td nowrap="nowrap"><input type="checkbox" name="horse_id[]" value="2014101426" id="chk_horse"></td>
<td class="bml txt_l w_human" nowrap="nowrap"><a href="/horse/2014101426/" title="キッド">キッド</a></td>
</tr>
'''


def test_1頭に絞れる検索はcanonicalのhorse_idを返す():
    assert form_module.parse_horse_search(SINGLE_MATCH_PAGE, 'ラッキーキッド') == '2023101148'


def test_複数該当でも馬名が完全一致する行が1つならそれを返す():
    assert form_module.parse_horse_search(LIST_PAGE, 'キッドストン') == '2021105146'


def test_同姓同名が複数いたら推測せずNoneを返す():
    # 「キッド」は1993年生と2014年生の2頭がヒットする。どちらかを勝手に選ばない。
    assert form_module.parse_horse_search(LIST_PAGE, 'キッド') is None


def test_該当なしはNoneを返す():
    assert form_module.parse_horse_search(LIST_PAGE, 'いない馬') is None


def test_search_horse_idはfetchしたページをparse_horse_searchに渡す(monkeypatch):
    calls = []

    def fake_fetch(url, opener=None):
        calls.append(url)
        return SINGLE_MATCH_PAGE

    monkeypatch.setattr(form_module, '_fetch', fake_fetch)
    assert form_module.search_horse_id('ラッキーキッド') == '2023101148'
    assert 'word=' in calls[0]


def test_fetch_jra_recent_runsは見つからなければNoneを返す(monkeypatch):
    monkeypatch.setattr(form_module, 'search_horse_id', lambda name, opener=None: None)
    assert form_module.fetch_jra_recent_runs('いない馬') is None


def test_fetch_jra_recent_runsはhorse_idの近走を返す(monkeypatch):
    monkeypatch.setattr(form_module, 'search_horse_id',
                        lambda name, opener=None: '2023101148')
    monkeypatch.setattr(form_module, '_fetch', lambda url, opener=None: HORSE_PAGE)
    runs = form_module.fetch_jra_recent_runs('ラッキーキッド')
    assert len(runs) == 2
