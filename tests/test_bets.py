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


# ----------------------------------------------------------------------
# 中穴候補（partners、2026-08-14 追加）
# ----------------------------------------------------------------------

def test_中穴候補を読める():
    r = race([{'mark': '◎', 'umaban': 7}],
             partners=[{'umaban': 9, 'reason': '前走出遅れ'}])
    assert r.partners == [{'umaban': 9, 'reason': '前走出遅れ'}]


def test_中穴候補は無くても読める():
    r = race([{'mark': '◎', 'umaban': 7}])
    assert r.partners == []


def test_中穴候補の理由は省略できる():
    r = race([{'mark': '◎', 'umaban': 7}], partners=[{'umaban': 9}])
    assert r.partners == [{'umaban': 9, 'reason': ''}]


def test_書き出しても中穴候補が残る():
    r = race([{'mark': '◎', 'umaban': 7}],
             partners=[{'umaban': 9, 'reason': '前走出遅れ'}])
    assert r.to_dict()['partners'] == [{'umaban': 9, 'reason': '前走出遅れ'}]


def test_相手プールは印の優先順で中穴候補が最後につく():
    r = race([
        {'mark': '◎', 'umaban': 7},
        {'mark': '○', 'umaban': 11},
        {'mark': '▲', 'umaban': 2},
        {'mark': '△', 'umaban': 3},
        {'mark': '△', 'umaban': 14},
    ], partners=[{'umaban': 9, 'reason': '前走出遅れ'}, {'umaban': 5, 'reason': '距離短縮'}])
    assert r.partner_pool == [11, 2, 3, 14, 9, 5]


def test_相手プールは軸と重複した中穴候補を除く():
    r = race([{'mark': '◎', 'umaban': 7}], partners=[{'umaban': 7, 'reason': '軸と同じ'}])
    assert r.partner_pool == []


def test_相手プールは印馬と重複した中穴候補を足さない():
    r = race([
        {'mark': '◎', 'umaban': 7},
        {'mark': '○', 'umaban': 11},
    ], partners=[{'umaban': 11, 'reason': '重複'}])
    assert r.partner_pool == [11]
