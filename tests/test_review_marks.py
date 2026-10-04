#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""週次レビューの「印と人気上位の比較」（案A・案D、2026-09-27〜）。ネットワークには接続しない。

着順は架空のもの。人気は着順と独立に指定する。
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import review  # noqa: E402
from bets import RaceBets  # noqa: E402


def make_race(marks, partners=(), org='jra'):
    return RaceBets(
        race_id='202601020811', name='テストステークス', start_time='15:25',
        marks=[{'mark': m, 'umaban': u} for m, u in marks], bets=[],
        org=org, partners=[{'umaban': u, 'reason': 'テスト'} for u in partners],
    )


def make_result(order):
    """order = [(着順, 馬番, 人気), ...]"""
    return {
        'race_id': '202601020811',
        'finishing_order': [
            {'rank': r, 'umaban': u, 'name': f'ウマ{u}', 'ninki': n} for r, u, n in order
        ],
        'payouts': {},
    }


# 1着1番(1人気) 2着2番(2人気) 3着7番(6人気) 4着3番(3人気) …
ORDER = [(1, 1, 1), (2, 2, 2), (3, 7, 6), (4, 3, 3), (5, 5, 4), (6, 6, 5)]


def test_印と同じ頭数の人気上位と3着以内の拾い方を比べる():
    race = make_race([('◎', 5), ('○', 2), ('▲', 7)])
    c = review.mark_vs_popularity(race, make_result(ORDER))
    assert c['top3'] == 3
    assert c['marked_top3'] == 2          # 2番と7番
    assert c['popular_top3'] == 2         # 人気上位3頭＝1・2・3番のうち1・2番


def test_partnersに残した人気馬は消したと数えない():
    race = make_race([('◎', 5), ('○', 6)], partners=[3])
    c = review.mark_vs_popularity(race, make_result(ORDER))
    # 1〜3番人気は1・2・3番。3番は partners に残したので、消したのは1・2番
    assert c['popular_cut'] == 2
    assert c['popular_cut_top3'] == 2


def test_地方で1番人気から本命を外したレースを数える():
    race = make_race([('◎', 5), ('○', 1)], org='nar')
    c = review.mark_vs_popularity(race, make_result(ORDER))
    assert c['nar_favorite_off'] == {'favorite_won': True, 'honmei_won': False}


def test_中央や本命が1番人気なら数えない():
    assert review.mark_vs_popularity(
        make_race([('◎', 5)], org='jra'), make_result(ORDER))['nar_favorite_off'] is None
    assert review.mark_vs_popularity(
        make_race([('◎', 1)], org='nar'), make_result(ORDER))['nar_favorite_off'] is None


def test_人気が欠けていれば比べない():
    result = make_result(ORDER)
    del result['finishing_order'][0]['ninki']
    assert review.mark_vs_popularity(make_race([('◎', 5)]), result) is None


def test_集計とレビューに比較の表が出る():
    race = make_race([('◎', 5), ('○', 1)], org='nar')
    entry = review.review_race(race, make_result(ORDER), None)
    summary = review.summarize([entry])
    assert summary['top3_total'] == 3
    assert summary['top3_marked'] == 1
    assert summary['top3_popular'] == 2
    assert summary['nar_favorite_off'] == 1
    assert summary['nar_favorite_off_favorite_won'] == 1   # 1番人気（○1番）が勝った
    assert summary['nar_favorite_off_honmei_won'] == 0
    text = review.render([], summary, summary, (date(2026, 9, 28), date(2026, 10, 4)))
    assert '印と人気上位の比較' in text
    assert '1/3（33%）' in text


def test_比較できるレースが無くても表は崩れない():
    summary = review.summarize([])
    text = review.render([], summary, summary, (date(2026, 9, 28), date(2026, 10, 4)))
    assert '印と人気上位の比較' in text


def test_中央と地方を分けて集計する():
    jra = review.review_race(make_race([('◎', 1)], org='jra'), make_result(ORDER), None)
    nar = review.review_race(make_race([('◎', 5), ('○', 1)], org='nar'), make_result(ORDER), None)
    by_org = review.summarize_by_org([jra, nar])
    assert by_org['jra']['settled'] == 1 and by_org['nar']['settled'] == 1
    assert by_org['jra']['honmei_win'] == 1          # ◎1番が1着
    assert by_org['nar']['honmei_win'] == 0
    assert by_org['nar']['favorite_win'] == 1        # 1番人気は勝っている
    text = review.render([], review.summarize([jra, nar]), review.summarize([jra, nar]),
                         (date(2026, 9, 28), date(2026, 10, 4)),
                         week_by_org=by_org, total_by_org=by_org)
    assert '## 中央・地方別' in text
    assert '| ◎勝率 | 1/1（100%） | 1/1（100%） | 0/1（0%） | 0/1（0%） |' in text


def test_片方が0件でも表は崩れない():
    jra = review.review_race(make_race([('◎', 1)], org='jra'), make_result(ORDER), None)
    by_org = review.summarize_by_org([jra])
    assert by_org['nar']['settled'] == 0
    lines = review.render_by_org(by_org, by_org)
    assert any(l.startswith('| ◎勝率 |') and '—' in l for l in lines)
    mail = review.render_mail(review.summarize([jra]), (date(2026, 9, 28), date(2026, 10, 4)),
                              'x.md', week_by_org=by_org)
    assert '中央 1R' in mail and '地方 ' not in mail.split('── レース別')[0].split('印の精度')[1]


def wide_result(order, wide):
    r = make_result(order)
    r['payouts'] = {'ワイド': [{'combination': list(c), 'yen': y} for c, y in wide]}
    return r


def test_仮想の本命と中穴候補のワイドを精算する():
    race = make_race([('◎', 1), ('○', 2)], partners=[7, 6])
    result = wide_result(ORDER, [((1, 2), 300), ((1, 7), 1500), ((2, 7), 2000)])
    v = review.virtual_axis_partner_wide(race, result)
    assert v == {'bets': 2, 'hits': 1, 'staked': 200, 'returned': 1500}


def test_中穴候補が無ければ仮想ワイドは組まない():
    assert review.virtual_axis_partner_wide(make_race([('◎', 1)]), make_result(ORDER)) is None
    assert review.virtual_axis_partner_wide(make_race([('○', 1)], partners=[7]), make_result(ORDER)) is None


def test_仮想ワイドを集計してレビューとメールに出す():
    race = make_race([('◎', 1), ('○', 2)], partners=[7, 6])
    entry = review.review_race(race, wide_result(ORDER, [((1, 7), 1500)]), None)
    s = review.summarize([entry])
    assert (s['vwide_races'], s['vwide_bets'], s['vwide_hits']) == (1, 2, 1)
    assert s['vwide_returned'] == 1500
    text = review.render([], s, s, (date(2026, 9, 28), date(2026, 10, 4)), wide_since_summary=s)
    assert '仮想：◎×中穴候補（partners）のワイド' in text
    assert '| 回収率 | 750% | 750% | 750% |' in text
    mail = review.render_mail(s, (date(2026, 9, 28), date(2026, 10, 4)), 'x.md',
                              week_by_org=review.summarize_by_org([entry]))
    assert '仮想：◎×中穴候補のワイド 2点 的中1 回収率750%' in mail
