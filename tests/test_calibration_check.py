#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主観勝率の乖離チェック（calibration_check.py）の計算部分を確かめる。

架空の単勝オッズ・架空の主観勝率だけを使う（test_backtest_pairs.py と同じ
理由：実在レースの数字を手で書き起こすと、書き間違いがそのまま「実績」に
見えてしまう）。ネットワークにも data/ にも触らない。
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bet_builder  # noqa: E402
import calibration_check  # noqa: E402

# 単勝2.0/4.0/4.0倍 → 控除率を除いて正規化すると 0.50/0.25/0.25 ちょうど。
WIN_ODDS = {1: 2.0, 2: 4.0, 3: 4.0}
MARKET = bet_builder.market_win_probabilities(WIN_ODDS)


def test_上書きされた馬だけが比の対象になり出走表に無い馬番は落ちる():
    # 1番は市場0.50に対し主観0.60（1.2倍）、3番は0.25に対し0.20（0.8倍）。
    # 2番は上書きが無いので対象外。9番は出走表に無いので落ちる。
    rows = calibration_check.horse_ratios(
        {1: 0.60, 3: 0.20, 9: 0.30}, MARKET, mark_of={1: '◎', 3: '▲'}.get)

    assert [r['umaban'] for r in rows] == [1, 3]
    assert [r['mark'] for r in rows] == ['◎', '▲']
    assert rows[0]['ratio'] == 1.2
    assert rows[1]['ratio'] == 0.8


def test_上限は市場の倍率を超えた分だけを削り下方向は触らない():
    # 上限1.3倍 → 1番の天井は 0.50*1.3=0.65、2番の天井は 0.25*1.3=0.325。
    capped = calibration_check.capped_overrides(
        {1: 0.80, 2: 0.20}, MARKET, 1.3)

    assert capped[1] == 0.65        # 0.80 は天井まで削られる
    assert capped[2] == 0.20        # 市場より低い見積りはそのまま


def test_セット的中率は券種が混ざっても重複なく合算される():
    p = dict(MARKET)
    # 3頭立てなので、どのワイドも必ず両馬が3着以内＝的中率1.0。
    assert calibration_check.set_hit_rate(p, [('ワイド', [1, 2])]) == 1.0
    # 単勝1点は、その馬が1着になる確率そのもの。
    assert abs(calibration_check.set_hit_rate(p, [('単勝', [1])]) - 0.50) < 1e-9
    # 単勝1番とワイド2-3を同時に持っても、ワイドが必ず当たる以上1.0を超えない
    # （単純合算なら1.5になってしまう）。
    assert abs(calibration_check.set_hit_rate(
        p, [('単勝', [1]), ('ワイド', [2, 3])]) - 1.0) < 1e-9


def test_期間の区切りは境界日を含めて振り分ける():
    old, middle, latest = (label for _s, _e, label in calibration_check.PERIODS)

    assert calibration_check.period_of(date(2026, 8, 26)) == old
    assert calibration_check.period_of(calibration_check.WIN_PROB_START) == middle
    assert calibration_check.period_of(date(2026, 9, 15)) == middle
    assert calibration_check.period_of(
        calibration_check.WIN_ODDS_LOGGED_FROM) == latest
