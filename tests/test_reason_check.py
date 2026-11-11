#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""勝率を動かした理由の区分（win_reasons）の点検と集計。ネットワークには接続しない。"""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bets  # noqa: E402
import reason_check as rc  # noqa: E402


def race(win_probabilities, win_reasons=None, org='jra', race_id='202605050211'):
    payload = {
        'date': '2026-11-14',
        'races': [{
            'race_id': race_id, 'org': org, 'name': 'テスト', 'start_time': '15:30',
            'marks': [{'mark': '◎', 'umaban': 1}], 'bets': [],
            'win_probabilities': {str(k): v for k, v in win_probabilities.items()},
            **({'win_reasons': win_reasons} if win_reasons is not None else {}),
        }],
    }
    return bets.parse_sheet(payload).races[0]


def result(rows):
    """rows: [(馬番, 着順, 単勝オッズ)]"""
    return {'race_id': 'x', 'finishing_order': [
        {'rank': r, 'umaban': n, 'odds': o} for n, r, o in rows]}


DAY = date(2026, 11, 14)


def test_理由の無い馬番を返す():
    r = race({1: 0.3, 2: 0.2, 3: 0.1},
             [{'umaban': 1, 'category': '馬場', 'reason': 'a'},
              {'umaban': 3, 'category': '展開', 'reason': 'b'}])
    assert rc.missing_reasons(r) == [2]


def test_欄が空なら全馬が欠け_check_raceは1行にまとめる():
    r = race({1: 0.3, 2: 0.2})
    assert rc.missing_reasons(r) == [1, 2]
    notes = rc.check_race(r)
    assert len(notes) == 1 and notes[0][0] is None


def test_勝率が無いレースは知らせない():
    assert rc.check_race(race({})) == []


def test_そろっていれば知らせない():
    r = race({1: 0.3}, [{'umaban': 1, 'category': '条件', 'reason': 'a'}])
    assert rc.check_race(r) == []


def test_空データ():
    a = rc.analyze([])
    assert a['by_org'] == {} and a['horses'] == 0
    assert 'まだ記録がありません（2026-11-14 の朝の予想から）' in rc.render_lines(a)


def test_見込み勝利数と回収率と上げ下げ():
    # オッズ 2,4,4 → 逆数 .5,.25,.25 合計1.0 → 市場 .5,.25,.25
    r = race({1: 0.6, 2: 0.2},
             [{'umaban': 1, 'category': '馬場', 'reason': 'a'},
              {'umaban': 1, 'category': '展開', 'reason': 'b'},
              {'umaban': 2, 'category': '展開', 'reason': 'c'}])
    res = result([(1, 1, 2.0), (2, 2, 4.0), (3, 3, 4.0)])
    a = rc.analyze([(DAY, r, res)])
    cats = a['by_org']['jra']['cats']
    assert set(cats) == {'馬場', '展開'}          # 先頭の区分だけで数える
    b = rc.finish(cats['馬場'])
    assert (b['n'], b['wins'], b['place']) == (1, 1, 1)
    assert b['expected'] == pytest.approx(0.5)
    assert b['roi'] == pytest.approx(2.0)
    t = rc.finish(cats['展開'])
    assert (t['n'], t['wins'], t['place']) == (1, 0, 1)
    assert t['roi'] == 0
    blk = a['by_org']['jra']
    assert blk['up']['n'] == 1 and blk['down']['n'] == 1     # 1番は上げ、2番は下げ
    assert rc.finish(blk['all'])['expected'] == pytest.approx(0.75)
    assert a['horses'] == 2
    text = '\n'.join(rc.render_lines(a))
    assert '30頭未満の区分は判定しない' in text and '中央' in text


def test_区分の無い馬は区分なしに入り_地方は別に数える():
    r = race({1: 0.5}, [], org='nar')
    res = result([(1, 2, 2.0), (2, 1, 2.0)])
    a = rc.analyze([(DAY, r, res)])
    assert list(a['by_org']) == ['nar']
    assert rc.NO_CATEGORY in a['by_org']['nar']['cats']
    assert a['horses'] == 0     # 区分のある馬がいなければ「記録なし」と出す
    assert 'まだ記録がありません' in '\n'.join(rc.render_lines(a))


def test_確定オッズの無いレースは数えない():
    r = race({1: 0.5}, [{'umaban': 1, 'category': '馬場', 'reason': 'a'}])
    assert rc.analyze([(DAY, r, {'finishing_order': [{'rank': 1, 'umaban': 1}]})])['by_org'] == {}
