#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直前検算（check.py）が地方競馬（org='nar'）でも買い目を組み立てられるか確かめる。

bet_builder の組み込み（2026-08-14）で _NarDay に raw_tables/ninki/meta を
足した。中央側と経路が分かれているぶん、独立に確認しておく。
ネットワークには接続しない。
"""

import io
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bets  # noqa: E402
import check  # noqa: E402
import nar  # noqa: E402
from bets import JST, BetSheet, RaceBets  # noqa: E402

RACELIST_HEADER = (
    '競馬場,競走年月日,レース番号,発走時刻,競走種類名称,レース名,芝ダート区分,回り,'
    '距離,天候,馬場,頭数,条件,1着賞金(円),コーナー通過順1,コーナー通過順2,'
    'コーナー通過順3,コーナー通過順4')

RACELIST = RACELIST_HEADER + '\n' + (
    '笠松,20260812,11,1635,重賞,テスト記念,ダート,右,1580,晴,良,10,オープン,6000000,,,,')

ODDS_HEADER = '競馬場,競走年月日,レース番号,賭式,番号1,番号2,番号3,オッズ,オッズ（最大）,人気'

ODDS = ODDS_HEADER + '\n' + '\n'.join([
    '笠松,20260812,11,単勝,1,,,3.0,,1',
    '笠松,20260812,11,馬複,1,2,,8.0,,3',  # 公式CSVでは馬連は「馬複」と表記される
    '笠松,20260812,11,馬複,1,3,,8.0,,4',
])


def rows(text):
    import csv
    return list(csv.DictReader(io.StringIO(text)))


def _sheet():
    return BetSheet(date=date(2026, 8, 12), races=[RaceBets(
        race_id='202608124711', name='テスト記念', venue='笠松', race_no=11,
        start_time='16:35', org='nar',
        marks=[{'mark': '◎', 'umaban': 1}, {'mark': '○', 'umaban': 2},
               {'mark': '▲', 'umaban': 3}],
        bets=[], subjective_hit_rate=0.35,
    )])


def test_地方の買い目も実オッズから組み立てて検算する(monkeypatch):
    monkeypatch.setattr(nar, 'race_data',
                        lambda type_=nar.DAILY, day=None, opener=None:
                        {'racelist': rows(RACELIST)})

    now = datetime(2026, 8, 12, 13, 0, tzinfo=JST)
    verdicts = check.review_sheet(_sheet(), now, nar_fetcher=lambda: rows(ODDS))

    assert len(verdicts) == 1
    verdict = verdicts[0]
    race = verdict.race
    assert race.org == 'nar'
    assert race.confidence in ('A', 'B')
    assert race.bets, '実オッズで買い目が組まれているはず'
    assert {b.combination for b in race.bets} == {'1-2', '1-3'}
    assert not verdict.blocked


def test_地方でオッズが未発売なら見送りになる(monkeypatch):
    """当日ファイルが空（発売前）のとき、無いオッズで買い目を捏造しない。"""
    monkeypatch.setattr(nar, 'race_data',
                        lambda type_=nar.DAILY, day=None, opener=None:
                        {'racelist': rows(RACELIST)})

    now = datetime(2026, 8, 12, 13, 0, tzinfo=JST)
    verdicts = check.review_sheet(_sheet(), now, nar_fetcher=lambda: [])

    race = verdicts[0].race
    assert race.confidence == 'C'
    assert race.bets == []
