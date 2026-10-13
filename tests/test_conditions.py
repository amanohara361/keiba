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
