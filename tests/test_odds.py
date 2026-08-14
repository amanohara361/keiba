#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""odds.py（中央のオッズ取得）を確かめる。ネットワークには接続しない。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import odds  # noqa: E402
from bets import Bet, RaceBets  # noqa: E402


def _fetcher(tables):
    def fetch(race_id, bet_type):
        return {'status': 'middle', 'reason': None, 'official_datetime': '14:00:00',
                'odds': tables.get(bet_type, {})}
    return fetch


def test_fetch_tablesは券種ごとの表と単勝をまとめて返す():
    fetch = _fetcher({
        '単勝': {'07': ['2.9', '3.0', '1']},
        '馬連': {'0711': ['11.2', '11.5', '3']},
        'ワイド': {'0714': ['9.0', '9.4', '4']},
    })
    tables, meta = odds.fetch_tables('202601020811', ['馬連', 'ワイド'], fetch)
    assert set(tables) == {'単勝', '馬連', 'ワイド'}
    assert odds.odds_for(tables['馬連'], [7, 11], '馬連') == 11.2
    assert meta['status'] == 'middle'
    assert meta['errors'] == []


def test_fetch_tablesは任意の組み合わせを問い合わせられる():
    """買い目（race.bets）を先に決めなくても、券種の表さえあれば照会できる。

    bet_builder が候補の組み合わせを試すのに使う経路。
    """
    fetch = _fetcher({
        '単勝': {},
        '馬連': {'0209': ['15.0', '15.4', '6']},
    })
    tables, _ = odds.fetch_tables('202601020811', ['馬連'], fetch)
    assert odds.odds_for(tables['馬連'], [9, 2], '馬連') == 15.0
    assert odds.odds_for(tables['馬連'], [2, 3], '馬連') is None


def test_collectは従来通りraceのbetsに絞って返す():
    race = RaceBets(
        race_id='202601020811', name='テスト', start_time='15:25',
        marks=[], bets=[Bet('馬連', [7, 11])], subjective_hit_rate=0.3)
    fetch = _fetcher({
        '単勝': {'07': ['2.9', '3.0', '1']},
        '馬連': {'0711': ['11.2', '11.5', '3']},
    })
    bet_odds, win_table, meta = odds.collect(race, fetcher=fetch)
    assert bet_odds == [11.2]
    assert win_table[7] == (2.9, 1)
    assert meta['status'] == 'middle'
