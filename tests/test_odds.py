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


# ----------------------------------------------------------------------
# netkeiba が取れないときの JRA 公式への切り替え（2026-11-28）
# 12:07 の検算で netkeiba の要求が全券種 HTTP 400 になり、中央6レースが全部見送りになった。
# ----------------------------------------------------------------------

import jra_official  # noqa: E402

_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


def _musashino():
    """2026-11-14 に取った武蔵野S（202605050311）の JRA 公式・単勝複勝ページ。"""
    with open(os.path.join(_FIX, 'jra_odds_tanpuku_202605050311.html'), 'rb') as f:
        return jra_official.parse_win(f.read().decode('shift_jis', errors='replace'))


def _fetch_400(race_id, bet_type):
    raise odds.OddsError(f'{race_id} の{bet_type}オッズを取得できませんでした: HTTP Error 400: Bad Request')


def test_JRA公式の単勝複勝をnetkeibaと同じ形の表にする():
    parsed = _musashino()
    tables = odds.tables_from_jra_official(parsed)
    win = odds.win_odds_table(tables['単勝'])
    assert len(win) == 16
    assert win[1][0] == 17.1
    # 人気は単勝オッズの低い順。1番人気はいちばん低いオッズの馬
    lowest = min(h['win'] for h in parsed['horses'].values())
    assert [n for n, (o, k) in win.items() if k == 1] == [
        n for n, h in parsed['horses'].items() if h['win'] == lowest]
    assert sorted(k for _, k in win.values())[0] == 1
    # 複勝は下限を使う（netkeiba の表と同じ）
    assert odds.odds_for(tables['複勝'], [1], '複勝') == 4.8


def test_取消の馬は買い目から外れる():
    tables = odds.tables_from_jra_official({'horses': {
        1: {'win': 3.0, 'place_min': 1.2, 'place_max': 1.5},
        2: {'win': None, 'win_text': '取消', 'place_min': None, 'place_max': None},
        3: {'win': 3.0, 'place_min': 1.3, 'place_max': 1.6},
    }})
    win = odds.win_odds_table(tables['単勝'])
    assert set(win) == {1, 3}
    assert win[1][1] == win[3][1] == 1          # 同じオッズは同じ人気
    assert odds.odds_for(tables['単勝'], [2], '単勝') is None
    assert odds.odds_for(tables['複勝'], [2], '複勝') is None


def test_netkeibaが400なら単勝複勝をJRA公式で取り直す():
    tables, meta = odds.fetch_tables('202605050311', ['複勝', '馬連', 'ワイド', '3連複'],
                                     _fetch_400, fallback=lambda race_id: _musashino())
    assert set(tables) == {'単勝', '複勝'}
    assert odds.win_odds_table(tables['単勝'])[1][0] == 17.1
    assert meta['status'] == 'jra_official'
    assert meta['official_datetime'] == '10時38分'
    assert odds.STATUS_LABELS['jra_official'].startswith('発売中')
    # netkeiba の失敗は記録に残す（何が起きたかを後から追えるように）
    assert any('HTTP Error 400' in e for e in meta['errors'])


def test_netkeibaが取れていればJRA公式は叩かない():
    called = []
    fetch = _fetcher({'単勝': {'07': ['2.9', '3.0', '1']}, '複勝': {'07': ['1.3', '1.6', '1']}})
    tables, meta = odds.fetch_tables('202601020811', ['複勝'], fetch,
                                     fallback=lambda r: called.append(r))
    assert called == []
    assert meta['status'] == 'middle'


def test_JRA公式も取れなければ理由を残して検算は続ける():
    def broken(race_id):
        raise jra_official.JraError('開催がオッズのメニューにありません')
    tables, meta = odds.fetch_tables('202605050311', ['複勝'], _fetch_400, fallback=broken)
    assert tables == {}
    assert any('JRA 公式からも取得できませんでした' in e for e in meta['errors'])


def test_差し替えたfetcherでは既定で本物のJRA公式を叩かない(monkeypatch):
    def boom(race_id):
        raise AssertionError('テストが本物の JRA 公式を叩こうとした')
    monkeypatch.setattr(odds, '_jra_official_fetch', boom)
    tables, _ = odds.fetch_tables('202605050311', ['複勝'], _fetch_400)
    assert tables == {}


def test_本物のfetchを渡されたときは既定でJRA公式に切り替える(monkeypatch):
    """check.py は `fetcher or odds.fetch` で本物の fetch を渡してくる。それでも切り替わること。"""
    monkeypatch.setattr(odds, 'fetch', _fetch_400)
    monkeypatch.setattr(odds, '_jra_official_fetch', lambda race_id: _musashino())
    tables, meta = odds.fetch_tables('202605050311', ['複勝'], odds.fetch)
    assert meta['status'] == 'jra_official'
    assert len(tables['単勝']) == 16
