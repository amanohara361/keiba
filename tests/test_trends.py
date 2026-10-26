#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""要因別の過去データ（trends.py）。ネットワークには接続しない。

フィクスチャは 2026-10-26 に db.netkeiba.com から取った実物の抜粋
（2025-11-02 東京11R 天皇賞(秋)、マスカレードボールの競走成績、同日のレース一覧）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trends  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def read(name):
    with open(os.path.join(FIXTURES, name), encoding='utf-8') as f:
        return f.read()


def test_場コードで中央地方海外を分ける():
    assert trends.venue_kind('202505041111') == 'jra'
    assert trends.venue_kind('202544100511') == 'nar'
    assert trends.venue_kind('2026A0010105') == 'overseas'
    assert trends.venue_kind('2025H1010108') == 'overseas'


def test_中央の平地GIだけ拾う():
    races = trends.parse_day_races(read('db_race_list_20251102.html'))
    g1 = [(rid, t) for rid, t in races if trends.is_jra_g1(rid, t)]
    assert g1 == [('202505041111', '天皇賞(秋)(GI)')]
    assert not trends.is_jra_g1('202506050911', '中山大障害(J.GI)')


def test_出走馬の人気と着順を読む():
    runners = trends.parse_race_runners(read('db_race_table_202505041111.html'))
    assert len(runners) == 14
    assert runners[0] == {'horse_id': '2022105519', 'name': 'マスカレードボール', 'ninki': 1, 'rank': 1}
    assert runners[7]['name'] == 'タスティエーラ' and runners[7]['ninki'] == 2


def test_前走を競走成績から引く():
    page = read('db_horse_result_2022105519.html')
    # 2026年キングジョージの前走は香港のQE2世C（海外）
    prev = trends.parse_previous_race(page, '2026A0010105')
    assert prev['kind'] == 'overseas'
    assert prev['date'] == '2026/04/26'
    assert prev['days'] == 90
    # 2025年天皇賞(秋)の前走は東京優駿（中央）
    prev = trends.parse_previous_race(page, '202505041111')
    assert prev['kind'] == 'jra' and prev['race_name'] == '東京優駿(GI)'
    assert prev['days'] == 154
    # 載っていないレースは None
    assert trends.parse_previous_race(page, '201905041111') is None


def _race(name, runners):
    return {'race_id': 'x', 'date': '20251102', 'race_name': name, 'runners': runners}


def _runner(ninki, rank, kind, days=70):
    return {'horse_id': '0', 'name': f'{ninki}番人気', 'ninki': ninki, 'rank': rank,
            'prev': {'kind': kind, 'race_name': '前走', 'days': days}}


def test_人気をそろえた倍率():
    # 1番人気は2頭中1勝（基準の勝率0.5）。海外帰りの1番人気が負け、中央組の1番人気が勝つ。
    races = [
        _race('A', [_runner(1, 1, 'jra'), _runner(2, 2, 'jra')]),
        _race('B', [_runner(1, 3, 'overseas'), _runner(2, 1, 'jra')]),
    ]
    rows = trends.flatten(races)
    base = trends.baseline(rows)
    assert base['1']['win'] == 0.5

    overseas = trends.score([r for r in rows if r['prev_kind'] == 'overseas'], base)
    assert overseas['record'] == [0, 0, 1, 0]
    assert overseas['win_ratio'] == 0.0
    assert overseas['top3_ratio'] == 1.0   # 1番人気の3着内率は1.0、実際も3着内


def test_集計表を作る():
    races = [_race('天皇賞(秋)', [_runner(1, 1, 'jra'), _runner(2, 5, 'overseas', days=100)])]
    text = trends.summary(races)
    assert '## 前走海外 × レース' in text
    assert '| 2025 | 天皇賞(秋) | 2番人気 | 2 | 5 | 前走 | 13〜25週 |' in text
