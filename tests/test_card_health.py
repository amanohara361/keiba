#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""カードの欄の空き具合（card_health.py）と、直前検算のメール件名。ネットワークには接続しない。"""

import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bets  # noqa: E402
import card_health  # noqa: E402
import check  # noqa: E402
import conditions as conditions_module  # noqa: E402
import odds as odds_module  # noqa: E402
from bets import Bet, BetSheet  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, 'tests'))
from test_discipline import make_race, marks_of  # noqa: E402


def entry(**over):
    base = {'jockey': '武豊', 'kinryo': 56.0, 'sex': '牡', 'affiliation': '栗東',
            'blinker': False, 'oikiri': {'rank': 'B'}, 'pedigree': {'father': 'x'},
            'recent': [{'passing': '3-3', 'last3f': 34.5}]}
    base.update(over)
    return base


def race(surface='芝', entries=None, **cond):
    conditions = {'going': '良', 'weather': '晴', 'turn': '右', 'rail': 'A',
                  'race_terms': '3勝クラス'}
    conditions.update(cond)
    return {'candidate': True, 'surface': surface, 'conditions': conditions,
            'odds': {'win_odds': {'1': 2.0}}, 'entries': entries or [entry(), entry()]}


def test_全部埋まっていれば何も言わない():
    assert card_health.check({'races': [race(), race(surface='ダート', rail=None)]}) == []


def test_回りが全レース空なら知らせる():
    """2026-10-31〜11-14 の実際の壊れ方。括弧が消えて turn・rail が全部 null になった。"""
    card = {'races': [race(turn=None, rail=None), race(surface='ダート', turn=None, rail=None)]}
    got = {p['key']: (p['filled'], p['total']) for p in card_health.check(card)}
    assert got == {'turn': (0, 2), 'rail': (0, 1)}     # コース区分は芝だけを数える


def test_1頭だけの欠けは知らせない():
    card = {'races': [race(entries=[entry(), entry(), entry(jockey=None)])]}
    assert card_health.check(card) == []


def test_馬の欄の大半が空なら知らせる():
    card = {'races': [race(entries=[entry(jockey=None), entry(jockey=None), entry()])]}
    assert [p['key'] for p in card_health.check(card)] == ['jockey']


def test_候補でないレースは数えない():
    other = race(turn=None)
    other['candidate'] = None
    assert card_health.check({'races': [race(), other]}) == []


def test_馬場はカードでは見ない():
    """前夜は発表前のことがある（10-31 は 2/6）。馬場は検算時に見る。"""
    assert card_health.check({'races': [race(going=None)]}) == []


def test_検算時はキーがあって空のものだけ数える():
    assert card_health.check_conditions([{'going': '良'}, {'going': '良'}]) == []
    got = card_health.check_conditions([{'going': '良', 'turn': None}, {'going': '良', 'turn': None}, None])
    assert [(p['key'], p['filled'], p['total']) for p in got] == [('turn', 0, 2)]


def test_欄が読めていなければ検算メールの件名で知らせる(tmp_path, monkeypatch):
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path / 'bets'))
    monkeypatch.setattr(bets, 'CHECKS_DIR', str(tmp_path / 'checks'))
    cards = tmp_path / 'cards'
    cards.mkdir()
    (cards / '2026-08-02.json').write_text(json.dumps({
        'date': '2026-08-02', 'races': [],
        'health': [{'key': 'turn', 'label': '回り', 'filled': 0, 'total': 6}]}), encoding='utf-8')
    monkeypatch.setattr(card_health, 'CARDS_DIR', str(cards))

    sent = []
    monkeypatch.setattr(check.Mailer, 'is_configured', lambda self: True)
    monkeypatch.setattr(check.Mailer, 'send',
                        lambda self, subject, body: sent.append((subject, body)) or True)

    bets.save_sheet(BetSheet(
        date=date(2026, 8, 2),
        races=[make_race(name='クイーンステークス',
                         marks=marks_of(items=[('◎', 7), ('○', 11), ('△', 14)]),
                         bets=[Bet('馬連', [7, 11]), Bet('ワイド', [7, 14])],
                         subjective_hit_rate=0.35)],
    ))

    def fake_fetch(race_id, bet_type):
        tables = {'単勝': {'07': ['2.9', '3.0', '1'], '11': ['5.0', '5.2', '2'],
                         '14': ['9.0', '9.4', '3'], '02': ['20.0', '21.0', '4']},
                  '馬連': {'0711': ['11.2', '11.5', '3']}, 'ワイド': {'0714': ['9.0', '9.4', '4']}}
        return {'status': 'middle', 'reason': None,
                'official_datetime': '14:28:00', 'odds': tables.get(bet_type, {})}

    monkeypatch.setattr(odds_module, 'fetch', fake_fetch)
    monkeypatch.setattr(conditions_module, 'fetch',
                        lambda rid, **kw: {'going': '良', 'weather': '晴',
                                          'surface': '芝', 'distance': 1800})

    exit_code = check.main(['--date', '2026-08-02', '--now', '2026-08-02T14:30', '--no-save',
                            '--no-html', '--no-md'])
    assert exit_code == check.EXIT_OK          # 発注は止めない。知らせるだけ
    assert [s for s, _ in sent] == ['【要確認】カードの欄が読めていません（回り）']
    assert '回り（turn）：全部空' in sent[0][1]
