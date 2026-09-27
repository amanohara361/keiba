#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""馬場状態の読み取り（conditions.py）。ネットワークには接続しない。"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import conditions  # noqa: E402


@pytest.mark.parametrize('text, going', [
    ('天候:曇 / 馬場:稍', '稍重'),     # race.netkeiba.com の略記
    ('天候:雨 / 馬場:不', '不良'),
    ('天候:曇 / 馬場:重', '重'),
    ('天候:晴 / 馬場:良', '良'),
    ('芝 : 稍重', '稍重'),
    ('ダート:不良', '不良'),
])
def test_略記も正式名で読む(text, going):
    assert conditions.find_going(text) == going


def test_当日の結果ページのヘッダから稍重を読む():
    """2026-09-27 中山2R。直前検算がこの略記を読めず「未発表」扱いにしていた。"""
    path = os.path.join(ROOT, 'tests', 'fixture_nkresult_20260927_202606040902.html')
    with open(path, encoding='utf-8') as f:
        got = conditions.parse(f.read())
    assert got == {'going': '稍重', 'weather': '曇', 'surface': '芝', 'distance': 2000}
