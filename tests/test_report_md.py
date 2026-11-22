#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check.py の検算結果をMarkdownに描き出す report_md.py の単体テスト。

GitHub上でリポジトリを直接ブラウズしても読めるように、という目的なので、
確かめるのは report_html.py と同じく「印・買い目・BLOCK理由」を
取りこぼさずに出せているかだけ。
"""

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discipline  # noqa: E402
import report_md  # noqa: E402
from bets import JST, Bet, RaceBets  # noqa: E402


def make_race(**kwargs):
    defaults = dict(
        race_id='202601020811',
        name='テストステークス',
        start_time='15:25',
        marks=[],
        bets=[],
        confidence='B',
        subjective_hit_rate=0.20,
    )
    defaults.update(kwargs)
    return RaceBets(**defaults)


def review(race, bet_odds=None, win_table=None, meta=None, conditions=None,
          forms=None, now=None, day=date(2026, 8, 12)):
    now = now or datetime(2026, 8, 12, 14, 30, tzinfo=JST)
    return discipline.review_race(
        race, bet_odds or [], win_table or {}, meta or {}, now, day,
        conditions, forms)


class FakeSheet:
    def __init__(self, races, day=date(2026, 8, 12), source='cloud-nar', generated_at=None):
        self.races = races
        self.date = day
        self.source = source
        self.generated_at = generated_at


def test_blocked_race_shows_block_reason():
    race = make_race(
        name='くろゆり賞', venue='笠松', race_no=11, org='nar',
        marks=[{'mark': '◎', 'umaban': 8}, {'mark': '○', 'umaban': 6}],
        bets=[Bet('ワイド', [8, 6])],
        subjective_hit_rate=0.30,
    )
    verdict = review(race, bet_odds=[1.5], conditions={'surface': 'ダート', 'distance': 1580})

    out = report_md.render(FakeSheet([race]), [verdict],
                            datetime(2026, 8, 12, 15, 23, tzinfo=JST))

    assert '見送り（BLOCK）' in out
    assert 'くろゆり賞' in out
    assert '笠松11R' in out
    assert '1.5倍' in out


def test_clear_race_shows_discipline_pass():
    race = make_race(
        marks=[{'mark': '◎', 'umaban': 7}],
        bets=[Bet('単勝', [7])],
        subjective_hit_rate=0.5,
    )
    verdict = review(race, bet_odds=[6.0], conditions={'surface': '芝', 'distance': 1800})

    out = report_md.render(FakeSheet([race]), [verdict],
                            datetime(2026, 8, 12, 15, 23, tzinfo=JST))

    assert '規律をすべてクリア' in out
    assert '6.0倍' in out


def test_no_bets_shows_neutral_not_discipline_pass():
    race = make_race(marks=[{'mark': '◎', 'umaban': 3}], bets=[])
    verdict = review(race)

    out = report_md.render(FakeSheet([race]), [verdict],
                            datetime(2026, 8, 12, 15, 23, tzinfo=JST))

    assert '規律をすべてクリア' not in out
    assert '見送り（買い目なし' in out


def test_render_missing_names_the_missing_file():
    out = report_md.render_missing(date(2026, 8, 12), datetime(2026, 8, 12, 10, 7, tzinfo=JST))
    assert '2026-08-12' in out
    assert '朝の買い目が届いていません' in out


def test_path_for_and_save_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(report_md, 'CHECKS_MD_DIR', str(tmp_path))
    path = report_md.path_for(date(2026, 8, 12))
    report_md.save('# hello', path)
    with open(path, encoding='utf-8') as f:
        assert f.read() == '# hello'


# ----------------------------------------------------------------------
# 発走済みのレース（2026-11-22 マイルCS：発走後の検算で買い目の表が空になった）
# ----------------------------------------------------------------------

def _started_verdict():
    race = make_race(
        name='マイルCS', venue='京都', race_no=11, start_time='15:40',
        marks=[{'mark': '◎', 'umaban': 5}],
        bets=[Bet('単勝', [5]), Bet('複勝', [5])],
    )
    verdict = review(race, meta={'skipped': '発走済みのため取得せず'},
                     now=datetime(2026, 8, 12, 15, 53, tzinfo=JST))
    return race, verdict


def test_発走済みでも買い目の行は消えず発走前の最後の検算のオッズで出る():
    race, verdict = _started_verdict()
    prestart = {race.race_id: {
        'checked_at': '2026-08-12T15:28:00+09:00',
        'bet_odds': [{'bet': str(race.bets[0]), 'odds': 7.4},
                     {'bet': str(race.bets[1]), 'odds': 2.9}],
        'conditions': {'surface': '芝', 'distance': 1600, 'going': '良'},
    }}

    out = report_md.render(FakeSheet([race]), [verdict],
                           datetime(2026, 8, 12, 15, 53, tzinfo=JST), prestart)

    assert '| 単勝 | 5 | 7.4倍 | 100円 |' in out
    assert '| 複勝 | 5 | 2.9倍 | 100円 |' in out
    assert '発走済み（発走前の最後の検算 15:28 のオッズ）' in out
    assert '芝1600m' in out and '馬場: 良' in out
    assert '未取得' not in out


def test_発走前の記録が無くても買い目の行は残る():
    race, verdict = _started_verdict()

    out = report_md.render(FakeSheet([race]), [verdict],
                           datetime(2026, 8, 12, 15, 53, tzinfo=JST))

    assert '| 単勝 | 5 | オッズ取得できず | 100円 |' in out
    assert '| 複勝 | 5 | オッズ取得できず | 100円 |' in out
    assert '発走済み（発走前の検算記録なし）' in out
