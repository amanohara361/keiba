#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""運用の買い方（2026-11-03 ユーザー承認、予想メソッド第13章冒頭の節）のテスト。

中央は◎の単勝＋複勝の固定形、地方は買わない。従来の bet_builder.build_bets の
結果は説明文に参考として残る。
"""

import os
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import bet_builder  # noqa: E402
import bets  # noqa: E402
import check  # noqa: E402
import conditions as conditions_module  # noqa: E402
import discipline  # noqa: E402
import odds as odds_module  # noqa: E402
import report_html  # noqa: E402
import report_md  # noqa: E402
from bets import JST, Bet, BetSheet, RaceBets  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(report_html, 'HTML_PATH', str(tmp_path / '_report' / 'index.html'))
    monkeypatch.setattr(report_md, 'CHECKS_MD_DIR', str(tmp_path / '_checks_md'))


def make_race(org='jra', **kwargs):
    defaults = dict(
        race_id='202601020811', name='テストステークス', start_time='15:25',
        marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11},
               {'mark': '△', 'umaban': 14}],
        bets=[], org=org, win_probabilities={7: 0.35, 11: 0.20, 14: 0.12},
    )
    defaults.update(kwargs)
    return RaceBets(**defaults)


WIN_ODDS = {7: 2.9, 11: 5.0, 14: 9.0, 2: 20.0}


def no_odds(bet_type, horses):
    return None


def test_中央は本命の単勝と複勝の固定形():
    race = make_race()
    confidence, built, note = bet_builder.build_policy_bets(race, no_odds, WIN_ODDS)
    assert confidence == 'B'
    assert [str(b) for b in built] == ['単勝 7', '複勝 7']
    assert note.startswith('中央の固定形：◎7の単勝＋複勝（単勝2.9倍）')
    assert '【参考：従来の組み立て】' in note
    assert '足切りなしなら勝負度' in note
    # 主観的中率は運用の買い目に使わないので、検算記録に残さない
    assert race.subjective_hit_rate is None


def test_地方は買い目を出さず従来の組み立てを参考に残す():
    confidence, built, note = bet_builder.build_policy_bets(make_race(org='nar'), no_odds, WIN_ODDS)
    assert (confidence, built) == ('C', [])
    assert note.startswith(bet_builder.NAR_STOPPED)
    assert '【参考：従来の組み立て】買い目なし' in note


def test_朝の見送り判断は固定形より優先する():
    race = make_race(skip_reason='混戦で確信が低い')
    confidence, built, note = bet_builder.build_policy_bets(race, no_odds, WIN_ODDS)
    assert (confidence, built) == ('C', [])
    assert '混戦で確信が低い' in note


def test_本命の単勝オッズが無ければ見送る():
    confidence, built, note = bet_builder.build_policy_bets(make_race(), no_odds, {11: 5.0})
    assert (confidence, built) == ('C', [])
    assert '取消・除外の可能性' in note


def test_本命の馬体重が大きく動いたら見送る():
    confidence, built, note = bet_builder.build_policy_bets(
        make_race(), no_odds, WIN_ODDS, withdrawn={7})
    assert (confidence, built) == ('C', [])
    assert '±15kg' in note


def test_固定形には合成オッズと期待値の規律を掛けない():
    race = make_race(bets=[Bet('単勝', [7]), Bet('複勝', [7])], confidence='B')
    assert discipline.is_fixed_form(race)
    verdict = discipline.review_race(race, [2.9, 1.2], {7: (2.9, 1)}, {},
                                     datetime(2026, 8, 2, 14, 30, tzinfo=JST), date(2026, 8, 2))
    assert not verdict.blocked
    assert 'fixed_form' in [f.code for f in verdict.findings]
    assert 'composite_below_minimum' not in [f.code for f in verdict.findings]


def test_固定形の判定は中身で行う():
    assert not discipline.is_fixed_form(make_race(bets=[Bet('単勝', [11]), Bet('複勝', [11])]))
    assert not discipline.is_fixed_form(make_race(bets=[Bet('単勝', [7])]))
    assert not discipline.is_fixed_form(
        make_race(org='nar', bets=[Bet('単勝', [7]), Bet('複勝', [7])]))


def test_直前検算の全体を通して固定形が出る(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path / 'bets'))
    monkeypatch.setattr(bets, 'CHECKS_DIR', str(tmp_path / 'checks'))
    bets.save_sheet(BetSheet(date=date(2026, 8, 2), races=[make_race(name='クイーンステークス')]))

    def fake_fetch(race_id, bet_type):
        tables = {
            '単勝': {'07': ['2.9', '3.0', '1'], '11': ['5.0', '5.2', '2'],
                   '14': ['9.0', '9.4', '3'], '02': ['20.0', '21.0', '4']},
            '複勝': {'07': ['1.2', '1.5', '1']},
            '馬連': {'0711': ['8.0', '8.2', '2']},
        }
        return {'status': 'middle', 'reason': None,
                'official_datetime': '14:28:00', 'odds': tables.get(bet_type, {})}

    monkeypatch.setattr(odds_module, 'fetch', fake_fetch)
    monkeypatch.setattr(conditions_module, 'fetch',
                        lambda rid, **kw: {'going': '良', 'weather': '晴',
                                          'surface': '芝', 'distance': 1800})

    exit_code = check.main(['--date', '2026-08-02', '--now', '2026-08-02T14:30', '--no-email'])
    assert exit_code == check.EXIT_OK
    out = capsys.readouterr().out
    assert '単勝 7  2.9倍' in out
    assert '複勝 7  1.2倍' in out
    assert '発注を止めました' not in out

    saved = bets.load_sheet(date(2026, 8, 2)).races[0]
    assert saved.confidence == 'B'
    assert [str(b) for b in saved.bets] == ['単勝 7', '複勝 7']
