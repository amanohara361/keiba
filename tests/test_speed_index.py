#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""speed_index の検証。ネットワーク・data_jra には触れない。"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import speed_index as si  # noqa: E402


def test_parse_time_and_class():
    assert si.parse_time('1:38.7') == 98.7
    assert si.parse_time('58.4') == 58.4
    assert si.parse_time('') is None
    assert si.race_class('2歳未勝利') == '2歳未勝利'
    assert si.race_class('3歳以上1勝クラス') == '1勝'
    assert si.race_class('東スポ杯2歳S') == 'その他'


def _race(rid, date, venue, dist, cls, times, kg=56.0):
    return {'race_id': rid, 'date': date, 'venue': venue, 'surface': '芝', 'distance': dist, 'class': cls,
            'runs': [{'name': f'{rid}-{i}', 'umaban': i + 1, 'finish': i + 1, 'time': t, 'kg': kg,
                      'odds': 2.0, 'pop': i + 1} for i, t in enumerate(times)]}


def test_day_variant_is_removed():
    # 同じ1勝クラス・同じコース。2日目は馬場が1%遅い（全レースが1%遅い）。
    races = []
    for n, date in enumerate(('20261101', '20261102')):
        slow = 1.01 if n else 1.0
        for k in range(4):
            base = 96.0 * slow
            races.append(_race(f'{date}{k}', date, '東京', 1600, '1勝', [base, base + 0.2, base + 0.4, base + 1.0]))
    si.index_runs(races)
    winners = [r['runs'][0]['index'] for r in races]
    assert max(winners) - min(winners) < 0.5  # 馬場差を除けば同じ指数
    # 1〜3着の平均が80付近
    top3 = sum(run['index'] for run in races[0]['runs'][:3]) / 3
    assert abs(top3 - 80) < 0.5
    # 1秒差 ≒ 10点（1600m）
    r = races[0]['runs']
    assert abs((r[0]['index'] - r[3]['index']) - 1000 * math.log(97.0 / 96.0)) < 0.1  # 指数は0.1点に丸める


def test_few_races_on_course_gives_none():
    races = [_race('a', '20261101', '東京', 1600, '1勝', [96, 96.2, 96.4]),
             _race('b', '20261101', '東京', 2400, '1勝', [146, 146.5, 147])]
    races += [_race(f'c{k}', '20261101', '東京', 1600, '1勝', [96, 96.2, 96.4]) for k in range(3)]
    si.index_runs(races)
    assert races[1]['runs'][0]['index'] is None
    assert races[0]['runs'][0]['index'] is not None
