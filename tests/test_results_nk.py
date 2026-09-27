#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""race.netkeiba.com の結果ページから着順・払戻を読む経路。ネットワークには接続しない。

フィクスチャは 2026-09-27 に Actions の probe result-raw で取得した実物
（2026-09-20 中山11R オールカマー、1着8番メイショウゲキリン 9人気）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import results  # noqa: E402

PAGE_PATH = os.path.join(os.path.dirname(__file__), 'fixtures', 'nk_result_202606040611.html')
with open(PAGE_PATH, encoding='utf-8') as f:
    PAGE = f.read()


def only_race_page(url):
    """db.netkeiba.com は着順が取れない状態（2026-09-19以降の実情）を再現する。"""
    return '' if 'db.netkeiba.com' in url else PAGE


def test_着順と人気を読む():
    order = results._nk_finishing_order(PAGE, '202606040611')
    assert [h['umaban'] for h in order[:3]] == [8, 1, 4]
    assert order[0]['ninki'] == 9
    assert order[0]['odds'] == 44.5


def test_払戻を読む():
    payouts = results.parse_nk_payouts(PAGE)
    assert payouts['単勝'] == [{'combination': [8], 'yen': 4450}]
    assert [e['yen'] for e in payouts['複勝']] == [820, 340, 200]
    assert payouts['ワイド'] == [
        {'combination': [1, 8], 'yen': 5210},
        {'combination': [4, 8], 'yen': 2320},
        {'combination': [1, 4], 'yen': 620},
    ]
    assert payouts['3連複'] == [{'combination': [1, 4, 8], 'yen': 29440}]
    assert payouts['3連単'] == [{'combination': [8, 1, 4], 'yen': 383690}]


def test_単勝の払戻と確定オッズが一致する():
    """CLAUDE.md の検算：払戻金÷100 が確定オッズと一致する。"""
    order = results._nk_finishing_order(PAGE, '202606040611')
    payouts = results.parse_nk_payouts(PAGE)
    assert payouts['単勝'][0]['yen'] / 100 == order[0]['odds']


def test_dbで取れなければraceのページで結果を返す():
    result = results.fetch('202606040611', fetcher=only_race_page)
    assert result['finishing_order'][0]['umaban'] == 8
    assert results.payout_for(result['payouts'], 'ワイド', [8, 4]) == 2320
    assert results.payout_for(result['payouts'], '3連複', [7, 9, 13]) == 0


def test_どちらのページにも着順が無ければ未確定():
    assert results.fetch('202606040611', fetcher=lambda url: '<html></html>') is None
