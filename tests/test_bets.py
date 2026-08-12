#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""買い目ファイルの契約（bets.py のスキーマ）を確かめる。ネットワークには接続しない。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bets  # noqa: E402


def race(marks, **overrides):
    payload = {
        'date': '2026-08-15',
        'races': [{
            'race_id': '202601020811',
            'name': 'テストステークス',
            'start_time': '15:25',
            'marks': marks,
            'bets': [],
        }],
    }
    payload['races'][0].update(overrides)
    return bets.parse_sheet(payload).races[0]


# ----------------------------------------------------------------------
# 手順タグ（第1章「予想の手順（7ステップ）」、2026-08-12 追加）
# ----------------------------------------------------------------------

def test_印にどの手順が根拠だったかをタグ付けできる():
    r = race([{'mark': '◎', 'umaban': 7, 'steps': [1, 3, 5]}])
    assert r.marks[0]['steps'] == [1, 3, 5]


def test_手順タグが無くても読める():
    """過去分（steps を持たない買い目）は遡って埋められない。読めなくなってはいけない。"""
    r = race([{'mark': '◎', 'umaban': 7}])
    assert 'steps' not in r.marks[0]


def test_知らない手順番号は弾く():
    with pytest.raises(bets.BetsError):
        race([{'mark': '◎', 'umaban': 7, 'steps': [8]}])


def test_手順0も弾く():
    with pytest.raises(bets.BetsError):
        race([{'mark': '◎', 'umaban': 7, 'steps': [0]}])


def test_書き出しても手順タグが残る():
    r = race([{'mark': '◎', 'umaban': 7, 'steps': [1, 5]}])
    assert r.to_dict()['marks'][0]['steps'] == [1, 5]


def test_手順は第1章の7つで全部揃っている():
    assert set(bets.STEPS) == set(range(1, 8))
