# -*- coding: utf-8 -*-
"""follow.py（発走前の検算を起こす目覚まし）のテスト。"""

import json
from datetime import date, datetime

import follow

DAY = date(2026, 11, 14)


def at(hh, mm):
    return datetime(2026, 11, 14, hh, mm, tzinfo=follow.JST)


RACES = [('jra', at(14, 55)), ('jra', at(15, 5)), ('jra', at(15, 20)),
         ('jra', at(15, 22)), ('jra', at(15, 45)), ('nar', at(16, 30))]


def test_plan_is_lead_minutes_before_each_post():
    times = follow.plan(RACES, at(14, 8), 'jra')
    # 15:20 と 15:22 は 15:08 の1回にまとめる
    assert [t.strftime('%H:%M') for t in times] == ['14:43', '14:53', '15:08', '15:33']


def test_plan_skips_races_already_inside_lead():
    # 15:07 の検算の時点で、15:05 発走は発走済み、15:20・15:22 は12分前を過ぎた直後
    times = follow.plan(RACES, at(15, 9), 'jra')
    assert [t.strftime('%H:%M') for t in times] == ['15:33']


def test_plan_respects_window_and_org():
    assert follow.plan(RACES, at(10, 7), 'jra') == []
    assert [t.strftime('%H:%M') for t in follow.plan(RACES, at(15, 0), 'nar')] == ['16:18']


def test_load_races_reads_sheet_and_ignores_bad_times(tmp_path, monkeypatch):
    monkeypatch.setattr(follow, 'BETS_DIR', str(tmp_path))
    (tmp_path / '2026-11-14.json').write_text(json.dumps({'races': [
        {'org': 'jra', 'start_time': '15:20'},
        {'start_time': '15:45'},
        {'org': 'nar', 'start_time': None},
    ]}), encoding='utf-8')
    assert follow.load_races(DAY) == [('jra', at(15, 20)), ('jra', at(15, 45))]
    assert follow.load_races(date(2026, 11, 15)) == []


def test_kick_dispatches_only_when_there_is_something_to_wait_for(tmp_path, monkeypatch):
    monkeypatch.setattr(follow, 'BETS_DIR', str(tmp_path))
    (tmp_path / '2026-11-14.json').write_text(json.dumps({'races': [
        {'org': 'jra', 'start_time': '15:20'}]}), encoding='utf-8')
    calls = []

    def dispatcher(workflow, inputs):
        calls.append((workflow, inputs))
        return True

    assert follow.kick(DAY, at(10, 7), 'jra', dispatcher) is False
    assert calls == []
    assert follow.kick(DAY, at(14, 7), 'jra', dispatcher) is True
    assert calls == [('check_follow.yml', {'org': 'jra', 'date': '2026-11-14'})]


def test_run_sleeps_until_each_time_then_dispatches_the_check(tmp_path, monkeypatch):
    monkeypatch.setattr(follow, 'BETS_DIR', str(tmp_path))
    (tmp_path / '2026-11-14.json').write_text(json.dumps({'races': [
        {'org': 'jra', 'start_time': '15:20'},
        {'org': 'jra', 'start_time': '15:45'}]}), encoding='utf-8')
    current = [at(14, 8)]
    slept, calls = [], []

    def sleep(seconds):
        slept.append(round(seconds / 60))
        current[0] = current[0] + follow.timedelta(seconds=seconds)

    def dispatcher(workflow, inputs):
        calls.append((workflow, inputs))
        return True

    fired = follow.run(DAY, at(14, 8), 'jra', dispatcher, sleep, lambda: current[0])
    assert fired == 2
    assert slept == [60, 25]
    assert calls == [('check.yml', {'date': '2026-11-14', 'from_follow': 'true'})] * 2


def test_dispatch_without_token_fails_quietly(monkeypatch):
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    assert follow.dispatch('check.yml', {}, repo='o/r') is False
