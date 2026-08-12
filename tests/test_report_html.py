#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check.py の検算結果をHTMLに描き出す report_html.py の単体テスト。

メール（テキスト）はここでは変えない。ここで確かめるのは、GitHub Pages に
置く1枚絵が「印・買い目・BLOCK理由」を取りこぼさずに出せているかだけ。
"""

import json
import os
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bets  # noqa: E402
import discipline  # noqa: E402
import nar_card  # noqa: E402
import report_html  # noqa: E402
from bets import JST, Bet, BetSheet, RaceBets  # noqa: E402


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


def test_blocked_race_shows_block_pill_and_reasons():
    race = make_race(
        name='くろゆり賞', venue='笠松', race_no=11, org='nar',
        marks=[{'mark': '◎', 'umaban': 8}, {'mark': '○', 'umaban': 6}],
        bets=[Bet('ワイド', [8, 6])],
        subjective_hit_rate=0.30,
    )
    verdict = review(race, bet_odds=[1.5], conditions={'surface': 'ダート', 'distance': 1580})

    out = report_html.render(FakeSheet([race]), [verdict],
                             datetime(2026, 8, 12, 15, 23, tzinfo=JST))

    assert '見送り（BLOCK）' in out
    assert 'くろゆり賞' in out
    assert '笠松11R' in out
    assert '1.5倍' in out
    assert 'BLOCK' in out


def test_clean_race_shows_pass_pill_and_no_findings():
    race = make_race(
        name='クリーンS',
        marks=[{'mark': '◎', 'umaban': 7}],
        bets=[Bet('単勝', [7])],
        subjective_hit_rate=0.5,
    )
    verdict = review(race, bet_odds=[6.0], conditions={'surface': '芝', 'distance': 1800})

    out = report_html.render(FakeSheet([race]), [verdict],
                             datetime(2026, 8, 12, 10, 0, tzinfo=JST))

    assert 'pill-good' in out
    assert '規律をすべてクリア' in out


def test_confidence_c_race_shows_no_bets_placeholder():
    race = make_race(name='見送りレース', confidence='C',
                     marks=[{'mark': '◎', 'umaban': 3}], bets=[])
    verdict = review(race)

    out = report_html.render(FakeSheet([race]), [verdict],
                             datetime(2026, 8, 12, 10, 0, tzinfo=JST))

    assert '買い目なし' in out
    assert 'pill-neutral' in out


def test_missing_odds_shows_provisional_note_not_a_crash():
    race = make_race(
        marks=[{'mark': '◎', 'umaban': 1}],
        bets=[Bet('馬連', [1, 2])],
        subjective_hit_rate=0.3,
    )
    verdict = review(race, bet_odds=[None], meta={'status': 'yoso'})

    out = report_html.render(FakeSheet([race]), [verdict],
                             datetime(2026, 8, 12, 9, 0, tzinfo=JST))

    assert '予想オッズ（発売前）' in out
    assert 'オッズ取得できず' in out


def test_nar_horse_names_are_looked_up_from_the_local_card(tmp_path, monkeypatch):
    monkeypatch.setattr(nar_card, 'CARDS_DIR', str(tmp_path))
    card = {
        'date': '2026-08-12',
        'races': [{
            'race_id': '202608124711',
            'entries': [
                {'umaban': 8, 'name': 'マルヨハルキ'},
                {'umaban': 6, 'name': 'キャッシュブリッツ'},
            ],
        }],
    }
    with open(nar_card.card_path(date(2026, 8, 12)), 'w', encoding='utf-8') as f:
        json.dump(card, f, ensure_ascii=False)

    race = make_race(
        race_id='202608124711', org='nar', venue='笠松', race_no=11,
        marks=[{'mark': '◎', 'umaban': 8}, {'mark': '○', 'umaban': 6}],
    )
    verdict = review(race, day=date(2026, 8, 12))

    out = report_html.render(FakeSheet([race], day=date(2026, 8, 12)), [verdict],
                             datetime(2026, 8, 12, 9, 0, tzinfo=JST))

    assert 'マルヨハルキ' in out
    assert 'キャッシュブリッツ' in out


def test_missing_sheet_page_names_the_expected_file():
    out = report_html.render_missing(date(2026, 8, 12), datetime(2026, 8, 12, 10, 7, tzinfo=JST))
    assert '2026-08-12.json' in out
    assert '朝の買い目が届いていません' in out


def test_save_writes_to_html_path(tmp_path, monkeypatch):
    target = tmp_path / 'nested' / 'index.html'
    monkeypatch.setattr(report_html, 'HTML_PATH', str(target))

    path = report_html.save('<html></html>')

    assert path == str(target)
    assert target.read_text(encoding='utf-8') == '<html></html>'


@pytest.mark.parametrize('text', ['<script>alert(1)</script>', 'A & B "quoted"'])
def test_race_note_is_escaped(text):
    race = make_race(marks=[{'mark': '◎', 'umaban': 1}], note=text)
    verdict = review(race)

    out = report_html.render(FakeSheet([race]), [verdict],
                             datetime(2026, 8, 12, 10, 0, tzinfo=JST))

    assert '<script>' not in out
    assert html_escaped(text) in out


def html_escaped(text):
    import html
    return html.escape(text)


# ----------------------------------------------------------------------
# 過去の見返し（render_history）
# ----------------------------------------------------------------------

def test_render_history_uses_the_last_of_several_checks_that_day(tmp_path, monkeypatch):
    """1日に複数回検算した日は、直近（最後）の判定を見せること。"""
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path / 'bets'))
    monkeypatch.setattr(bets, 'CHECKS_DIR', str(tmp_path / 'checks'))

    race = make_race(
        name='見返しテストS',
        marks=[{'mark': '◎', 'umaban': 1}],
        bets=[Bet('単勝', [1])],
        subjective_hit_rate=0.4,
    )
    bets.save_sheet(BetSheet(date=date(2026, 8, 12), races=[race], source='cloud-nar'))

    # 1回目（朝・発売前）: BLOCKなし判定
    bets.save_check_result(date(2026, 8, 12), {
        'checked_at': '2026-08-12T09:12:00+09:00',
        'races': [{'race_id': race.race_id, 'blocked': False,
                   'composite_odds': None, 'expected_value': None,
                   'bet_odds': [{'bet': '単勝 1', 'odds': None}],
                   'findings': [], 'conditions': {}, 'odds_meta': {}}],
    })
    # 2回目（直前・実オッズ）: BLOCK判定
    bets.save_check_result(date(2026, 8, 12), {
        'checked_at': '2026-08-12T15:23:00+09:00',
        'races': [{'race_id': race.race_id, 'blocked': True,
                   'composite_odds': 1.5, 'expected_value': 0.6,
                   'bet_odds': [{'bet': '単勝 1', 'odds': 1.5}],
                   'findings': [{'severity': 'BLOCK', 'code': 'x',
                                 'message': '期待値未達', 'remedy': ''}],
                   'conditions': {}, 'odds_meta': {}}],
    })

    out = report_html.render_history(date(2026, 8, 12))

    assert '見送り（BLOCK）' in out
    assert '期待値未達' in out
    assert '1.5倍' in out


def test_render_history_without_any_check_still_shows_marks(tmp_path, monkeypatch):
    """買い目はあるが検算がまだ無い日でも、印だけは表示すること（クラッシュしない）。"""
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path / 'bets'))
    monkeypatch.setattr(bets, 'CHECKS_DIR', str(tmp_path / 'checks'))

    race = make_race(name='未検算レース', marks=[{'mark': '◎', 'umaban': 5}])
    bets.save_sheet(BetSheet(date=date(2026, 8, 12), races=[race]))

    out = report_html.render_history(date(2026, 8, 12))

    assert '未検算レース' in out
    assert '5番' in out


def test_render_history_with_no_bets_file_shows_no_record(tmp_path, monkeypatch):
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path / 'bets'))
    monkeypatch.setattr(bets, 'CHECKS_DIR', str(tmp_path / 'checks'))

    out = report_html.render_history(date(2026, 8, 12))

    assert '記録が見つかりません' in out
