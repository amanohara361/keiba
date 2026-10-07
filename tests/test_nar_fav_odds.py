#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nar_fav_odds.py の表づくりと集計を、架空の行だけで確かめる。
ネットワークにも data/ にも触らない。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nar_fav_odds as f  # noqa: E402


def _race(venue, ymd, no, results, favs=(1,), odds=None, place=()):
    """results: {馬番: 着順文字列}。1番が1番人気（favs で同率を作れる）。"""
    win, horses = [], []
    for ban, fin in results.items():
        o = (odds or {}).get(ban, 2.5 if ban in favs else 10.0)
        win.append({'競馬場': venue, '競走年月日': ymd, 'レース番号': str(no), '番号1': str(ban),
                    'オッズ': str(o), '人気': '1' if ban in favs else str(ban + 1)})
        horses.append({'競馬場': venue, '競走年月日': ymd, 'レース番号': str(no),
                       '馬番': str(ban), '着順': fin})
    pay = {'競馬場': venue, '競走年月日': ymd, 'レース番号': str(no)}
    for i, (ban, yen) in enumerate(place, 1):
        pay[f'複勝組番{i}'] = str(ban)
        pay[f'複勝払戻金{i}（円）'] = str(yen)
    return win, horses, [pay]


FULL = {b: str(b) for b in range(1, 9)}


def _build(*races):
    w, h, p = [], [], []
    for a, b, c in races:
        w += a; h += b; p += c
    return f.build_rows(w, h, p)


def test_1番人気の着順と自分の複勝払戻だけを拾う():
    rows, skip = _build(_race('大井', '20260901', 1, FULL, odds={1: 1.8},
                              place=[(1, 110), (2, 200), (3, 300)]))
    assert rows == [{'venue': '大井', 'date': '20260901', 'race_no': 1, 'runners': 8,
                     'umaban': 1, 'odds': 1.8, 'finish': 1, 'place_pay': 110}]
    assert not skip


def test_同率1番人気や取消は推測で埋めず除外する():
    scratched = {**FULL, 1: '取消'}
    rows, skip = _build(_race('大井', '20260901', 1, FULL, favs=(1, 2)),
                        _race('大井', '20260901', 2, scratched),
                        _race('大井', '20260901', 3, {1: '1', 2: '2', 3: '3'}))
    assert rows == []
    assert skip == {'1番人気が同率で複数・不明': 1, '1番人気が取消・中止等': 1, '完走5頭未満': 1}


def test_結果がまだ無い先の日付は除外する():
    rows, skip = _build(_race('大井', '20260930', 1, {b: '' for b in FULL}))
    assert rows == [] and skip == {'結果未確定': 1}


def test_集計は2倍の境目で分け市場想定勝率は払戻率割るオッズ():
    rows = [
        {'venue': '大井', 'date': '20260901', 'odds': 1.6, 'finish': 1, 'place_pay': 110},
        {'venue': '大井', 'date': '20260901', 'odds': 2.0, 'finish': 4, 'place_pay': 0},
    ]
    s = f.stats(rows[:1])
    assert s['win'] == 1 and abs(s['market'] - 0.5) < 1e-9 and abs(s['win_roi'] - 1.6) < 1e-9
    text = f.report(rows)
    assert '2倍未満       n=    1' in text and '2倍以上       n=    1' in text


def test_カイ二乗の近似は既知の値に近い():
    # df=14 の上側5%点は 23.68
    assert abs(f.chi2_sf(23.68, 14) - 0.05) < 0.005
