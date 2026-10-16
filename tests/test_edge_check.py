#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主観 vs 確定オッズ・◎のオッズのずれ（edge_check.py）を架空のレースで確かめる。

実在レースの数字は使わない（test_calibration_check.py と同じ理由）。
"""

import math
from datetime import date, datetime

import pytest

import edge_check
import review

DAY = date(2026, 10, 18)


def _at(hh, mm):
    return datetime(2026, 10, 18, hh, mm, tzinfo=edge_check.JST)


def _record(checks, final, winner, wp, marks=None, start='15:00', day=DAY):
    return {'day': day, 'race_id': 'r1', 'org': 'jra', 'name': '架空S', 'start_time': start,
            'win_probabilities': wp, 'marks': marks or {}, 'checks': checks,
            'final': final, 'winner': winner}


def test_空の検算と時刻の無い記録は落とし古い順に並べる():
    history = [
        {'checked_at': '2026-10-18T13:07:00+09:00',
         'races': [{'race_id': 'r1', 'win_odds': {'1': 2.0, '2': 4.0}}]},
        {'checked_at': '2026-10-18T10:07:00+09:00',
         'races': [{'race_id': 'r1', 'win_odds': {'1': 2.5, '2': 3.0}},
                   {'race_id': 'r2', 'win_odds': {'1': 9.9}}]},
        {'checked_at': '2026-10-18T16:07:00+09:00',
         'races': [{'race_id': 'r1', 'win_odds': {}}]},
        {'races': [{'race_id': 'r1', 'win_odds': {'1': 1.1}}]},
    ]
    checks = edge_check.race_checks(history, 'r1')
    assert [c[0].hour for c in checks] == [10, 13]
    assert checks[-1][1] == {1: 2.0, 2: 4.0}


def test_確定結果から確定オッズと1着を取る():
    result = {'finishing_order': [
        {'rank': 1, 'umaban': 3, 'odds': 5.0},
        {'rank': 2, 'umaban': 1, 'odds': 2.0},
        {'rank': None, 'umaban': 7, 'odds': None},
    ]}
    assert edge_check.final_odds_and_winner(result) == ({3: 5.0, 1: 2.0}, 3)
    assert edge_check.final_odds_and_winner(None) == (None, None)


def test_勝ち馬を上書きで上げると主観の対数尤度が市場より0に近づく():
    seen = {1: 2.0, 2: 4.0, 3: 4.0}           # 市場勝率 0.5 / 0.25 / 0.25
    rec = _record([(_at(13, 7), seen)], final=dict(seen), winner=2, wp={2: 0.4})
    m_seen, subj, m_final = edge_check.race_logliks(rec)
    assert m_seen == pytest.approx(math.log(0.25))
    assert subj == pytest.approx(math.log(0.4))
    assert m_final == pytest.approx(math.log(0.25))


def test_取消で確定に居ない馬は除いて確率を作り直す():
    seen = {1: 2.0, 2: 4.0, 3: 4.0}
    rec = _record([(_at(13, 7), seen)], final={1: 2.0, 2: 2.0}, winner=2, wp={})
    m_seen, subj, m_final = edge_check.race_logliks(rec)
    assert m_seen == pytest.approx(math.log(1 / 3))   # 1と2だけで 0.5:0.25 を作り直す
    assert m_final == pytest.approx(math.log(0.5))
    assert subj == pytest.approx(m_seen)              # 上書きが無ければ市場そのまま


def test_勝ち馬が検算に居なければ数えない():
    rec = _record([(_at(13, 7), {1: 2.0, 2: 3.0})], final={1: 2.0, 2: 3.0, 9: 50.0},
                  winner=9, wp={})
    assert edge_check.race_logliks(rec) is None
    assert edge_check.loglik_summary([rec])['races'] == 0


def test_主観マイナス確定の平均と標準誤差():
    seen = {1: 2.0, 2: 4.0, 3: 4.0}
    a = _record([(_at(13, 7), seen)], final=dict(seen), winner=2, wp={2: 0.4})
    b = _record([(_at(13, 7), seen)], final=dict(seen), winner=1, wp={2: 0.4})
    s = edge_check.loglik_summary([a, b])
    da = math.log(0.4) - math.log(0.25)
    db = math.log(0.6 * 0.5 / 0.75 / (0.6 * 0.5 / 0.75 + 0.6 * 0.25 / 0.75 + 0.4)) - math.log(0.5)
    assert s['races'] == 2
    assert s['diff_mean'] == pytest.approx((da + db) / 2)
    assert s['diff_se'] is not None


def test_ずれは検算の時刻で分け最後の検算も別に数える():
    checks = [(_at(10, 7), {1: 4.0, 2: 2.0}),     # 発走173分前
              (_at(14, 7), {1: 3.0, 2: 2.0}),     # 53分前（最後の検算）
              ]
    rec = _record(checks, final={1: 3.0, 2: 2.5}, winner=1, wp={1: 0.4},
                  marks={1: '◎', 2: '○'})
    ratios = edge_check.drift_summary([rec])
    assert ratios['発走120分以上前'] == [pytest.approx(0.75)]
    assert ratios['発走60分前以内'] == [pytest.approx(1.0)]
    assert ratios['発走60〜120分前'] == []
    assert ratios['最後の検算'] == [pytest.approx(1.0)]


def test_発走時刻が読めなくても最後の検算だけは数える():
    rec = _record([(_at(10, 7), {1: 4.0})], final={1: 2.0}, winner=1, wp={},
                  marks={1: '◎'}, start='未定')
    ratios = edge_check.drift_summary([rec])
    assert ratios['最後の検算'] == [pytest.approx(0.5)]
    assert all(not ratios[b[2]] for b in edge_check.BUCKETS)


def test_出力に表とメール用の1行が出る():
    seen = {1: 2.0, 2: 4.0, 3: 4.0}
    rec = _record([(_at(14, 7), seen)], final={1: 1.8, 2: 4.0, 3: 5.0}, winner=1,
                  wp={1: 0.55}, marks={1: '◎'})
    summary = {'total': edge_check.loglik_summary([rec]),
               'week': edge_check.loglik_summary([]),
               'drift': {k: edge_check.describe(v)
                         for k, v in edge_check.drift_summary([rec]).items()}}
    text = '\n'.join(edge_check.render_lines(summary))
    assert '主観 − 確定' in text and '| レース数 | 1 | 0 |' in text
    assert '| 最後の検算 | 1 | 0.900 |' in text
    line = edge_check.mail_line(summary)
    assert line.startswith(f'主観 vs 確定オッズ（観察中）：1/{edge_check.READY_RACES}R')


def test_集計に失敗しても週次レビューは止めない(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('壊れた')
    monkeypatch.setattr(edge_check, 'analyze', boom)
    assert review.edge_report() == (None, None, None)
    assert review.render_edge(None) == []


def test_メールに観察中の1行が載る():
    week = review.summarize([])
    body = review.render_mail(week, (date(2026, 10, 12), date(2026, 10, 18)), 'x.md',
                              edge_line='主観 vs 確定オッズ（観察中）：60/200R')
    assert '主観 vs 確定オッズ（観察中）：60/200R' in body
    assert '主観 vs 確定オッズ' not in review.render_mail(
        week, (date(2026, 10, 12), date(2026, 10, 18)), 'x.md')


def _edge_summary(races, drift_n):
    return {'total': {'races': races}, 'drift': {'最後の検算': {'n': drift_n}}}


def test_保留中の改善案は目安に届いたものだけ到達になる():
    status = review.pending_status(_edge_summary(edge_check.READY_RACES, 10), 1499)
    by_key = {p['key']: p for p in status}
    assert by_key['blend']['ready'] and by_key['blend']['now'] == edge_check.READY_RACES
    assert not by_key['drift']['ready']
    assert not by_key['frame']['ready'] and by_key['frame']['now'] == 1499


def test_集計が無いときは量を出さず到達にもしない():
    status = review.pending_status(None, None)
    assert all(p['now'] is None and not p['ready'] for p in status)
    assert '| 案2 主観と市場の混ぜ合わせ | — |' in '\n'.join(review.render_pending(status))


def test_到達した案だけメール冒頭で知らせ進み具合は毎回1行出す():
    week = review.summarize([])
    status = review.pending_status(_edge_summary(60, edge_check.READY_DRIFT), 729)
    body = review.render_mail(week, (date(2026, 10, 12), date(2026, 10, 18)), 'x.md',
                              pending=status)
    assert '■ 保留中の改善案の検討どきです' in body
    assert '  - 案3 ◎のオッズのずれを期待値に織り込む：' in body
    assert '  - 案2 主観と市場の混ぜ合わせ：' not in body
    assert f'保留中の改善案：案2 60/{edge_check.READY_RACES}R' in body
    quiet = review.render_mail(week, (date(2026, 10, 12), date(2026, 10, 18)), 'x.md',
                               pending=review.pending_status(_edge_summary(60, 10), 729))
    assert '検討どき' not in quiet


def test_data_jraのレース数を数える(tmp_path):
    (tmp_path / '20261018_東京.json').write_text('[{"race_id": "1"}, {"race_id": "2"}]',
                                                 encoding='utf-8')
    (tmp_path / '20261018_京都.json').write_text('[{"race_id": "3"}]', encoding='utf-8')
    assert review.count_jra_races(str(tmp_path)) == 3
    (tmp_path / '20261019_新潟.json').write_text('壊れた', encoding='utf-8')
    assert review.count_jra_races(str(tmp_path)) is None
