#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""馬体重の大幅増減（±15kg以上）を検算で知らせ、買い目に反映することを確かめる。

2026-10-08 ユーザー依頼。第1章「-2点：馬体重が±15kg以上変動」と、第2章「何kgで
評価を下げるか事前に決めておく」を、直前検算の機械ルールにした。印は動かさず、
朝の主観勝率の上乗せ（市場より上げた分）だけを取り消す。ネットワークには接続しない。
"""

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bet_builder  # noqa: E402
import discipline  # noqa: E402
import form as form_module  # noqa: E402
from bets import JST, RaceBets  # noqa: E402
from test_missing_input import WIN_ODDS, _lookup  # noqa: E402


def _race(win_probabilities=None):
    return RaceBets(
        race_id='202605040511', name='テスト記念', venue='東京', race_no=11,
        start_time='15:40',
        marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11},
               {'mark': '▲', 'umaban': 14}],
        partners=[{'umaban': 3}],
        bets=[], win_probabilities=win_probabilities)


def _entry(diff, age=4, weight=480):
    return {'name': 'ウマ', 'weight': weight, 'weight_diff': diff, 'age': age}


# ----------------------------------------------------------------------
# 出馬表
# ----------------------------------------------------------------------

def test_出馬表から年齢を読む():
    page = '''<tr class="HorseList">
<td class="Umaban1 Txt_C">1</td>
<td class="HorseInfo"><a href="https://db.netkeiba.com/horse/2023106194" title="A">A</a></td>
<td class="Barei Txt_C">牡3</td><td class="Txt_C">56.0</td>
<td class="Weight">498(+16)</td></tr>'''
    entry = form_module.parse_entries(page)[1]
    assert entry['age'] == 3
    assert entry['weight'] == 498
    assert entry['weight_diff'] == 16


# ----------------------------------------------------------------------
# 判定
# ----------------------------------------------------------------------

def test_15kg以上の増減だけを拾う():
    entries = {7: _entry(-15), 11: _entry(14), 14: _entry(None), 3: _entry(+20)}
    swings = discipline.weight_swings(_race(), entries)
    assert set(swings) == {7, 3}


def test_印も候補も無い馬は見ない():
    assert discipline.weight_swings(_race(), {5: _entry(-30)}) == {}


def test_3歳のプラス体重は成長分として勝率を戻さない():
    entries = {7: _entry(+16, age=3), 11: _entry(-16, age=3)}
    assert discipline.weight_withdrawals(_race(), entries) == {11}
    codes = [f.code for f in discipline.check_weight_swing(_race(), entries)]
    assert sorted(codes) == ['weight_growth', 'weight_swing']


def test_体重未発表なら何も言わない():
    assert discipline.check_weight_swing(_race(), {}) == []


# ----------------------------------------------------------------------
# 買い目への反映
# ----------------------------------------------------------------------

def test_上乗せしていた馬の勝率を市場に戻して組み直す():
    race = _race({7: 0.60, 11: 0.20})
    _, _, plain = bet_builder.build_bets(race, _lookup, WIN_ODDS)
    _, _, pulled = bet_builder.build_bets(race, _lookup, WIN_ODDS, withdrawn={7})

    assert '【馬体重】' not in plain
    assert pulled.startswith('【馬体重】7番')
    # 朝の値は data/bets に残すので書き換えない。
    assert race.win_probabilities[7] == 0.60


def test_市場より下げていた馬はそのまま():
    _, _, note = bet_builder.build_bets(
        _race({7: 0.30, 11: 0.20}), _lookup, WIN_ODDS, withdrawn={7})
    assert '【馬体重】' not in note


def test_上乗せを全部取り消しても書き漏らし扱いにしない():
    _, _, note = bet_builder.build_bets(
        _race({7: 0.60}), _lookup, WIN_ODDS, withdrawn={7})
    assert bet_builder.MISSING_INPUT not in note


# ----------------------------------------------------------------------
# 検算の記録
# ----------------------------------------------------------------------

def test_検算に警告と体重の記録が残る():
    race = _race({7: 0.60})
    now = datetime(2026, 10, 10, 14, 7, tzinfo=JST)
    entries = {7: _entry(-18), 11: _entry(+2), 5: _entry(+30)}
    verdict = discipline.review_race(
        race, [], {}, {}, now, date(2026, 10, 10), {'going': '良'}, {}, entries)

    assert 'weight_swing' in [f.code for f in verdict.warnings]
    weights = verdict.to_dict()['weights']
    assert weights['7'] == {'weight': 480, 'diff': -18, 'age': 4}
    assert '5' not in weights   # 印も候補も無い馬は残さない
