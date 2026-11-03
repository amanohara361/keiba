#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""騎手別の見込み勝利数（jockey_edge.py）を架空のレースで確かめる。

実在レースの数字は使わない（test_weight_going.py と同じ理由）。
"""

import json
from datetime import date

import pytest

import jockey_edge
import review


def _race(rid, horses):
    return {'race_id': rid,
            'horses': [{'着順': fin, '単勝': str(o), '人気': str(pop), '騎手': j}
                       for fin, o, pop, j in horses]}


def _write(tmp_path, name, races):
    p = tmp_path / name
    p.write_text(json.dumps(races, ensure_ascii=False), encoding='utf-8')
    return str(p)


def test_見込み勝率はレースごとに合計1へ割り戻す(tmp_path):
    p = _write(tmp_path, '20260101_東京.json',
               [_race('1', [(1, 2.0, 1, 'Ａ'), (2, 4.0, 2, 'Ｂ'), (3, 4.0, 3, 'Ｃ')])])
    rides = jockey_edge.load_rides([p])
    assert sum(r[2] for r in rides) == pytest.approx(1.0)
    assert [round(r[2], 3) for r in rides] == [0.5, 0.25, 0.25]
    assert rides[0][0] == date(2026, 1, 1)


def test_取消や着順の無い馬は数えない(tmp_path):
    race = _race('1', [(1, 2.0, 1, 'Ａ'), (2, 3.0, 2, 'Ｂ'), (3, 5.0, 3, 'Ｃ')])
    race['horses'][2]['着順'] = '取消'
    p = _write(tmp_path, '20260101_東京.json', [race])
    assert {r[1] for r in jockey_edge.load_rides([p])} == {'Ａ', 'Ｂ'}


def test_略名とフルネームと減量記号は同じ騎手に寄せる():
    m = jockey_edge.canonical_map({'岩田望', '岩田望来', '津村', '津村明', '津村明秀'})
    assert m['岩田望来'] == '岩田望'
    assert m['津村明秀'] == '津村'  # 多段でも行き止まりまでたどる
    assert jockey_edge._strip('◇小林美駒') == '小林美駒'
    assert jockey_edge._strip('Ｍ．デム') == 'Ｍデム'


def test_別々の騎手の頭に一致する略名には寄せない():
    m = jockey_edge.canonical_map({'小林', '小林美', '小林凌', '小林美駒'})
    assert m['小林美駒'] == '小林美'
    assert m['小林美'] == '小林美'
    assert m['小林凌'] == '小林凌'


def test_人気どおりなら差はほぼ0で人気以上に勝てば正になる():
    # 毎回 見込み0.5 の馬に乗り、10回中9勝した騎手と5勝した騎手
    d = date(2026, 1, 1)
    rides = ([(d, '強', 0.5, i < 9, 2.0, 1) for i in range(10)]
             + [(d, '並', 0.5, i < 5, 2.0, 1) for i in range(10)])
    s = jockey_edge.summarize(rides)
    assert s['並']['all']['diff'] == pytest.approx(0.0)
    assert s['強']['all']['diff'] == pytest.approx(4.0)
    assert s['強']['all']['z'] > 2


def test_一撃抜きの回収率は最大の1勝を除く():
    d = date(2026, 1, 1)
    rides = [(d, 'Ａ', 0.02, True, 50.0, 12)] + [(d, 'Ａ', 0.02, False, 50.0, 12)] * 9
    a = jockey_edge.summarize(rides)['Ａ']['all']
    assert a['roi'] == pytest.approx(5.0)
    assert a['roi_cut'] == pytest.approx(0.0)


def test_直近の窓は最終日から数える():
    rides = [(date(2026, 1, 1), 'Ａ', 0.5, True, 2.0, 1),
             (date(2026, 6, 1), 'Ａ', 0.5, True, 2.0, 1)]
    s = jockey_edge.summarize(rides, recent_days=30)
    assert s['Ａ']['all']['n'] == 2
    assert s['Ａ']['recent']['n'] == 1


def test_週次レビューに節が載りデータが無ければ出さない(tmp_path):
    races = [_race(str(i), [(1, 2.0, 1, 'Ａ'), (2, 3.0, 2, 'Ｂ')]) for i in range(3)]
    _write(tmp_path, '20260101_東京.json', races)
    lines = review.jockey_edge_report(str(tmp_path))
    assert lines and lines[0].startswith('対象: 20260101_東京.json')
    assert review.render_jockey_edge(lines)[0].startswith('## 騎手別')
    assert review.jockey_edge_report(str(tmp_path / 'none')) is None
    assert review.render_jockey_edge(None) == []
