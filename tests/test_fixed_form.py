from datetime import date, timedelta

import review

SINCE = review.FIXED_FORM_SINCE


def _entry(staked, returned, legacy=None, legacy_nf=None, org='jra', hit_win=False):
    rows = [{'group': '◎', 'ninki': 1, 'rank': 1 if hit_win else 2, 'odds': 2.0},
            {'group': '', 'ninki': 2, 'rank': 2 if hit_win else 1, 'odds': 4.0}]
    return {'org': org, 'category': review.SKIPPED, 'staked': staked, 'returned': returned,
            'blocked': False, 'legacy_buy': legacy, 'legacy_buy_no_floor': legacy_nf,
            'simple_bets': {'◎単勝': {'staked': 100, 'returned': 200 if hit_win else 0},
                            '◎単勝＋◎複勝': {'staked': 200, 'returned': 320 if hit_win else 120}},
            'horse_rows': rows}


def _history(entries_by_day):
    return [{'date': d, 'entries': es} for d, es in entries_by_day]


def test_no_races_yet():
    t = review.fixed_form_tracking(_history([(SINCE - timedelta(days=1), [_entry(200, 0)])]),
                                   SINCE + timedelta(days=1))
    assert t == {'races': 0, 'days': 2}
    assert 'まだ無い' in review.render_fixed_form(t)[2]
    assert 'まだ無い' in review.mail_fixed_form_lines(t)[0]


def test_counts_only_central_after_since():
    h = _history([
        (SINCE - timedelta(days=1), [_entry(200, 5000)]),          # 採用前は数えない
        (SINCE + timedelta(days=4), [_entry(200, 320, legacy=True, legacy_nf=True, hit_win=True),
                                     _entry(200, 0, legacy=False, legacy_nf=False),
                                     _entry(200, 900, org='nar')]),  # 地方は数えない
    ])
    t = review.fixed_form_tracking(h, SINCE + timedelta(days=5))
    assert (t['bought'], t['staked'], t['returned']) == (2, 400, 320)
    assert abs(t['roi'] - 0.8) < 1e-9
    assert t['legacy']['買う']['races'] == 1 and t['legacy']['買う']['◎単勝'] == 2.0
    assert t['legacy']['足切りなしなら見送る']['◎単勝＋◎複勝'] == 0.6
    assert t['honmei_after']['races'] == 2 and t['honmei_after']['win']['actual'] == 1
    assert not t['review_due'] and not t['below_floor']


def test_below_floor_after_four_weeks():
    h = _history([(SINCE + timedelta(days=4), [_entry(200, 100), _entry(200, 0)])])
    # 11-03 採用 → 12-01（火）の週次レビューは 11-30 締め＝28日目で判定する
    early = review.fixed_form_tracking(h, SINCE + timedelta(days=review.FIXED_FORM_REVIEW_DAYS - 2))
    assert not early['below_floor']
    late = review.fixed_form_tracking(h, date(2026, 11, 30))
    assert late['review_due'] and late['below_floor']
    assert '見直しどき' in '\n'.join(review.render_fixed_form(late))
    week = review.summarize([])
    mail = review.render_mail(week, (SINCE, SINCE), 'x.md', fixed_form=late)
    assert '中央の固定形の見直しどき' in mail


def test_not_below_floor_when_roi_ok():
    h = _history([(SINCE + timedelta(days=4), [_entry(200, 300)])])
    t = review.fixed_form_tracking(h, SINCE + timedelta(days=60))
    assert t['review_due'] and not t['below_floor']
