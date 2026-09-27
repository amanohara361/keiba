#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ハイブリッド馬券術のバックテスト（backtest_hybrid.py）の計算を確かめる。

着順・オッズは架空のもの（test_backtest_pairs.py と同じ理由）。ネットワークには
接続しない。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest_hybrid as bh  # noqa: E402
import bet_builder  # noqa: E402
from bets import RaceBets  # noqa: E402


P = {1: 0.40, 2: 0.25, 3: 0.20, 4: 0.15}


def leg(bet_type, horses, odds, p=P):
    return {'type': bet_type, 'horses': horses, 'odds': odds,
            'p': bh.leg_rate(p, bet_type, horses)}


def brute_force_return(p, legs):
    """全着順を総当たりして、1点同額のときの期待回収率を直接数える。"""
    total = 0.0
    for top3, prob in bet_builder._harville_top3(p):
        paid = sum(l['odds'] for l in legs
                   if bet_builder._combo_hits(l['type'], l['horses'], top3))
        total += prob * paid / len(legs)
    return total


def test_exact_expected_return_matches_enumeration_on_overlapping_legs():
    # 単勝1・ワイド1-2・3連複1-2-3 は同時に当たりうる（1-2-3着なら全部的中）。
    legs = [leg('単勝', [1], 3.0), leg('ワイド', [1, 2], 2.5), leg('3連複', [1, 2, 3], 6.0)]
    exact = bh.exact_expected_return(legs)
    assert abs(exact - brute_force_return(P, legs)) < 1e-12
    # 合成オッズ×合算的中率（第13章の近似）は、券種が混ざると正確な値と一致しない。
    composite = 1 / sum(1 / l['odds'] for l in legs)
    assert abs(composite * bh.union_rate(P, legs) - exact) > 1e-3


def test_union_rate_counts_overlaps_once():
    legs = [leg('単勝', [1], 3.0), leg('ワイド', [1, 2], 2.5)]
    union = bh.union_rate(P, legs)
    assert max(l['p'] for l in legs) <= union < sum(l['p'] for l in legs)
    expected = sum(prob for top3, prob in bet_builder._harville_top3(P)
                   if top3[0] == 1 or {1, 2} <= set(top3))
    assert abs(union - expected) < 1e-12


def test_no_trigami_requires_every_leg_to_cover_total_stake():
    assert bh.no_trigami([leg('単勝', [1], 3.0), leg('ワイド', [1, 2], 3.0),
                          leg('3連複', [1, 2, 3], 30.0)])
    assert not bh.no_trigami([leg('単勝', [1], 2.9), leg('ワイド', [1, 2], 6.0),
                              leg('3連複', [1, 2, 3], 30.0)])
    # 2点なら2.0倍でよい。
    assert bh.no_trigami([leg('単勝', [1], 2.0), leg('ワイド', [1, 2], 4.0)])
    assert not bh.no_trigami([])


def make_context(priced_odds, win_odds=None):
    race = RaceBets(
        race_id='202601020811', name='テストステークス', start_time='15:25',
        marks=[{'mark': '◎', 'umaban': 1}, {'mark': '○', 'umaban': 2},
               {'mark': '▲', 'umaban': 3}, {'mark': '△', 'umaban': 4}],
        bets=[], win_probabilities={1: 0.30},
    )
    win_odds = win_odds or {'1': 4.0, '2': 5.0, '3': 6.0, '4': 8.0, '5': 15.0, '6': 30.0}
    record = {'win_odds': win_odds, 'priced_odds': priced_odds}
    return bh.Context(race, record)


PRICED = {
    '単勝': {'1': 4.0},
    'ワイド': {'1-2': 3.2, '1-3': 3.8, '1-5': 9.0, '2-3': 8.0},
    '3連複': {'1-2-3': 12.0, '1-3-4': 30.0, '1-2-5': 45.0},
}


