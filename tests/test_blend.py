#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主観を市場に混ぜる勝率（bet_builder.blend_subjective / model_win_probabilities）。"""

import pytest

import bet_builder
from bet_builder import blend_subjective, model_win_probabilities

MARKET = {1: 0.5, 2: 0.3, 3: 0.2}


class _Race:
    def __init__(self, org, wp, marks=()):
        self.org = org
        self.win_probabilities = wp
        self.marks = [{'mark': m, 'umaban': u} for m, u in marks]

    def horses_for(self, mark):
        return [m['umaban'] for m in self.marks if m['mark'] == mark]

    def mark_of(self, umaban):
        for m in self.marks:
            if m['umaban'] == umaban:
                return m['mark']
        return None


def test_強さ0なら市場と一致する():
    assert blend_subjective(MARKET, {1: 0.2, 3: 0.6}, 0.0) == pytest.approx(MARKET)
    assert blend_subjective(MARKET, {}, 0.7) == pytest.approx(MARKET)


def test_強さ1で上下とも許すと主観比がそのまま反映され合計1():
    out = blend_subjective(MARKET, {3: 0.4}, 1.0, raise_only=False)
    assert sum(out.values()) == pytest.approx(1.0)
    assert out[3] / out[1] == pytest.approx(0.4 / 0.5)
    assert out[1] / out[2] == pytest.approx(0.5 / 0.3)


def test_上げだけなら下げた馬は市場の比のまま残る():
    out = blend_subjective(MARKET, {1: 0.3, 3: 0.4}, 1.0, raise_only=True)
    assert sum(out.values()) == pytest.approx(1.0)
    # 1番は市場より下げた→市場のまま、2番も無記入で市場のまま。比は市場と同じ
    assert out[1] / out[2] == pytest.approx(0.5 / 0.3)
    assert out[3] > MARKET[3]
    both = blend_subjective(MARKET, {1: 0.3, 3: 0.4}, 1.0, raise_only=False)
    assert both[1] / both[2] == pytest.approx(0.3 / 0.3)


def test_全頭書いて合計が0_9でも合計1になる():
    out = blend_subjective(MARKET, {1: 0.4, 2: 0.3, 3: 0.2}, 1.0, raise_only=False)
    assert sum(out.values()) == pytest.approx(1.0)
    assert out[1] == pytest.approx(0.4 / 0.9)


def test_marketに無い馬番や0以下は無視する():
    out = blend_subjective(MARKET, {9: 0.5, 2: 0.0, 3: -0.1}, 1.0)
    assert out == pytest.approx(MARKET)
    assert set(out) == set(MARKET)
    assert blend_subjective({}, {1: 0.5}, 1.0) == {}


def test_model_win_probabilitiesは_orgで強さを切り替える():
    odds = {1: 2.0, 2: 3.0, 3: 5.0}
    market = bet_builder.market_win_probabilities(odds)
    jra = _Race('jra', {3: 0.4}, [('◎', 3)])
    nar = _Race('nar', {3: 0.4}, [('◎', 3)])
    assert model_win_probabilities(nar, odds) == pytest.approx(market)
    out = model_win_probabilities(jra, odds)
    full = blend_subjective(market, {3: 0.4}, 1.0)
    assert market[3] < out[3] < full[3]
    assert sum(out.values()) == pytest.approx(1.0)
    assert model_win_probabilities(jra, {}) == {}
    unknown = _Race('xx', {3: 0.4})
    assert model_win_probabilities(unknown, odds) == pytest.approx(out)


def test_馬体重の大幅増減の馬は上げを取り消す():
    odds = {1: 2.0, 2: 3.0, 3: 5.0}
    market = bet_builder.market_win_probabilities(odds)
    race = _Race('jra', {3: 0.4, 1: 0.3})
    out = model_win_probabilities(race, odds, withdrawn={3})
    expected = blend_subjective(market, {1: 0.3}, 0.5)
    assert out == pytest.approx(expected)
    # 下げていた馬は取り消さない
    out2 = model_win_probabilities(race, odds, withdrawn={1})
    assert out2 == pytest.approx(blend_subjective(market, {3: 0.4}, 0.5))


def test_検算メール用の1行():
    odds = {1: 2.0, 2: 3.0, 3: 5.0}
    jra = _Race('jra', {3: 0.4}, [('◎', 3), ('○', 1)])
    line = bet_builder.blend_line(jra, odds)
    assert line.startswith('混ぜた勝率（市場→混ぜた後）：◎3 ')
    assert '→' in line and '○1' not in line
    assert bet_builder.blend_line(_Race('nar', {3: 0.4}), odds) == \
        '地方は市場の勝率で評価（主観は記録のみ）'
    assert bet_builder.blend_line(_Race('jra', {}), odds) is None
