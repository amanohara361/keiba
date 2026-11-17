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
    assert t['checkpoint']['reached'] is None and t['checkpoint']['next'][0] == 100


def test_no_verdict_at_four_weeks_even_if_roi_low():
    """4週・回収率80%未満でも判定しない（2026-11-17 決定ログ。節目は◎の全レース数で決める）。"""
    h = _history([(SINCE + timedelta(days=4), [_entry(200, 100), _entry(200, 0)])])
    t = review.fixed_form_tracking(h, date(2026, 11, 30))
    assert t['roi'] < 0.8 and t['checkpoint']['reached'] is None
    text = '\n'.join(review.render_fixed_form(t))
    assert '見直しどき' not in text and '次の節目は 100R' in text
    mail = review.render_mail(review.summarize([]), (SINCE, SINCE), 'x.md', fixed_form=t)
    assert '節目に届きました' not in mail


def _many(n, hit_every):
    """◎の全レース n 本。hit_every 本に1本◎が勝つ（オッズ2.0倍・2頭立てなので見込みは0.667勝/R）。"""
    return [_entry(200, 0, hit_win=(i % hit_every == 0)) for i in range(n)]


def test_checkpoint_100_below_floor_alerts_once():
    # 100R中◎50勝（見込み66.7勝）→ z は大きく負 → 100Rの節目で見直し
    h = _history([(SINCE + timedelta(days=5), _many(60, 2)), (SINCE + timedelta(days=12), _many(40, 2))])
    t = review.fixed_form_tracking(h, SINCE + timedelta(days=13))
    cp = t['checkpoint']
    assert cp['reached']['races'] == 100 and cp['new'] and cp['below']
    mail = review.render_mail(review.summarize([]), (SINCE, SINCE), 'x.md', fixed_form=t)
    assert '100R の節目に届きました' in mail and 'bet_builder）へは戻さない' in mail
    # 翌週は同じ節目のままなので、メールの知らせは出さない
    later = review.fixed_form_tracking(h, SINCE + timedelta(days=20))
    assert not later['checkpoint']['new']
    assert '節目に届きました' not in review.render_mail(review.summarize([]), (SINCE, SINCE), 'x.md', fixed_form=later)


def test_checkpoint_200_above_floor_continues():
    # 毎回◎が勝つ → z は大きく正 → 続ける
    h = _history([(SINCE + timedelta(days=5), _many(200, 1))])
    t = review.fixed_form_tracking(h, SINCE + timedelta(days=6))
    cp = t['checkpoint']
    assert cp['reached']['races'] == 200 and not cp['below'] and cp['next'] is None
    assert '続ける' in '\n'.join(review.render_fixed_form(t))


def _band_entry(odds, won, win_ret, place_ret):
    rows = [{'group': '◎', 'ninki': 1, 'rank': 1 if won else 2, 'odds': odds},
            {'group': '', 'ninki': 2, 'rank': 2 if won else 1, 'odds': 3.0}]
    return {'org': 'jra', 'category': review.SKIPPED, 'staked': 0, 'returned': 0, 'blocked': False,
            'simple_bets': {'◎単勝': {'staked': 100, 'returned': win_ret},
                            '◎複勝': {'staked': 100, 'returned': place_ret}},
            'horse_rows': rows}


def test_honmei_odds_bands_split_and_period():
    h = _history([
        (SINCE - timedelta(days=1), [_band_entry(2.0, True, 200, 110)]),     # 採用前・2.5倍未満
        (SINCE + timedelta(days=4), [_band_entry(2.5, True, 250, 120),       # 境目の2.5倍は 2.5〜7倍
                                     _band_entry(8.0, False, 0, 0),
                                     dict(_band_entry(4.0, True, 400, 150), org='nar')]),  # 地方は数えない
    ])
    end = SINCE + timedelta(days=5)
    total = review.honmei_odds_bands(h, end)
    after = review.honmei_odds_bands(h, end, review.FIXED_FORM_SINCE)
    assert total['2.5倍未満']['races'] == 1 and after['2.5倍未満']['races'] == 0
    mid = after['2.5〜7倍']
    assert (mid['races'], mid['wins'], mid['win_ret'], mid['place_ret']) == (1, 1, 250, 120)
    assert abs(mid['expected'] - (1 / 2.5) / (1 / 2.5 + 1 / 3.0)) < 1e-9
    assert after['7倍以上']['races'] == 1
    text = '\n'.join(review.render_honmei_bands({'bands_after': after, 'bands_total': total}))
    assert '| 2.5〜7倍 | 1R 勝ち1/0.5・単250%・複120% |' in text


def test_bands_skip_races_without_full_odds():
    e = _band_entry(3.0, True, 300, 130)
    e['horse_rows'][1]['odds'] = None
    assert review.honmei_odds_bands(_history([(SINCE, [e])]), SINCE)['2.5〜7倍']['races'] == 0


def test_skip_reason_tracking_splits_races():
    skip = dict(_band_entry(3.0, False, 0, 0), skip_reason='混戦')
    buy = dict(_band_entry(3.0, True, 300, 130), simple_bets={
        '◎単勝': {'staked': 100, 'returned': 300}, '◎複勝': {'staked': 100, 'returned': 130},
        '◎単勝＋◎複勝': {'staked': 200, 'returned': 430}})
    skip['simple_bets']['◎単勝＋◎複勝'] = {'staked': 200, 'returned': 0}
    old = dict(skip)  # 採用前は数えない
    h = _history([(SINCE - timedelta(days=1), [old]), (SINCE + timedelta(days=4), [skip, buy])])
    t = review.skip_reason_tracking(h, SINCE + timedelta(days=5))
    assert (t['見送り']['races'], t['見送り']['wins'], t['見送り']['returned']) == (1, 0, 0)
    assert (t['買う']['races'], t['買う']['wins'], t['買う']['returned']) == (1, 1, 430)
    text = '\n'.join(review.render_skip_reason({'skip_reason': t}))
    assert '| 買う | 1R | 1/0.5 | 215% |' in text
