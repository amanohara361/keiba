#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""win_probabilities の書き漏らしが沈黙しないことを確かめる。

2026-08-26、地方の朝タスクが win_probabilities を書かずに push した。
直前検算は買い目を1点も出さなかったが、出力は「基準を満たさないので見送り」
という規律どおりの文言で、メールの件名は「直前検算 問題なし」になる寸前だった。
**買い目が消えているのに問題なしと知らせるのが、いちばん質の悪い沈黙である。**

原因は「入力が欠けている」状態と「規律に届かなかった」判断を、コードが
同じ見送りとして扱っていたこと。ここでは3つの経路それぞれが、欠落を
欠落として言うかどうかを見る。ネットワークには接続しない。
"""

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bet_builder  # noqa: E402
import bets  # noqa: E402
import check  # noqa: E402
import discipline  # noqa: E402
import make_bets  # noqa: E402
from bets import JST, BetSheet, RaceBets  # noqa: E402

WIN_ODDS = {7: 2.5, 11: 6.0, 14: 9.0, 3: 12.0, 5: 20.0, 8: 30.0, 9: 45.0}


def _race(win_probabilities=None, **kw):
    return RaceBets(
        race_id='202608260101', name='テスト記念', venue='船橋', race_no=11,
        start_time='20:05',
        marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11},
               {'mark': '▲', 'umaban': 14}],
        bets=[], win_probabilities=win_probabilities, **kw)


def _lookup(bet_type, numbers):
    """**効率的な市場**を模したオッズ表。

    市場推定勝率から Harville で出した的中率の逆数に、券種の払戻率を掛けて
    返す。つまり期待値がちょうど控除率（0.775〜0.80）になるオッズで、
    どの買い目も規律（期待値1.2）を満たさない。主観勝率で市場を修正しない
    かぎり買い目が出ない、という設計上の前提をそのまま置いている。
    固定値を返すと、市場が非効率なだけの案が規律を通ってしまい、
    「入力欠落なのに買い目が出る」別の話になってしまう。
    """
    p = bet_builder.market_win_probabilities(WIN_ODDS)
    numbers = tuple(numbers)
    if bet_type == '馬連':
        hit = bet_builder.p_quinella(p, numbers)
    elif bet_type == 'ワイド':
        hit = bet_builder.p_wide(p, numbers)
    elif bet_type == '3連複':
        hit = bet_builder.p_trio(p, numbers)
    else:
        return None
    return round(bet_builder.PAYOUT_RATE[bet_type] / hit, 1) if hit else None


# ----------------------------------------------------------------------
# 1. bet_builder は「規律の話」にしない
# ----------------------------------------------------------------------

def test_win_probabilitiesが無い見送りは入力欠落として説明される():
    confidence, built, note = bet_builder.build_bets(
        _race(), _lookup, WIN_ODDS)

    assert confidence == 'C'
    assert built == []
    assert bet_builder.MISSING_INPUT in note
    assert 'win_probabilities' in note
    # 規律のせいにしていないこと。ここが本題。
    assert '第13章の基準' not in note


def test_win_probabilitiesがあれば入力欠落の印は付かない():
    _, _, note = bet_builder.build_bets(
        _race({7: 0.42, 11: 0.20, 14: 0.11}), _lookup, WIN_ODDS)

    assert bet_builder.MISSING_INPUT not in note


# ----------------------------------------------------------------------
# 2. レポートは規律違反より先に入力欠落を出す
# ----------------------------------------------------------------------

def _verdicts(now):
    sheet = BetSheet(date=date(2026, 8, 26), races=[_race()], source='cloud-nar')
    return sheet, check.review_sheet(
        sheet, now,
        fetcher=lambda: {},
        conditions_fetcher=lambda rid: None,
        nar_fetcher=lambda: [])


def test_レポートの先頭に入力欠落の見出しが立つ():
    race = _race()
    verdict = discipline.RaceVerdict(
        race, [], None, {}, {}, {})
    verdict.bet_note = bet_builder.MISSING_INPUT + 'win_probabilities がありません'
    now = datetime(2026, 8, 26, 14, 7, tzinfo=JST)
    sheet = BetSheet(date=date(2026, 8, 26), races=[race], source='cloud-nar')

    body = check.format_report(sheet, [verdict], now)

    assert '朝タスクの入力が欠けているレース: 1件' in body
    # 「全レース、規律をクリア」で終わらせないこと。
    head = body.split('対象日')[0]
    assert '入力が欠けている' in head


def test_入力欠落が無ければ従来どおりの見出しになる():
    race = _race({7: 0.42})
    verdict = discipline.RaceVerdict(
        race, [], None, {}, {}, {})
    verdict.bet_note = 'ワイド 7-11（合成4.20倍・期待値1.31）'
    now = datetime(2026, 8, 26, 14, 7, tzinfo=JST)
    sheet = BetSheet(date=date(2026, 8, 26), races=[race], source='cloud')

    body = check.format_report(sheet, [verdict], now)

    assert '入力が欠けている' not in body
    assert '規律をクリア' in body


# ----------------------------------------------------------------------
# 3. make_bets --show が朝タスクの自己検証として機能する
# ----------------------------------------------------------------------

def test_showは印だけで主観勝率が無いレースを問題として数える(monkeypatch, capsys):
    sheet = BetSheet(date=date(2026, 8, 26), races=[_race()], source='cloud-nar')
    monkeypatch.setattr(bets, 'load_sheet', lambda day: sheet)
    monkeypatch.setattr(make_bets.bets, 'load_sheet', lambda day: sheet)

    problems = make_bets.show(date(2026, 8, 26))
    out = capsys.readouterr().out

    assert problems == 1
    assert 'win_probabilities がありません' in out
    assert '見送り' in out


def test_showは主観勝率があれば問題なしと数える(monkeypatch, capsys):
    sheet = BetSheet(date=date(2026, 8, 26),
                     races=[_race({7: 0.42, 11: 0.20})], source='cloud')
    monkeypatch.setattr(make_bets.bets, 'load_sheet', lambda day: sheet)

    problems = make_bets.show(date(2026, 8, 26))
    out = capsys.readouterr().out

    assert problems == 0
    assert '主観勝率' in out


def test_showは非ゼロで終わるので朝タスクが素通りできない(monkeypatch):
    sheet = BetSheet(date=date(2026, 8, 26), races=[_race()], source='cloud-nar')
    monkeypatch.setattr(make_bets.bets, 'load_sheet', lambda day: sheet)

    assert make_bets.main(['--show', '--date', '2026-08-26']) == 1


def test_印そのものが無いレースは問題に数えない(monkeypatch):
    """◎が無いレースは朝タスクが「印を打てなかった」と決めた結果で、
    書き漏らしではない。ここまで警告すると警告が意味を失う。"""
    race = RaceBets(race_id='202608260102', name='印なし', venue='船橋',
                    race_no=10, start_time='19:30', marks=[], bets=[])
    sheet = BetSheet(date=date(2026, 8, 26), races=[race], source='cloud-nar')
    monkeypatch.setattr(make_bets.bets, 'load_sheet', lambda day: sheet)

    assert make_bets.show(date(2026, 8, 26)) == 0
