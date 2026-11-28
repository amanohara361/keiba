#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""馬場状態の読み取り（conditions.py）。ネットワークには接続しない。"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import conditions  # noqa: E402


@pytest.mark.parametrize('text, going', [
    ('天候:曇 / 馬場:稍', '稍重'),     # race.netkeiba.com の略記
    ('天候:雨 / 馬場:不', '不良'),
    ('天候:曇 / 馬場:重', '重'),
    ('天候:晴 / 馬場:良', '良'),
    ('芝 : 稍重', '稍重'),
    ('ダート:不良', '不良'),
])
def test_略記も正式名で読む(text, going):
    assert conditions.find_going(text) == going


def test_当日の結果ページのヘッダから稍重を読む():
    """2026-09-27 中山2R。直前検算がこの略記を読めず「未発表」扱いにしていた。"""
    path = os.path.join(ROOT, 'tests', 'fixture_nkresult_20260927_202606040902.html')
    with open(path, encoding='utf-8') as f:
        got = conditions.parse(f.read())
    assert got == {'going': '稍重', 'weather': '曇', 'surface': '芝', 'distance': 2000}


# 2026-10-13、probe で取った京都8R（202608040408）の RaceData02 の実物。
RACE_DATA02_FEMALE = """
<div class="RaceData02">
<span>4回</span>
<span>京都</span>
<span>4日目</span>
<span>サラ系３歳以上</span>
<span>２勝クラス</span>
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
<span>牝[指]</span>
<span>定量</span>
<span>16頭</span>
<br>
<span>本賞金:1190,480,300,180,119万円</span>
</div>
"""


def test_競走条件から牝馬限定と斤量の決め方を読む():
    got = conditions.parse_terms(RACE_DATA02_FEMALE)
    assert got['female_only'] is True
    assert got['weight_rule'] == '定量'
    assert got['race_terms'].startswith('4回 京都 4日目 サラ系３歳以上 ２勝クラス 牝[指] 定量')


def test_混合戦は牝馬限定にしない():
    """「牝」を含む語でも、牝で始まる語でなければ数えない。"""
    page = RACE_DATA02_FEMALE.replace('<span>牝[指]</span>', '<span>(混)[指]</span>')
    assert conditions.parse_terms(page)['female_only'] is False
    page = RACE_DATA02_FEMALE.replace('<span>牝[指]</span>', '<span>牡・牝(特指)</span>')
    assert conditions.parse_terms(page)['female_only'] is False


def test_ハンデ戦を読む():
    page = RACE_DATA02_FEMALE.replace('<span>定量</span>', '<span>ハンデ</span>')
    assert conditions.parse_terms(page)['weight_rule'] == 'ハンデ'


def test_競走条件の欄が無ければNone():
    got = conditions.parse_terms('<div class="RaceData01">ダ1800m</div>')
    assert got == {'female_only': None, 'weight_rule': None, 'race_terms': None}



# 2026-10-13、セッションから直接取った出馬表の RaceData01 の実物。
RACE_DATA01_KYOTO_TURF = ('<div class="RaceData01">\n14:50発走 /<!-- <span class="Turf"> --><span> 芝1400m</span> '
                          '(右 外&nbsp;A)\n/ 天候:晴<span class="Icon_Weather Weather01"></span>\n'
                          '<span class="Item03">/ 馬場:良</span>\n\n</div>')    # 宇治川特別 202608040310
RACE_DATA01_TOKYO_TURF = ('<div class="RaceData01">\n15:30発走 /<!-- <span class="Turf"> --><span> 芝2000m</span> '
                          '(左&nbsp;A)\n/ 天候:晴<span class="Icon_Weather Weather01"></span>\n'
                          '<span class="Item03">/ 馬場:良</span>\n\n</div>')    # オクトーバーS 202605040511
RACE_DATA01_KYOTO_DIRT = ('<div class="RaceData01">\n13:40発走 /<!-- <span class="Turf"> --><span> ダ1800m</span> (右)\n'
                          '/ 天候:晴<span class="Icon_Weather Weather01"></span>\n\n'
                          '<span class="Item04">/ 馬場:良</span>\n</div>')     # 京都8R 202608040408


