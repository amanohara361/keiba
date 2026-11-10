#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""無印にした理由（unmarked）とカードの近走の突き合わせ。ネットワークには接続しない。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bets  # noqa: E402
import unmarked_check as uc  # noqa: E402


def race(unmarked=None, partners=None):
    payload = {
        'date': '2026-11-08',
        'races': [{
            'race_id': '202605050211',
            'name': 'テストステークス',
            'start_time': '15:30',
            'marks': [{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 4}],
            'partners': partners or [],
            'bets': [],
        }],
    }
    if unmarked is not None:
        payload['races'][0]['unmarked'] = unmarked
    return bets.parse_sheet(payload).races[0]


def jra_card(going='良'):
    """AR共和国杯の6番（2026-11-08）を縮めたもの。京都大賞典は稍重の1.0秒差。"""
    return {
        'surface': '芝', 'distance': 2500,
        'conditions': {'going': going, 'surface': '芝', 'distance': 2500},
        'entries': [{'umaban': 6, 'recent': [
            {'date': '2026/10/04', 'race': '京都大賞典(GII)', 'rank': 12, 'margin': '1.0',
             'going': '稍', 'surface': '芝', 'distance': 2400, 'passing': '8-8-11-12'},
            {'date': '2026/03/22', 'race': '阪神大賞典(GII)', 'rank': 9, 'margin': '2.4',
             'going': '良', 'surface': '芝', 'distance': 3000, 'passing': '5-5-6-8'},
            {'date': '2025/11/02', 'race': '某S', 'rank': 10, 'margin': '1.8',
             'going': '良', 'surface': '芝', 'distance': 2500, 'passing': '3-3-3-5'},
        ]}],
    }


def test_書き出しても無印の理由が残る():
    r = race([{'umaban': 6, 'reason': '京都大賞典12着', 'loss_date': '2026/10/04'}])
    assert r.to_dict()['unmarked'] == [
        {'umaban': 6, 'reason': '京都大賞典12着', 'loss_date': '2026/10/04'}]


def test_無印の理由が無い古いファイルも読める():
    r = race()
    assert r.unmarked == []
    assert 'unmarked' not in r.to_dict()


def test_loss_dateが日付でなければ弾く():
    with pytest.raises(bets.BetsError):
        race([{'umaban': 6, 'reason': 'x', 'loss_date': '前走'}])


def test_着差1秒5未満と馬場違いを知らせる():
    notes = uc.check_race(
        race([{'umaban': 6, 'reason': '京都大賞典12着', 'loss_date': '2026/10/04'}]), jra_card())
    (num, text), = notes
    assert num == 6
    assert '1.5秒未満' in text and '馬場が違う' in text and '1.0秒差' in text


def test_距離違いを知らせる():
    (_, text), = uc.check_race(
        race([{'umaban': 6, 'reason': '阪神大賞典9着2.4秒', 'loss_date': '20260322'}]), jra_card())
    assert '距離が違う' in text and '1.5秒未満' not in text


def test_基準どおりの負けは知らせるだけで切ってよい():
    (_, text), = uc.check_race(
        race([{'umaban': 6, 'reason': '1.8秒差', 'loss_date': '2025-11-02'}]), jra_card())
    assert '基準どおり' in text


def test_朝に馬場が未発表なら馬場は比べない():
    (_, text), = uc.check_race(
        race([{'umaban': 6, 'reason': 'x', 'loss_date': '2026/10/04'}]), jra_card(going=None))
    assert '馬場が違う' not in text and '1.5秒未満' in text


def test_負けを理由にしてloss_dateが無ければ知らせる():
    (num, text), = uc.check_race(race([{'umaban': 6, 'reason': '逃げて崩れ'}]), jra_card())
    assert num == 6 and 'loss_date' in text


def test_負け以外の理由なら日付は要らない():
    assert uc.check_race(race([{'umaban': 6, 'reason': '初ダートで血統も合わない'}]), jra_card()) == []


def test_印やpartnersの馬は無印に書かない():
    (num, text), = uc.check_race(
        race([{'umaban': 9, 'reason': 'x'}], partners=[{'umaban': 9, 'reason': 'y'}]), jra_card())
    assert num == 9 and 'partners' in text


def test_無印の理由が空なら知らせる():
    (num, text), = uc.check_race(race([]), jra_card())
    assert num is None and 'unmarked' in text


def test_地方の2着は大敗ではない():
    card = {'surface': 'ダート', 'distance': 1200, 'going': '稍重', 'entries': [
        {'umaban': 13, 'recent_runs': [
            {'date': '20261022', 'race_name': '特別', 'finish': 2, 'margin': '3/4',
             'going': '重', 'surface': 'ダート', 'distance': 1200, 'corners': [1, 1]}]}]}
    (_, text), = uc.check_race(race([{'umaban': 13, 'reason': '逃げて崩れ', 'loss_date': '20261022'}]), card)
    assert '大敗ではない' in text and '勝ち馬と3/4' in text


def test_地方の3着以下は勝ち馬との差を確かめるよう知らせる():
    card = {'surface': 'ダート', 'distance': 1200, 'going': '良', 'entries': [
        {'umaban': 13, 'recent_runs': [
            {'date': '20260730', 'race_name': 'ポラリスSS', 'finish': 9, 'margin': '2',
             'going': '良', 'surface': 'ダート', 'distance': 1200, 'corners': [1, 1]}]}]}
    (_, text), = uc.check_race(race([{'umaban': 13, 'reason': '大敗', 'loss_date': '2026/07/30'}]), card)
    assert '前の馬との差' in text
