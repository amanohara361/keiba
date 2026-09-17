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


def test_上書きが空なら市場の分布はそのまま返る():
    # 改訂案の懸念点3：apply_subjective は上書きで削った分を無印馬へ按分するので、
    # 市場側の基準をどちらにするかが問題になる。上書きが空なら按分は起きない。
    assert bet_builder.apply_subjective(MARKET, {}) == MARKET
    # 出走表に無い馬番しか上書きが無い場合も、呼び出し側で除いた結果は空になる。
    only_scratched = {k: v for k, v in {9: 0.9}.items() if k in MARKET}
    assert bet_builder.apply_subjective(MARKET, only_scratched) == MARKET


def test_セット単位の上限は市場のcap倍で頭打ちにし下方向は触らない():
    # 市場のみ10%のセットに対し、主観25%（2.5倍）。
    assert abs(calibration_check.apply_set_cap(0.25, 0.10, 1.5) - 0.15) < 1e-12
    assert calibration_check.apply_set_cap(0.25, 0.10, 3.0) == 0.25   # 上限に届かない
    # 主観が市場より低いときは、CAPが何であれ触らない。
    assert calibration_check.apply_set_cap(0.08, 0.10, 1.3) == 0.08
    # 市場側が出せないときは下げる根拠が無いのでそのまま。
    assert calibration_check.apply_set_cap(0.25, 0.0, 1.3) == 0.25
    assert calibration_check.apply_set_cap(0.25, None, 1.3) == 0.25


def test_市場のみのセット的中率はbet_builderの的中率と同じ関数で出る():
    # 市場のみの的中率は「主観の代わりに market を渡すだけ」で出る、という
    # 改訂案の前提そのものの確認。3連複1点は p_trio と一致する。
    legs = [('3連複', [1, 2, 3])]
    assert abs(calibration_check.set_hit_rate(MARKET, legs)
               - bet_builder.p_trio(MARKET, (1, 2, 3))) < 1e-12
    # 主観を上書きすると同じ組み合わせの的中率が上がり、比が1より大きくなる。
    p = bet_builder.apply_subjective(MARKET, {1: 0.70})
    subjective = calibration_check.set_hit_rate(p, legs)
    market = calibration_check.set_hit_rate(MARKET, legs)
    assert subjective >= market            # 3頭立てなのでどちらも1.0
    assert calibration_check.apply_set_cap(subjective, market, 1.3) <= market * 1.3


class _FakeCandidate:
    """`choose` に渡す最小限の候補（bet_builder.Candidate と同じ属性だけ持つ）。"""

    def __init__(self, name, composite, hit_rate):
        self._name = name
        self.composite = composite
        self.hit_rate = hit_rate

    def label(self):
        return self._name


def test_規律を満たす候補のうち的中率最大が選ばれる():
    # 合成6.0倍×的中率25% = 期待値1.50、合成30.0倍×5% = 1.50。
    # 期待値が同じでも「的中率最大」の規則どおり堅いほうが選ばれる。
    steady = _FakeCandidate('ワイド', 6.0, 0.25)
    longshot = _FakeCandidate('3連複', 30.0, 0.05)
    picked = calibration_check.choose([longshot, steady], lambda c: c.hit_rate)
    assert picked[0] is steady
    # 合成オッズが3.0倍未満の候補は、期待値がいくら高くても対象外。
    cheap = _FakeCandidate('単勝', 2.0, 0.90)
    assert calibration_check.choose([cheap], lambda c: c.hit_rate) is None
    # 規律を満たす候補が1つも無ければ見送り（None）。
    weak = _FakeCandidate('3連複', 10.0, 0.05)   # 期待値0.50
    assert calibration_check.choose([weak], lambda c: c.hit_rate) is None


def test_上限をかけると選ばれる候補が入れ替わることがある():
    # 堅い候補は主観25%だが市場は10%（2.5倍）。穴の候補は主観5%・市場4%（1.25倍）。
    steady = _FakeCandidate('ワイド', 6.0, 0.25)
    longshot = _FakeCandidate('3連複', 40.0, 0.05)
    market = {id(steady): 0.10, id(longshot): 0.04}

    # 上限なしなら的中率最大の堅い候補（期待値1.50）。
    assert calibration_check.choose(
        [steady, longshot], lambda c: c.hit_rate)[0] is steady

    # 上限1.3倍：堅い候補は13%に削られ期待値0.78で脱落、穴の候補が残る。
    capped = calibration_check.choose(
        [steady, longshot],
        lambda c: calibration_check.apply_set_cap(c.hit_rate, market[id(c)], 1.3))
    assert capped[0] is longshot

    # 上限3.0倍なら誰も頭打ちにならず、選定は元どおり。
    assert calibration_check.choose(
        [steady, longshot],
        lambda c: calibration_check.apply_set_cap(
            c.hit_rate, market[id(c)], 3.0))[0] is steady


def test_買い目セットの比較は券種と馬番の並びに左右されない():
    assert (calibration_check._legs_key([('ワイド', [12, 3])])
            == calibration_check._legs_key([('ワイド', [3, 12])]))
    assert (calibration_check._legs_key([('馬連', [1, 2]), ('ワイド', [3, 4])])
            == calibration_check._legs_key([('ワイド', [4, 3]), ('馬連', [2, 1])]))
    assert (calibration_check._legs_key([('馬連', [1, 2])])
            != calibration_check._legs_key([('ワイド', [1, 2])]))


def test_priced_oddsのlookupは記録に無い組み合わせにNoneを返す():
    lookup = calibration_check.priced_lookup(
        {'ワイド': {'3-12': 3.2}, '3連複': {'3-5-12': 9.1}})

    assert lookup('ワイド', [12, 3]) == 3.2      # 並び順は問わない
    assert lookup('3連複', [12, 5, 3]) == 9.1
    assert lookup('ワイド', [3, 5]) is None      # 値付けできなかった組
    assert lookup('馬連', [3, 12]) is None       # 記録の無い券種


def test_期間の区切りは境界日を含めて振り分ける():
    old, middle, latest = (label for _s, _e, label in calibration_check.PERIODS)

    assert calibration_check.period_of(date(2026, 8, 26)) == old
    assert calibration_check.period_of(calibration_check.WIN_PROB_START) == middle
    assert calibration_check.period_of(date(2026, 9, 15)) == middle
    assert calibration_check.period_of(
        calibration_check.WIN_ODDS_LOGGED_FROM) == latest
