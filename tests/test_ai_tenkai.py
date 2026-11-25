#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ai_tenkai.parse の検証。ネットワークには接続しない。

ページの形は 2026-11-25 の浦和記念（nar）と東京1R（race）の出馬表から写した最小形。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_tenkai  # noqa: E402


def _line(rows, tail):
    return ''.join(
        f"$(\"#Horse{u}\").css({{ 'top':'{t}%', 'left':'{l}%', }}).{tail}" for u, t, l in rows)


def _page(plain, jockey, paid):
    rem = "children('span[class*=\"Speed\"]').remove();"
    sp = "append('<span class=\"SpeedUp_02\"></span>');"
    parts = ['<td class="Umaban1 Txt_C">1</td><td class="Umaban2 Txt_C">2</td><td class="Umaban3 Txt_C">3</td>',
             'function updateHorsePosition() {']
    for label, rows in (('チェックなし', plain), ('出遅れ率', paid), ('騎手傾向', jockey)):
        for num, tail in (('１', rem), ('３', rem), ('４', sp)):
            parts.append('// $("#Horse2").css({ dummy });')
            parts.append(_line(rows, tail))
            parts.append(f'// console.log("{label}{num}コーナー");')
    parts.append('// コーナーのクリックイベント')
    return '\n'.join(parts)


PLAIN = [(2, -4, 0), (3, 12, 14.2), (1, -4, 60)]
JOCKEY = [(3, -4, 0), (2, 12, 3), (1, 30, 40)]
PAID = [(2, -4, 0), (13, -4, 22.7), (9, -4, 46)]


def test_orders_and_fields():
    pred = ai_tenkai.parse(_page(PLAIN, JOCKEY, PAID))
    assert pred['runners'] == [1, 2, 3]
    assert ai_tenkai.order(pred, 'plain', 'start') == [2, 3, 1]
    assert ai_tenkai.order(pred, 'jockey', 'c4') == [3, 2, 1]
    first = pred['plain']['c4'][0]
    assert first == {'umaban': 2, 'gap': 0.0, 'lane': 0, 'speed': '02'}
    assert pred['jockey']['start'][2]['lane'] == 2


def test_dummy_rows_are_dropped():
    # 無料版の行が有料版の見本と同じなら捨てる
    pred = ai_tenkai.parse(_page(PLAIN, PAID, PAID))
    assert 'jockey' not in pred
    # 出走馬に無い馬番（13・9）が入った行も捨てる
    pred = ai_tenkai.parse(_page(PLAIN, [(2, -4, 0), (13, 12, 5)], PAID))
    assert 'jockey' not in pred


def test_no_block():
    assert ai_tenkai.parse('<html></html>') is None
    assert ai_tenkai.order(None) == []


def test_url():
    assert 'nar.netkeiba.com' in ai_tenkai.url_for('202642112511')
    assert 'race.netkeiba.com' in ai_tenkai.url_for('202605050601')
