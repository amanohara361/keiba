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


# --- 週次レビューへの組み込み（review.py） ---

from datetime import date  # noqa: E402

import review  # noqa: E402

PERIOD = (date(2026, 10, 6), date(2026, 10, 12))


def test_メールに重不良の鞍数が1行出て目安未満なら検討どきは出ない():
    body = review.render_mail(review.summarize([]), PERIOD, 'x.md', wg_heavy=33)
    assert '道悪×馬体重（観察中）：重・不良 33/60鞍' in body
    assert '検討どき' not in body


def test_目安に届いたらメールの冒頭で知らせる():
    body = review.render_mail(review.summarize([]), PERIOD, 'x.md', wg_heavy=60)
    assert '■ 道悪×馬体重の検討どきです（重・不良 60鞍' in body


def test_集計できないときはメールにも詳細にも出さない(tmp_path):
    assert review.weight_going_report(str(tmp_path)) == (None, 0)
    body = review.render_mail(review.summarize([]), PERIOD, 'x.md', wg_heavy=None)
    assert '道悪×馬体重' not in body
    assert review.render_weight_going(None) == []


def test_詳細ファイル用の行は通算の表と残り鞍数を含む(tmp_path):
    horses = [(1, 2.0, 500), (2, 3.0, 480), (3, 5.0, 460), (4, 9.0, 440), (5, 20.0, 420)]
    p = tmp_path / '20260101_東京.json'
    p.write_text(json.dumps([_race('1', '重', horses)], ensure_ascii=False), encoding='utf-8')
    lines, heavy = review.weight_going_report(str(tmp_path))
    assert heavy == 1
    assert lines[-1] == '重・不良は1鞍（目安60鞍まであと59鞍）。'
    assert '## 道悪×相対馬体重（観察中・規則化前）' in review.render_weight_going(lines)
