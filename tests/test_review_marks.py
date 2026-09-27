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
