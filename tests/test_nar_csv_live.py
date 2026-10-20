#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nar_csv.live（公式サイトから直接取って地方のバイアスを判定する）の検証。ネットワークには接続しない。

押さえたいのは次の3点。
  - 確定前のレース（着順・コーナー通過順が空）を判定に混ぜないこと
  - 帯広ばんえいを混ぜないこと（第14章）
  - 前月が取れなくても当月だけで判定を出すこと
フィクスチャは形を借りた架空のレース（実在レースの着順ではない）。
"""

import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import nar  # noqa: E402
import nar_csv  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_remote():
    yield
    nar_csv._REMOTE = None


def _race(venue, ymd, no, corner='1,2,3,4,5,6', done=True):
    row = {'競馬場': venue, '競走年月日': ymd, 'レース番号': str(no), 'レース名': f'{no}R',
           '芝ダート区分': 'ダート', '距離': '1400', '天候': '晴', '馬場': '良',
           '条件': '', '上がり3F': '39.0' if done else '',
           'コーナー通過順1': corner if done else ''}
    for i in range(2, 9):
        row[f'コーナー通過順{i}'] = ''
    return row


def _horses(venue, ymd, no, done=True):
    return [{'競馬場': venue, '競走年月日': ymd, 'レース番号': str(no), '馬番': str(n),
             '馬名': f'馬{n}', '着順': str(n) if done else '', 'タイム': f'{1}{250 + n}' if done else '',
             '上がり3F': '39.0', '人気': str(n), '馬体重': '480', '馬体重増減': '0',
             '騎手名': 'x'} for n in range(1, 7)]


def _month(ymd):
    races = [_race('川崎', ymd, 1), _race('川崎', ymd, 2, done=False),
             _race('帯広ば', ymd, 1)]
    horses = _horses('川崎', ymd, 1) + _horses('川崎', ymd, 2, done=False) + _horses('帯広ば', ymd, 1)
    return {'racelist': races, 'horselist': horses, 'payback': []}


def test_live_judges_only_finished_non_banei_venues(capsys):
    def fetcher(kind, day):
        return _month('20261014')
    done = nar_csv.live('20261014', 'all', fetcher=fetcher)
    out = capsys.readouterr().out
    assert done == ['川崎']
    assert '■ 20261014 川崎' in out and '■ 20261014 帯広' not in out


def test_live_survives_missing_previous_month():
    calls = []

    def fetcher(kind, day):
        calls.append(day)
        if len(calls) > 1:
            raise nar.NarError('前月は取れない')
        return _month('20261014')
    # 1レースだけの架空データでは平常値が作れず判定不能になる。落ちずに空で返ればよい。
    assert nar_csv.live('20261014', '川崎', fetcher=fetcher) == []
    assert len(calls) == 2


def test_live_current_month_failure_propagates():
    def fetcher(kind, day):
        raise nar.NarError('当月が取れない')
    with pytest.raises(nar.NarError):
        nar_csv.live('20261014', 'all', fetcher=fetcher)


def test_live_no_finished_races(capsys):
    def fetcher(kind, day):
        return {'racelist': [_race('川崎', '20261014', 1, done=False)],
                'horselist': _horses('川崎', '20261014', 1, done=False), 'payback': []}
    assert nar_csv.live('20261014', 'all', fetcher=fetcher) == []
    assert '確定レースがありません' in capsys.readouterr().out


# ---- 検算メールへの接続（check.bias_lines）-------------------------------

SAVED = """# 測定時刻 2026-10-14T18:07:00+09:00
■ 20261014 園田  トラックバイアス判定（予想メソッド 第11章）
  R    距離 馬場       前半     基準差 ペース      上3F  1〜3着の脚質      4〜8着の脚質
  1   820 良      14.2   +0.05 平均      36.9  先/先/差            先/逃/追/差/追
  2  1400 良      55.5   +0.80 スロー     40.1  差/先/差            逃/追/追/追
  3  1400 良      53.0   +0.17 スロー     40.5  差/差/逃            先/追/差/追/追

→ 判定:
   ・★スローペースなのに差し追込が上位＝強烈な外差しバイアスの可能性

※本判定は統計的な目安です。

■ 20261014 名古屋  トラックバイアス判定（予想メソッド 第11章）
  1  1500 稍重     56.4   -0.17 ハイ      39.7  逃/先/先            差/差/追/差/追

→ 判定:
   ・顕著な矛盾なし。当日は脚質フラットと見るのが妥当
