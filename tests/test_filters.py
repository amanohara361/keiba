from datetime import timedelta

import review

SINCE = review.FILTER_SINCE


class FakeRace:
    def __init__(self, org, honmei):
        self.org = org
        self._h = honmei

    def horses_for(self, mark):
        return [self._h] if mark == '◎' and self._h else []


def _check(odds):
    return {'win_odds': {str(k): v for k, v in odds.items()}}


def test_condition_a_central():
    race = FakeRace('jra', 3)
    assert review.prestart_filter(race, _check({1: 1.9, 3: 5.0})) == {'cond': 'A', 'applies': True}
    assert review.prestart_filter(race, _check({1: 2.0, 3: 5.0})) == {'cond': 'A', 'applies': False}


def test_condition_b_local():
    race = FakeRace('nar', 3)
    assert review.prestart_filter(race, _check({1: 2.5, 3: 5.0}))['applies'] is True
    # ◎が1番人気なら当たらない
    assert review.prestart_filter(FakeRace('nar', 1), _check({1: 2.5, 3: 5.0}))['applies'] is False
    # 1番人気が2.0倍未満・3.0倍以上は当たらない
    assert review.prestart_filter(race, _check({1: 1.9, 3: 5.0}))['applies'] is False
    assert review.prestart_filter(race, _check({1: 3.0, 3: 5.0}))['applies'] is False


def test_no_odds_or_no_honmei():
    assert review.prestart_filter(FakeRace('jra', 3), None) is None
    assert review.prestart_filter(FakeRace('jra', None), _check({1: 1.5})) is None


def _entry(org, cond, applies, returned):
    return {'org': org, 'category': review.SKIPPED,
            'prestart_filter': {'cond': cond, 'applies': applies},
            'simple_bets': {'◎単勝': {'staked': 100, 'returned': returned},
                            '◎単勝＋◎複勝': {'staked': 200, 'returned': returned}}}


def test_tracking_splits_after_since_and_proposes():
    before = [_entry('jra', 'A', True, 0)] * 5
    hit = [_entry('jra', 'A', True, 100)] * review.FILTER_REVIEW_AT       # 単複50%
    miss = [_entry('jra', 'A', False, 200)] * 10                           # 単複100%
    h = [{'date': SINCE - timedelta(days=1), 'entries': before},
         {'date': SINCE, 'entries': hit + miss}]
    t = review.filter_tracking(h)['A']
    assert t['after_hit']['races'] == review.FILTER_REVIEW_AT
    assert t['all_hit']['races'] == review.FILTER_REVIEW_AT + 5
    assert abs(t['after_hit']['◎単勝＋◎複勝'] - 0.5) < 1e-9
    assert t['ready'] and t['propose']
    assert '承認にかける' in review.mail_filter_lines({'A': t})[0]


def test_tracking_not_ready_below_count():
    h = [{'date': SINCE, 'entries': [_entry('jra', 'A', True, 0)] * 3}]
    t = review.filter_tracking(h)['A']
    assert not t['ready'] and not t['propose']
    assert '0R' in '\n'.join(review.render_filters({'B': review.filter_tracking([])['B']}))