def test_コース区分と内外回りを読む():
    got = conditions.parse_course(RACE_DATA01_KYOTO_TURF)
    assert got == {'turn': '右', 'loop': '外', 'rail': 'A', 'course_note': '右 外 A'}


def test_内外回りの無いコースはNone():
    got = conditions.parse_course(RACE_DATA01_TOKYO_TURF)
    assert got == {'turn': '左', 'loop': None, 'rail': 'A', 'course_note': '左 A'}


def test_ダートはコース区分が付かない():
    got = conditions.parse_course(RACE_DATA01_KYOTO_DIRT)
    assert got == {'turn': '右', 'loop': None, 'rail': None, 'course_note': '右'}


def test_コースの欄が無ければNone():
    got = conditions.parse_course('<div class="RaceData02">2勝クラス</div>')
    assert got == {'turn': None, 'loop': None, 'rail': None, 'course_note': None}


# 2026-11-14、出馬表の RaceData01 の書き方が変わった（括弧が消え、天候はアイコンの aria-label に）。
# 11-01〜11-14 のカードはこれを読めず、turn・loop・rail・weather が全レース null だった。
RACE_DATA01_2026_11_KYOTO_TURF = (
    '<div class="RaceData01">\n15:45発走 /<!-- <span class="Turf"> --><span> 芝1600m</span> 右 外&nbsp;B\n\n'
    '/ <span>予報</span><span class="Icon_Weather02 Icon_W02" role="img" aria-label="曇" title="曇"></span>\n\n'
    '<span class="Item03"> 良</span>\n\n<span class="Item05">/ 直線：弱追風</span>\n</div>')  # デイリー杯2歳S 202608050311
RACE_DATA01_2026_11_KYOTO_INNER = (
    '<div class="RaceData01">\n15:05発走 /<!-- <span class="Turf"> --><span> 芝2000m</span> 右&nbsp;B\n\n'
    '/ <span>予報</span><span class="Icon_Weather02 Icon_W02" role="img" aria-label="曇" title="曇"></span>\n\n'
    '<span class="Item03"> 良</span>\n\n<span class="Item05">/ 直線：弱追風</span>\n</div>')  # アンドロメダS 202608050310
RACE_DATA01_2026_11_TOKYO_DIRT = (
    '<div class="RaceData01">\n15:30発走 /<!-- <span class="Turf"> --><span> ダ1600m</span> 左\n\n'
    '/ <span>予報</span><span class="Icon_Weather02 Icon_W02" role="img" aria-label="曇" title="曇"></span>\n\n\n'
    '<span class="Item04"> 良</span>\n<span class="Item05">/ 直線：弱横風</span>\n</div>')  # 武蔵野S 202605050311


def test_括弧の無いコース表記を読む():
    got = conditions.parse_course(RACE_DATA01_2026_11_KYOTO_TURF)
    assert got == {'turn': '右', 'loop': '外', 'rail': 'B', 'course_note': '右 外 B'}
    got = conditions.parse_course(RACE_DATA01_2026_11_KYOTO_INNER)
    assert got == {'turn': '右', 'loop': None, 'rail': 'B', 'course_note': '右 B'}
    got = conditions.parse_course(RACE_DATA01_2026_11_TOKYO_DIRT)
    assert got == {'turn': '左', 'loop': None, 'rail': None, 'course_note': '左'}


def test_天候をアイコンのラベルから読む():
    got = conditions.parse(RACE_DATA01_2026_11_TOKYO_DIRT)
    assert got == {'going': '良', 'weather': '曇', 'surface': 'ダート', 'distance': 1600}


def test_旧い書き方の天候も従来どおり読む():
    assert conditions.parse(RACE_DATA01_KYOTO_TURF)['weather'] == '晴'


# 2026-11-15 実物：ラベルの無い略記（稍・不）。最後の手段の正規表現では拾えなかった。
RACE_DATA01_2026_11_FUKUSHIMA_YAYA = (
    '<div class="RaceData01">\n14:35発走 /<!-- <span class="Turf"> --><span> 芝2600m</span> 右&nbsp;A\n\n'
    '/ <span>予報</span><span class="Icon_Weather02 Icon_W04" role="img" aria-label="雨" title="雨"></span>\n\n'
    '<span class="Item03"> 稍</span>\n\n<span class="Item05">/ 直線：無風</span>\n</div>')  # 福島10R 202603030410