"""


def _write_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(nar_csv, 'BIAS_DIR', str(tmp_path))
    (tmp_path / '2026-10-14.txt').write_text(SAVED, encoding='utf-8')


def test_summarize_saved_picks_the_requested_venue(tmp_path, monkeypatch):
    _write_saved(tmp_path, monkeypatch)
    sono = nar_csv.summarize_saved('2026-10-14', '園田')
    assert sono['races'] == 3
    assert sono['measured_at'].startswith('2026-10-14T18:07')
    assert sono['verdicts'] == ['★スローペースなのに差し追込が上位＝強烈な外差しバイアスの可能性']
    assert nar_csv.summarize_saved('2026-10-14', '名古屋')['verdicts'][0].startswith('顕著な矛盾なし')
    assert nar_csv.summarize_saved('2026-10-14', '川崎') is None
    assert nar_csv.summarize_saved('2026-10-15', '園田') is None


def test_bias_lines_only_for_nar_and_never_touch_marks(tmp_path, monkeypatch):
    import bets
    import check
    _write_saved(tmp_path, monkeypatch)

    class R:  # RaceBets の代わり（必要な属性だけ）
        org = 'nar'
        venue = '園田'
    lines = check.bias_lines(R, datetime.date(2026, 10, 14))
    assert any('園田 3鞍で測定' in ln for ln in lines)
    assert any('★スローペースなのに' in ln for ln in lines)
    assert any('印は自動では動かしません' in ln for ln in lines)

    R.org = 'jra'
    assert check.bias_lines(R, datetime.date(2026, 10, 14)) == []
    R.org = 'nar'
    R.venue = '川崎'   # 保存に無い場は何も足さない
    assert check.bias_lines(R, datetime.date(2026, 10, 14)) == []
    assert bets  # import 確認のみ


def test_bias_lines_withholds_judgement_on_few_races(tmp_path, monkeypatch):
    import check
    _write_saved(tmp_path, monkeypatch)

    class R:
        org = 'nar'
        venue = '名古屋'
    lines = check.bias_lines(R, datetime.date(2026, 10, 14))
    assert lines == ['  当日バイアス: 判定に使えるレースが1鞍だけで、判断保留（18:07時点）']


def test_clean_summary_carries_bias_for_nar(tmp_path, monkeypatch):
    import check
    _write_saved(tmp_path, monkeypatch)

    class R:
        org = 'nar'
        venue = '園田'
        name = '園田のレース'

    class V:
        race = R
        composite = None
        expected_value = None
    now = datetime.datetime(2026, 10, 14, 18, 37)
    body = check.format_clean_summary([V], now, day=datetime.date(2026, 10, 14))
    assert '【園田のレース】' in body and '園田 3鞍で測定' in body
    assert check.format_clean_summary([V], now) == '18:37 直前検算 問題なし：園田のレース'


# ---- 週次レビューへの接続（review.nar_bias_report）---------------------------

def test_review_bias_report_lists_nar_days_only():
    import review
    day = {'date': datetime.date(2026, 10, 14), 'entries': [
        {'org': 'nar', 'venue': '川崎'}, {'org': 'nar', 'venue': '川崎'}, {'org': 'jra', 'venue': '東京'}]}
    jra_only = {'date': datetime.date(2026, 10, 17), 'entries': [{'org': 'jra', 'venue': '東京'}]}
    seen = []

    def measure(ymd, venues):
        seen.append((ymd, venues))
        return {'川崎': {'races': 12, 'verdicts': ['★スローなのに差し']}}
    lines, n = review.nar_bias_report([day, jra_only], measure=measure)
    assert seen == [('20261014', ['川崎'])] and n == 1
    assert any('| 2026-10-14 | 川崎 | 12鞍 | ★スローなのに差し |' == ln for ln in lines)


def test_review_bias_report_withholds_and_survives_failure():
    import review
    day = {'date': datetime.date(2026, 10, 14), 'entries': [
        {'org': 'nar', 'venue': '川崎'}, {'org': 'nar', 'venue': '園田'}]}

    def measure(ymd, venues):
        return {'川崎': {'races': 2, 'verdicts': ['x']}}
    lines, n = review.nar_bias_report([day], measure=measure)
    text = '\n'.join(lines)
    assert '判断保留（判定に使えるレースが2鞍）' in text
    assert '| 2026-10-14 | 園田 | — | 測れなかった' in text

    def boom(ymd, venues):
        raise RuntimeError('通信失敗')
    lines, n = review.nar_bias_report([day], measure=boom)   # 落ちない
    assert n == 2 and all('測れなかった' in ln for ln in lines if '園田' in ln or '川崎' in ln)
    assert review.nar_bias_report([], measure=boom) == ([], 0)