def test_build_hybrids_picks_highest_rate_legs_that_keep_mark_order():
    ctx = make_context(PRICED)
    portfolios, _reasons = bh.build_hybrids(ctx)
    h1 = portfolios['H1']
    assert [(l['type'], l['horses']) for l in h1['legs']] == [
        ('単勝', [1]), ('ワイド', [1, 2]), ('3連複', [1, 2, 3])]
    # 部品は◎絡みだけ（保険用の 2-3 ワイドは使わない）。
    assert all(1 in l['horses'] for s in ('H1', 'H2', 'H3') for l in portfolios[s]['legs'])
    assert [(l['type'], l['horses']) for l in portfolios['H2']['legs']] == [
        ('単勝', [1]), ('ワイド', [1, 2])]
    assert abs(h1['ev_exact'] - bh.exact_expected_return(h1['legs'])) < 1e-12
    # 上限（CAP）後の的中率を使っている。
    for l in h1['legs']:
        assert l['p'] <= l['p_mkt'] * bet_builder.SET_HIT_RATE_CAP + 1e-12


def test_build_hybrids_skips_shape_when_only_order_breaking_legs_exist():
    # ◎-○ のワイドが無く、残りは ○を外して▲や無印と組む組だけ。
    priced = dict(PRICED, ワイド={'1-3': 3.8})
    portfolios, reasons = bh.build_hybrids(make_context(priced))
    assert portfolios['H1'] is None and portfolios['H2'] is None
    assert 'ワイド' in reasons['H1']


def test_build_hybrids_respects_axis_odds_ceiling():
    win_odds = {'1': 12.0, '2': 3.0, '3': 5.0, '4': 8.0, '5': 15.0, '6': 30.0}
    portfolios, reasons = bh.build_hybrids(make_context(PRICED, win_odds))
    assert all(p is None for p in portfolios.values())
    assert '軸の上限' in reasons['H1']


def portfolio(odds, ev_exact, composite, union):
    legs = [{'type': '単勝', 'horses': [1], 'odds': o, 'p': 0.1} for o in odds]
    return {'legs': legs, 'ev_exact': ev_exact, 'composite': composite, 'union': union,
            'ev_rule': composite * union, 'no_trigami': bh.no_trigami(legs)}


def test_rules():
    ctx = make_context(PRICED)                 # ◎の単勝 4.0倍
    good = portfolio([4.0, 6.0, 30.0], 1.3, 2.2, 0.4)
    assert not bh.rule_buys('R1', good, ctx)   # 合成3.0倍を割る
    assert bh.rule_buys('R2', good, ctx)
    assert bh.rule_buys('R3', good, ctx)
    trigami = portfolio([2.5, 6.0, 30.0], 1.3, 1.9, 0.5)
    assert not bh.rule_buys('R2', trigami, ctx)
    weak_axis = make_context(PRICED, {'1': 6.0, '2': 3.0, '3': 5.0, '4': 8.0,
                                      '5': 15.0, '6': 30.0})
    assert bh.rule_buys('R2', good, weak_axis)
    assert not bh.rule_buys('R3', good, weak_axis)   # ◎が5.0倍超
    tiny = portfolio([10.0, 20.0, 40.0], 1.5, 5.7, 0.04)
    assert not bh.rule_buys('R2', tiny, ctx, floor=True)
    assert bh.rule_buys('R2', tiny, ctx, floor=False)
    assert bh.rule_buys('R0', trigami, ctx)
    assert not bh.rule_buys('R2', None, ctx)


def test_settle_pays_each_hit_leg():
    result = {
        'finishing_order': [{'rank': 1, 'umaban': 1}, {'rank': 2, 'umaban': 3},
                            {'rank': 3, 'umaban': 2}],
        'payouts': {'単勝': [{'combination': [1], 'yen': 420}],
                    'ワイド': [{'combination': [1, 3], 'yen': 380},
                             {'combination': [1, 2], 'yen': 330},
                             {'combination': [2, 3], 'yen': 800}],
                    '3連複': [{'combination': [1, 2, 3], 'yen': 1250}]},
    }
    p = {'legs': [leg('単勝', [1], 4.0), leg('ワイド', [1, 2], 3.2), leg('3連複', [1, 3, 4], 30.0)]}
    assert bh.settle(p, result) == (300, 420 + 330, True)
    assert bh.settle(None, result) == (0, 0, False)