def test_ラベルの無い略記の馬場状態を読む():
    assert conditions.parse(RACE_DATA01_2026_11_FUKUSHIMA_YAYA)['going'] == '稍重'
    fuller = RACE_DATA01_2026_11_TOKYO_DIRT.replace('Item04"> 良', 'Item04"> 不')
    assert conditions.parse(fuller)['going'] == '不良'


# ----------------------------------------------------------------------
# 馬場は JRA 公式の馬場発表が正本、netkeiba は予備（2026-11-28）
# ----------------------------------------------------------------------

import check  # noqa: E402
import jra_official  # noqa: E402

BABA = [
    {'venue': '東京', 'date': '20261128', 'kai': '5', 'day': '7', 'weather': '雨',
     'going_turf': '稍重', 'going_dirt': '重', 'decided': None, 'cancel_info': None, 'changes': {}},
    {'venue': '京都', 'date': '20261128', 'kai': '5', 'day': '7', 'weather': '晴',
     'going_turf': '良', 'going_dirt': '稍重', 'decided': None, 'cancel_info': None, 'changes': {}},
    {'venue': '東京', 'date': '20261129', 'kai': '5', 'day': '8', 'weather': None,
     'going_turf': None, 'going_dirt': None, 'decided': None, 'cancel_info': None, 'changes': {}},
]


def test_レースの芝ダに合わせて開催場の馬場発表を引く():
    assert jra_official.going_for_race(BABA, '202605050708', '芝') == {'going': '稍重', 'weather': '雨'}
    assert jra_official.going_for_race(BABA, '202605050708', 'ダート') == {'going': '重', 'weather': '雨'}
    assert jra_official.going_for_race(BABA, '202608050711', '芝')['going'] == '良'
    assert jra_official.going_for_race(BABA, '202605050708', '障害')['going'] == '稍重'


def test_芝ダ不明や未発表や開催違いは推測で埋めない():
    assert jra_official.going_for_race(BABA, '202605050708', None)['going'] is None
    assert jra_official.going_for_race(BABA, '202605050808', '芝')['going'] is None   # 翌日・未発表
    assert jra_official.going_for_race(BABA, '202609050708', '芝')['going'] is None   # 阪神は開催なし


def test_直前検算の馬場はJRA公式から読む(monkeypatch):
    monkeypatch.setattr(jra_official, 'fetch_baba', lambda opener=None: BABA)
    monkeypatch.setattr(conditions, 'fetch', lambda race_id, **k: pytest.fail('netkeiba を読みに行った'))
    got = check.fetch_conditions_jra_first('202605050708', {'surface': '芝', 'distance': 1600})
    assert got['going'] == '稍重' and got['weather'] == '雨'
    assert got['surface'] == '芝' and got['distance'] == 1600
    assert conditions.is_heavy(got)


def test_JRA公式で未発表ならnetkeibaを読む(monkeypatch):
    monkeypatch.setattr(jra_official, 'fetch_baba', lambda opener=None: BABA)
    netkeiba = {'going': '良', 'weather': '晴', 'surface': '芝', 'distance': 2000}
    monkeypatch.setattr(conditions, 'fetch', lambda race_id, **k: netkeiba)
    assert check.fetch_conditions_jra_first('202605050808', {'surface': '芝'}) == netkeiba


def test_両方取れなければ未発表として検算を続ける(monkeypatch):
    def down(race_id, **k):
        raise conditions.ConditionsError('HTTP Error 400')
    monkeypatch.setattr(conditions, 'fetch', down)   # JRA 公式は conftest で取れない扱い
    got = check.fetch_conditions_jra_first('202605050708', {'surface': '芝', 'distance': 1600})
    assert got['going'] is None and got['surface'] == '芝'


def test_馬場発表は1回の実行で1度だけ取りに行く(monkeypatch):
    calls = []
    monkeypatch.setattr(jra_official, 'fetch_baba', lambda opener=None: calls.append(1) or BABA)
    jra_official.fetch_baba_once()
    jra_official.fetch_baba_once()
    assert calls == [1]
