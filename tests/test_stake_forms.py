"""買い目構成の仮想記録（review.stake_forms、決定ログ 2026-11-11）。"""
from datetime import timedelta

import review

SINCE = review.STAKE_FORMS_SINCE


class FakeRace:
    def __init__(self, honmei, probs=None, org='jra'):
        self.org = org
        self._h = honmei
        self.win_probabilities = probs or {}

    def horses_for(self, mark):
        return [self._h] if mark == '◎' and self._h else []


def _result(winner, win_yen, place_yen):
    return {'payouts': {'単勝': [{'combination': [winner], 'yen': win_yen}],
                        '複勝': [{'combination': [winner], 'yen': place_yen}]}}


def _check(odds):
    return {'win_odds': {str(k): v for k, v in odds.items()}}


def test_basic_forms_and_band():
    out = review.stake_forms(FakeRace(3), _result(3, 420, 160), _check({1: 2.0, 3: 4.0, 5: 4.0}))
    # max はレース単位（そのレースの払戻合計）。fixed_form_tracking と同じ物差し
    assert out['fixed'] == {'bets': 2, 'hits': 2, 'staked': 200, 'returned': 580, 'max': 580}
    assert out['win']['returned'] == 420
    assert (out['win2_place1']['staked'], out['win2_place1']['returned']) == (300, 1000)
    assert out['band_win']['returned'] == 420 and out['band_fixed']['returned'] == 580
    # 払戻が同額：1000円÷4.0倍＝250円を賭け、確定4.2倍で1050円
    assert out['even_payout']['staked'] == 250 and out['even_payout']['returned'] == 1050


def test_band_is_half_open_and_needs_check_odds():
    result = _result(3, 500, 200)
    assert 'band_win' not in review.stake_forms(FakeRace(3), result, _check({1: 2.0, 3: 5.0}))
    assert 'band_win' in review.stake_forms(FakeRace(3), result, _check({1: 2.0, 3: 3.0}))
    out = review.stake_forms(FakeRace(3), result, None)
    assert set(out) == {'fixed', 'win', 'win2_place1'}
    assert review.stake_forms(FakeRace(None), result, _check({1: 2.0})) == {}


def test_raised_horses_split_by_honmei():
    # 市場は 1番 0.5・3番 0.25・5番 0.25。主観は 3番 0.40（1.6倍）・5番 0.45（1.8倍）・1番 0.6（1.2倍）
    out = review.stake_forms(FakeRace(3, {1: 0.6, 3: 0.40, 5: 0.45}), _result(5, 450, 170),
                             _check({1: 2.0, 3: 4.0, 5: 4.0}))
    assert out['raised_honmei'] == {'bets': 1, 'hits': 0, 'staked': 100, 'returned': 0, 'max': 0}
    assert out['raised_other'] == {'bets': 1, 'hits': 1, 'staked': 100, 'returned': 450, 'max': 450}


def _entry(org, returned):
    return {'org': org, 'category': review.SKIPPED,
            'stake_forms': {'win': {'bets': 1, 'hits': int(returned > 0), 'staked': 100,
                                    'returned': returned, 'max': returned}}}


def test_tracking_splits_period_and_org():
    h = [{'date': SINCE - timedelta(days=1), 'entries': [_entry('jra', 0)] * 3},
         {'date': SINCE, 'entries': [_entry('jra', 300), _entry('jra', 0), _entry('nar', 900),
                                     {'org': 'jra', 'category': review.UNSETTLED}]}]
    t = review.stake_form_tracking(h)
    assert t['after']['races'] == 2 and t['all']['races'] == 5
    assert abs(t['after']['win']['roi'] - 1.5) < 1e-9
    assert t['after']['win']['roi_drop_max'] == 0
    assert abs(t['all']['win']['roi'] - 0.6) < 1e-9
    assert t['after']['band_win']['roi'] is None
    text = '\n'.join(review.render_stake_forms(t))
    assert '(a) ◎単勝のみ' in text and '150%' in text and '0R' in text
    assert review.render_stake_forms(None) == []


def test_band_and_even_payout_follow_check_odds_not_final():
    # 検算時は4.0倍（帯の中）、確定は6.0倍（帯の外）。判定は検算側で決まり、精算は確定払戻
    out = review.stake_forms(FakeRace(3), _result(3, 600, 220), _check({1: 2.0, 3: 4.0}))
    assert out['band_win']['returned'] == 600
    assert out['even_payout']['staked'] == 250 and out['even_payout']['returned'] == 1500
    # 逆：検算時は6.0倍（帯の外）、確定は4.0倍（帯の中）なら買わない
    out = review.stake_forms(FakeRace(3), _result(3, 400, 160), _check({1: 2.0, 3: 6.0}))
    assert 'band_win' not in out and 'band_fixed' not in out


def test_honmei_missing_from_result_counts_as_loss():
    # ◎（3番）が払戻に居ない（取消など）。返還ではなく外れとして数える（results.settle と同じ）
    out = review.stake_forms(FakeRace(3), _result(5, 450, 170), _check({3: 4.0, 5: 4.0}))
    assert out['fixed'] == {'bets': 2, 'hits': 0, 'staked': 200, 'returned': 0, 'max': 0}


def test_drop_max_is_per_race_across_races():
    # レースA：単勝420・複勝160（合計580）、レースB：単勝0・複勝130、レースC：外れ
    rows = [review.stake_forms(FakeRace(3), _result(3, 420, 160), None),
            review.stake_forms(FakeRace(3), {'payouts': {'単勝': [{'combination': [1], 'yen': 300}],
                                                         '複勝': [{'combination': [3], 'yen': 130}]}}, None),
            review.stake_forms(FakeRace(3), _result(9, 900, 300), None)]
    h = [{'date': SINCE, 'entries': [{'org': 'jra', 'category': review.SKIPPED, 'stake_forms': r}
                                     for r in rows]}]
    t = review.stake_form_tracking(h)['after']['fixed']
    assert t['races'] == 3 and t['bets'] == 6 and t['staked'] == 600 and t['returned'] == 710
    # 最大の1レース（580）を除く：(710-580)/600。1点単位（420）で除くと (710-420)/600 になり食い違う
    assert abs(t['roi_drop_max'] - 130 / 600) < 1e-9


def test_failures_do_not_stop_review(caplog):
    class Broken(FakeRace):
        race_id = 'x'

        def horses_for(self, mark):
            raise RuntimeError('boom')
    assert review._safe_stake_forms(Broken(3), _result(3, 400, 160), None) is None
    assert review.safe_stake_form_tracking([{'date': SINCE, 'entries': [{}]}]) is None
    assert review.render_stake_forms(None) == []
