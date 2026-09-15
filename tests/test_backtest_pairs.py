#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""印ペアのバックテスト（backtest_pairs.py）の組の列挙と払戻の合算を確かめる。

着順・払戻のフィクスチャは架空のものを使う（test_review.py と同じ理由：
実在レースの着順を手で書き起こすと、書き間違いがそのまま「実績」として
data/results/ に残りかねない）。ネットワークには接続しない。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest_pairs  # noqa: E402
from bets import Bet, RaceBets  # noqa: E402


def make_race(marks, bet_list=()):
    return RaceBets(
        race_id='202601020811',
        name='テストステークス',
        start_time='15:25',
        confidence='B',
        marks=[{'mark': m, 'umaban': u} for m, u in marks],
        bets=[Bet(t, h) for t, h in bet_list],
    )


# 印は ◎1・○2・▲3・△4。1着1・2着3・3着4（○2だけが着外）。
RESULT = {
    'race_id': '202601020811',
    'finishing_order': [
        {'rank': 1, 'umaban': 1},
        {'rank': 2, 'umaban': 3},
        {'rank': 3, 'umaban': 4},
        {'rank': 4, 'umaban': 2},
    ],
    'payouts': {
        'ワイド': [
            {'combination': [1, 3], 'yen': 300},
            {'combination': [1, 4], 'yen': 800},
            {'combination': [3, 4], 'yen': 1500},
        ],
    },
}

MARKS = [('◎', 1), ('○', 2), ('▲', 3), ('△', 4)]


def test_V1は本命と他の印の組だけを買う():
    race = make_race(MARKS)
    assert backtest_pairs.v1_combos(race) == [[1, 2], [1, 3], [1, 4]]


def test_V2は印馬同士の全組み合わせを買う():
    race = make_race(MARKS)
    assert backtest_pairs.v2_combos(race) == [
        [1, 2], [1, 3], [1, 4], [2, 3], [2, 4], [3, 4],
    ]


def test_V1の払戻は的中した組だけを合算する():
    race = make_race(MARKS)
    settled = backtest_pairs.settle_wide_set(backtest_pairs.v1_combos(race), RESULT)
    # 3点300円。1-2は着外で0円、1-3が300円、1-4が800円。
    assert settled == {'staked': 300, 'returned': 1100, 'hit': True}


def test_V2は印同士の組も拾う():
    race = make_race(MARKS)
    settled = backtest_pairs.settle_wide_set(backtest_pairs.v2_combos(race), RESULT)
    # 6点600円。的中は1-3(300)・1-4(800)・3-4(1500)。
    assert settled == {'staked': 600, 'returned': 2600, 'hit': True}


def test_V3は既に買っている組を二重に足さない():
    race = make_race(MARKS, [('ワイド', [1, 3]), ('ワイド', [2, 3])])
    assert backtest_pairs.v3_extra_combos(race) == [[1, 2], [1, 4]]
    settled = backtest_pairs.settle_v3(race, RESULT)
    # 実際の買い目2点（1-3が300円・2-3は外れ）＋追加2点（1-2外れ・1-4が800円）。
    assert settled == {'staked': 400, 'returned': 1100, 'hit': True}


def test_本命が無ければV1は買わない():
    race = make_race([('○', 2), ('▲', 3)])
    assert backtest_pairs.v1_combos(race) == []
    assert backtest_pairs.settle_wide_set([], RESULT) == {
        'staked': 0, 'returned': 0, 'hit': False}
