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


DB_PAGE = '''
<table class="race_table_01 nk_tb_common">
<tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th><th>単勝</th><th>人気</th></tr>
<tr><td>1</td><td>1</td><td>1</td><td>カッタウマ</td><td>61.8</td><td>12</td></tr>
<tr><td>2</td><td>3</td><td>4</td><td>ニバンウマ</td><td>3.1</td><td>1</td></tr>
<tr><td>中</td><td>2</td><td>2</td><td>トマッタウマ</td><td>23.8</td><td>8</td></tr>
<tr><td>除</td><td>5</td><td>9</td><td>デナカッタウマ</td><td></td><td></td></tr>
</table>
'''

NK_ROWS = '''
<tr class="HorseList"><td class="Result_Num"><div class="Rank">1</div></td>
<td class="Num Txt_C"><div>1</div></td></tr>
<tr class="HorseList"><td class="Result_Num"><div class="Rank">中止</div></td>
<td class="Num Waku2"><div>2</div></td><td class="Num Txt_C"><div>2</div></td>
<td><span class="HorseNameSpan">トマッタウマ</span></td>
<td class="Odds Txt_R"><span >23.8</span></td></tr>
<tr class="HorseList"><td class="Result_Num"><div class="Rank">除外</div></td>
<td class="Num Txt_C"><div>9</div></td><td class="Odds Txt_R"><span>---.-</span></td></tr>
'''


def test_db_の結果表から着順の付かなかった馬を別に読む():
    """2026-10-20：中止の馬を落とすと「その馬がいなかったレース」として数えてしまう。"""
    assert [h['umaban'] for h in results.parse_finishing_order(DB_PAGE)] == [1, 4]
    assert results.parse_non_finishers(DB_PAGE) == [
        {'umaban': 2, 'status': '中止', 'name': 'トマッタウマ', 'odds': 23.8},
        {'umaban': 9, 'status': '除外', 'name': 'デナカッタウマ'},
    ]


def test_race_netkeiba_の結果ページからも同じ形で読む():
    assert results._nk_non_finishers(NK_ROWS) == [
        {'umaban': 2, 'status': '中止', 'name': 'トマッタウマ', 'odds': 23.8},
        {'umaban': 9, 'status': '除外', 'name': ''},
    ]
    assert results._nk_non_finishers(PAGE) == []


def test_fetch_の戻り値に着順の付かなかった馬の欄がある():
    result = results.fetch('202606040611', fetcher=only_race_page)
    assert result['non_finishers'] == []
