#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_bets.py（アプリが固まったときの手入力の保険）を確かめる。ネットワークには接続しない。

2026-08-26、win_probabilities の導入で買い目の組み立て方式が変わったが、
make_bets.py の3つの入力経路（対話式・--paste・--file）はどれも
win_probabilities を集めていなかった。そのため手入力で作った買い目は
直前検算で必ず「入力欠落」扱いになり、実質的にすべて見送りになっていた。
保険が保険として機能していなかった不具合の修正を確かめる。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_bets  # noqa: E402
from bets import BetsError  # noqa: E402


def test_勝率の行を読み取れる():
    assert make_bets.parse_win_prob_line('勝率 7:0.28 11:0.15') == {7: 0.28, 11: 0.15}


def test_主観勝率でも読み取れる():
    assert make_bets.parse_win_prob_line('主観勝率 8:0.3') == {8: 0.3}


def test_全角コロンでも読み取れる():
    assert make_bets.parse_win_prob_line('勝率 8：0.3') == {8: 0.3}


def test_パーセント表記でも読み取れる():
    assert make_bets.parse_win_prob_line('勝率 8:30%') == {8: 0.3}


def test_勝率で始まらない行はNoneを返す():
    """馬連などの買い目行と取り違えないことを確認する。"""
    assert make_bets.parse_win_prob_line('馬連 7-11 100') is None
    assert make_bets.parse_win_prob_line('◎7 ○11') is None


def test_勝率の行なのに読み取れない場合はエラーになる():
    with pytest.raises(BetsError):
        make_bets.parse_win_prob_line('勝率 読めません')


def test_ブロック全体から勝率が拾われてRaceBetsに入る():
    block = (
        '202601020811 クイーンステークス 15:25 B\n'
        '◎7 ○11 ▲2\n'
        '勝率 7:0.28 11:0.15\n'
        '馬連 7-11 100\n'
    )
    r = make_bets.parse_block(block)
    assert r.win_probabilities == {7: 0.28, 11: 0.15}
    assert r.marked_horses == {7, 11, 2}
    assert len(r.bets) == 1


def test_勝率の行が無くても空のwin_probabilitiesで組める():
    """後方互換：勝率の行を省いた旧形式のテキストも引き続き読める。"""
    block = (
        '202601020811 クイーンステークス 15:25 B\n'
        '◎7 ○11 ▲2\n'
        '馬連 7-11 100\n'
    )
    r = make_bets.parse_block(block)
    assert r.win_probabilities == {}


def test_対話式でも勝率を集める(monkeypatch):
    """ask_race() が win_probabilities を RaceBets へ渡すことを確認する。"""
    answers = iter([
        '202601020811',   # race_id
        'クイーンステークス',  # レース名
        '15:25',          # 発走時刻
        'B',               # 勝負度
        '◎7 ○11 ▲2',      # 印
        '馬連 7-11 100',    # 買い目
        '',                # 買い目入力の終了
        '',                # （非推奨）主観的中率をスキップ
        '7 0.28',          # 勝率
        '11 0.15',         # 勝率
        '',                # 勝率入力の終了
    ])
    monkeypatch.setattr('builtins.input', lambda *a: next(answers))
    r = make_bets.ask_race()
    assert r.win_probabilities == {7: 0.28, 11: 0.15}


# ----------------------------------------------------------------------
# --show の勝率理由の警告（2026-11-11）。知らせるだけで終了コードは変えない
# ----------------------------------------------------------------------

def _show(tmp_path, monkeypatch, capsys, win_reasons):
    import json
    from datetime import date
    import bets
    race = {'race_id': '202605050211', 'name': 'テスト', 'start_time': '15:30',
            'marks': [{'mark': '◎', 'umaban': 1}], 'bets': [],
            'win_probabilities': {'1': 0.3, '2': 0.2}}
    if win_reasons is not None:
        race['win_reasons'] = win_reasons
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path))
    (tmp_path / '2026-11-14.json').write_text(
        json.dumps({'date': '2026-11-14', 'races': [race]}), encoding='utf-8')
    problems = make_bets.show(date(2026, 11, 14))
    return problems, capsys.readouterr().out


def test_show_理由の無い馬を知らせる(tmp_path, monkeypatch, capsys):
    problems, out = _show(tmp_path, monkeypatch, capsys,
                          [{'umaban': 1, 'category': '馬場', 'reason': 'a'}])
    assert '⚠ 勝率理由2: 2番の勝率に理由の区分がありません' in out
    assert problems == 0


def test_show_理由の欄が空なら知らせる(tmp_path, monkeypatch, capsys):
    problems, out = _show(tmp_path, monkeypatch, capsys, None)
    assert 'win_reasons が空です' in out
    assert problems == 0


def test_show_そろっていれば知らせない(tmp_path, monkeypatch, capsys):
    problems, out = _show(tmp_path, monkeypatch, capsys,
                          [{'umaban': 1, 'category': '馬場', 'reason': 'a'},
                           {'umaban': 2, 'category': '展開', 'reason': 'b'}])
    assert '勝率理由' not in out
    assert problems == 0
