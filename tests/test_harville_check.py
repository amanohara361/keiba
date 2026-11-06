#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Harvilleの較正チェック（harville_check.py）の計算を確かめる。

勝率は架空のもの。ネットワークには接続しない。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bet_builder  # noqa: E402
import harville_check as hc  # noqa: E402


P = {1: 0.40, 2: 0.25, 3: 0.20, 4: 0.10, 5: 0.05}


def test_harville_matches_bet_builder():
    """λ=(1, 1) の3連複確率は bet_builder.p_trio と一致する。"""
    trios = hc.trio_probabilities(P, hc.HARVILLE)
    for trio, prob in trios.items():
        assert abs(prob - bet_builder.p_trio(P, tuple(trio))) < 1e-12


def test_trio_probabilities_sum_to_one():
    for lambdas in (hc.HARVILLE, hc.HENERY):
        assert abs(sum(hc.trio_probabilities(P, lambdas).values()) - 1.0) < 1e-9


def test_henery_discounts_favorite_trio():
    """Henery補正は人気馬3頭の組の確率を下げ、人気薄を含む組を上げる。"""
    hv = hc.trio_probabilities(P, hc.HARVILLE)
    hn = hc.trio_probabilities(P, hc.HENERY)
    assert hn[frozenset((1, 2, 3))] < hv[frozenset((1, 2, 3))]
    assert hn[frozenset((3, 4, 5))] > hv[frozenset((3, 4, 5))]


def test_running_style():
    assert hc.running_style([1, 1], 12) == '前'
    assert hc.running_style([6, 5], 12) == '中'
    assert hc.running_style([12, 12], 12) == '後'
    assert hc.running_style([], 12) is None


def test_matched_boost_aligns_mean():
    """倍率をかけた後の予測の平均頭数が実測と一致する。"""
    races = [
        {'odds': {1: 2.0, 2: 4.0, 3: 6.0, 4: 10.0, 5: 20.0},
         'top3': frozenset((1, 4, 5)),
         'style': {1: '前', 2: '後', 3: '後', 4: '前', 5: '前'}},
        {'odds': {1: 3.0, 2: 3.5, 3: 5.0, 4: 8.0, 5: 15.0},
         'top3': frozenset((2, 3, 4)),
         'style': {1: '前', 2: '後', 3: '中', 4: '前', 5: '後'}},
    ]
    k, pred, actual = hc.matched_boost(races, '前', hc.HARVILLE)
    mean = sum(n * v for n, v in enumerate(pred))
    assert abs(mean - sum(n * v for n, v in enumerate(actual))) < 1e-3
    assert k > 1.0
