#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""馬場別・相対馬体重の集計（weight_going.py）を架空のレースで確かめる。

実在レースの数字は使わない（test_calibration_check.py と同じ理由）。
"""

import json

import weight_going


def _race(rid, state, horses, track='芝'):
    return {'race_id': rid, '馬場': track, '馬場状態': state,
            'horses': [{'着順': fin, '単勝': str(o), '馬体重': f'{w}(0)'}
                       for fin, o, w in horses]}


def test_ダートと取消と5頭未満は対象から外れる(tmp_path):
    ok = _race('1', '良', [(1, 2.0, 500), (2, 3.0, 480), (3, 5.0, 460),
                           (4, 9.0, 440), (5, 20.0, 420)])
    dirt = _race('2', '良', [(i, 3.0, 480) for i in range(1, 6)], track='ダ')
    short = _race('3', '重', [(1, 2.0, 500), (2, 3.0, 480), (3, 5.0, 460), (4, 9.0, 440)])
    scratched = _race('4', '不良', [(1, 2.0, 500), (2, 3.0, 480), (3, 5.0, 460),
                                    (4, 9.0, 440), (5, 20.0, 420)])
    scratched['horses'][4]['着順'] = '取消'
    p = tmp_path / '20260101_東京.json'
    p.write_text(json.dumps([ok, dirt, short, scratched], ensure_ascii=False), encoding='utf-8')

    rows = weight_going.load_rows([str(p)])

    assert {r[0] for r in rows} == {'1'}
    assert [round(r[2]) for r in rows] == [40, 20, 0, -20, -40]  # 平均460からの差


def test_重い馬が人気より下に来るほど傾きは負になる():
    # 同じオッズ帯で、重い馬が下位・軽い馬が上位に来た2レース
    rs = weight_going.residuals([
        ('a', '重・不良', 30, 5, 1.0), ('a', '重・不良', -30, 5, 0.0),
        ('b', '重・不良', 20, 5, 0.75), ('b', '重・不良', -20, 5, 0.25),
    ])
    assert weight_going.slope(rs) < 0


def test_馬場区分は稍重以外の道悪を重と不良にまとめる():
    assert weight_going.going_class('良') == '良'
    assert weight_going.going_class('稍重') == '稍重'
    assert weight_going.going_class('重') == '重・不良'
    assert weight_going.going_class('不良') == '重・不良'
