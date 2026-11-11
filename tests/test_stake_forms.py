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
    assert out['fixed'] == {'bets': 2, 'hits': 2, 'staked': 200, 'returned': 580, 'max': 420}
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
