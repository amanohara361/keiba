#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""検証ノートに記録された実際の損失を、規律チェックが検出できるか確かめる。

ここに並ぶケースは架空の例ではなく、2026年7〜8月に実際に起きた買い目構成ミスである。
同じ形が再発したら必ず落ちるように固定しておく。ネットワークには接続しない。
"""

import os
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bets  # noqa: E402
import check  # noqa: E402
import discipline  # noqa: E402
import conditions as conditions_module  # noqa: E402
import odds as odds_module  # noqa: E402
import report_html  # noqa: E402
from bets import JST, Bet, BetSheet, BetsError, RaceBets, parse_sheet  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_html_report(tmp_path, monkeypatch):
    """HTMLレポートの書き出し先を一時ディレクトリへ逃がす。実リポジトリの docs/ を汚さない。"""
    monkeypatch.setattr(report_html, 'HTML_PATH', str(tmp_path / '_report' / 'index.html'))


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


def marks_of(**pairs):
    """marks_of(◎=7, ○=11) のようには書けないので順に渡す。"""
    return [{'mark': m, 'umaban': n} for m, n in pairs['items']]


def review(race, bet_odds=None, win_table=None, now=None, day=date(2026, 8, 2)):
    now = now or datetime(2026, 8, 2, 14, 30, tzinfo=JST)
    return discipline.review_race(
        race, bet_odds or [], win_table or {}, {}, now, day)


# ----------------------------------------------------------------------
# 実際に起きた損失
# ----------------------------------------------------------------------

def test_queen_stakes_mark_contradiction_is_blocked():
    """2026-08-02 クイーンS：○を外し▲を残した買い目で回収0円になった件。

    印3頭（◎7・○11・△14）がそのまま1〜3着を独占しながら、
    ○11を「期待値1.01で基準未達」として棄却し▲2（8着）を相手に残したため、
    的中組合せ 馬連7-11=1,120円 を含まず全滅した。
    """
    race = make_race(
        name='クイーンステークス',
        marks=marks_of(items=[('◎', 7), ('○', 11), ('▲', 2), ('△', 3), ('△', 14)]),
        bets=[
            Bet('馬連', [7, 2]),
            Bet('馬連', [7, 3]),
            Bet('馬連', [7, 14]),
        ],
        subjective_hit_rate=0.16,
    )

    verdict = review(race, bet_odds=[13.1, 23.1, 10.7])

    assert verdict.blocked
    codes = [f.code for f in verdict.blocks]
    assert 'mark_contradiction' in codes

    finding = next(f for f in verdict.blocks if f.code == 'mark_contradiction')
    assert '○11番' in finding.message      # 外れている上位印
    assert '▲2番' in finding.message       # 残っている下位印
    assert 'ワイド' in finding.remedy       # 第13章の解消案2


def test_tokai_stakes_lone_partner_is_warned():
    """2026-07-26 東海S：相手を○1頭に固定し、○の2着抜けで全滅した件。"""
    race = make_race(
        name='東海ステークス',
        marks=marks_of(items=[('◎', 5), ('○', 11), ('▲', 14)]),
        bets=[Bet('馬連', [5, 11])],
        subjective_hit_rate=0.30,
    )

    verdict = review(race, bet_odds=[12.0])

    codes = [f.code for f in verdict.warnings]
    assert 'lone_partner' in codes
    finding = next(f for f in verdict.warnings if f.code == 'lone_partner')
    assert '2着抜け' in finding.remedy


def test_sekigahara_marked_only_trifecta_is_warned():
    """2026-07-25 関ケ原S：印4頭の3連複ボックスが、無印馬の2着で全滅した件。"""
    race = make_race(
        name='関ケ原ステークス',
        marks=marks_of(items=[('◎', 1), ('○', 6), ('▲', 7), ('△', 9)]),
        bets=[
            Bet('3連複', [1, 6, 7]),
            Bet('3連複', [1, 6, 9]),
            Bet('3連複', [1, 7, 9]),
            Bet('3連複', [6, 7, 9]),
        ],
        subjective_hit_rate=0.25,
    )

    verdict = review(race, bet_odds=[20.0, 25.0, 30.0, 40.0])

    warned = [f for f in verdict.warnings if f.code == 'marked_only_trifecta']
    assert len(warned) == 4      # 4点とも印馬だけで組まれている
    assert '中穴' in warned[0].remedy


def test_ibis_summer_dash_win_odds_below_range_is_warned():
    """2026-08-02 アイビスSD：◎が朝4.3倍→確定3.8倍でレンジ下限を割った件。

    定刻14:30に稼働していれば200円の投資を見送れた、と検証ノートが明記している。
    """
    race = make_race(
        name='アイビスサマーダッシュ',
        marks=marks_of(items=[('◎', 16), ('○', 6)]),
        bets=[Bet('単勝', [16], stake=200)],
        subjective_hit_rate=0.29,
        confidence='A',
    )

    verdict = review(race, bet_odds=[3.8], win_table={16: (3.8, 2), 6: (2.7, 1)})

    codes = [f.code for f in verdict.warnings]
    assert 'win_odds_below_range' in codes
    # 期待値 3.8 × 0.29 = 1.10 < 1.2 なので発注も止まる
    assert verdict.blocked
    assert 'expected_value_below_minimum' in [f.code for f in verdict.blocks]


def test_morning_odds_would_have_passed_the_same_race():
    """同じアイビスSDでも、朝の4.3倍なら規律を通っていたこと。

    「オッズが動いたから止まった」という因果を固定しておく。
    """
    race = make_race(
        name='アイビスサマーダッシュ',
        marks=marks_of(items=[('◎', 16), ('○', 6)]),
        bets=[Bet('単勝', [16], stake=200)],
        subjective_hit_rate=0.29,
        confidence='A',
    )

    verdict = review(race, bet_odds=[4.3], win_table={16: (4.3, 2)})

    assert not verdict.blocked
    assert verdict.expected_value == pytest.approx(4.3 * 0.29, abs=0.01)


def test_already_started_race_is_blocked():
    """2026-08-01：◎2鞍とも1着だが配信が発走後で購入できなかった件。"""
    race = make_race(name='STV賞', start_time='15:25', bets=[Bet('単勝', [7])])

    verdict = review(race, bet_odds=[8.6],
                     now=datetime(2026, 8, 2, 20, 30, tzinfo=JST))

    assert verdict.blocked
    assert 'already_started' in [f.code for f in verdict.blocks]


# ----------------------------------------------------------------------
# 規律の算術
# ----------------------------------------------------------------------

def test_composite_odds_formula():
    """合成オッズ = 1 ÷ Σ(1/各オッズ)"""
    assert discipline.composite_odds([10.0, 10.0]) == pytest.approx(5.0)
    assert discipline.composite_odds([25.0, 20.0, 18.0]) == pytest.approx(6.87, abs=0.01)
    assert discipline.composite_odds([]) is None
    # 取得できなかった点は計算から除く
    assert discipline.composite_odds([10.0, None]) == pytest.approx(10.0)


def test_king_george_composite_was_actually_below_the_minimum():
    """2026-07-25 キングジョージ：推定オッズでの合成判定が甘かった件。

    推定（25/20/18倍＋単勝7.0倍）では約3.47倍で「クリア」としたが、
    確定ベースでは3.0倍を下回っていたと検証ノートが結論づけている。
    """
    estimated = discipline.composite_odds([25.0, 20.0, 18.0, 7.0])
    confirmed = discipline.composite_odds([8.3, 12.0, 10.0, 7.0])

    assert estimated > discipline.MIN_COMPOSITE_ODDS
    assert confirmed < discipline.MIN_COMPOSITE_ODDS


def test_composite_below_minimum_is_blocked():
    race = make_race(bets=[Bet('ワイド', [7, 11]), Bet('ワイド', [7, 14])])
    verdict = review(race, bet_odds=[4.5, 5.0])

    assert verdict.blocked
    assert 'composite_below_minimum' in [f.code for f in verdict.blocks]


def test_confidence_c_must_not_carry_bets():
    race = make_race(confidence='C', bets=[Bet('単勝', [1])])
    verdict = review(race, bet_odds=[5.0])

    assert 'confidence_c_has_bets' in [f.code for f in verdict.blocks]


def test_missing_odds_downgrades_to_provisional():
    """実オッズが取れなければ「暫定」として警告する（第13章 実オッズ優先の原則）。"""
    race = make_race(bets=[Bet('馬連', [1, 2])])
    verdict = review(race, bet_odds=[None])

    codes = [f.code for f in verdict.findings]
    assert 'no_odds' in codes
    assert '暫定' in next(f for f in verdict.findings if f.code == 'no_odds').remedy


def test_clean_bet_set_passes():
    race = make_race(
        marks=marks_of(items=[('◎', 7), ('○', 11), ('▲', 2)]),
        bets=[Bet('馬連', [7, 11]), Bet('馬連', [7, 2])],
        subjective_hit_rate=0.30,
    )
    verdict = review(race, bet_odds=[11.2, 13.1])

    assert not verdict.blocked
    assert verdict.warnings == []
    assert verdict.composite == pytest.approx(6.04, abs=0.01)
    assert verdict.expected_value == pytest.approx(1.81, abs=0.01)


def test_wide_hedge_suppresses_the_lone_partner_warning():
    """相手1頭でも、第13章が指示する◎絡みのワイド保険があれば警告しない。"""
    race = make_race(
        marks=marks_of(items=[('◎', 7), ('○', 11), ('△', 14)]),
        bets=[Bet('馬連', [7, 11]), Bet('ワイド', [7, 14])],
        subjective_hit_rate=0.35,
    )
    verdict = review(race, bet_odds=[11.2, 9.0])

    assert 'lone_partner' not in [f.code for f in verdict.warnings]


# ----------------------------------------------------------------------
# オッズAPIのキー変換
# ----------------------------------------------------------------------

@pytest.mark.parametrize('numbers,bet_type,expected', [
    ([1], '単勝', '01'),
    ([1, 3, 8], '3連複', '010308'),
    ([8, 3, 1], '3連複', '010308'),     # 昇順に揃える
    ([7, 11], '馬連', '0711'),
    ([11, 7], '馬連', '0711'),
    ([3, 1], '馬単', '0301'),           # 馬単は着順が意味を持つので並べ替えない
])
def test_key_of(numbers, bet_type, expected):
    assert odds_module.key_of(numbers, bet_type) == expected


def test_scratched_horse_is_not_treated_as_odds():
    table = {'01': ['2.9', '3.1', '1'], '02': ['-3.0', '0', '0']}
    assert odds_module.odds_for(table, [1], '単勝') == 2.9
    assert odds_module.odds_for(table, [2], '単勝') is None
    assert 2 not in odds_module.win_odds_table(table)


def test_collect_fetches_each_type_once():
    race = make_race(bets=[
        Bet('馬連', [7, 11]), Bet('馬連', [7, 2]), Bet('単勝', [7]),
    ])
    calls = []

    def fake_fetch(race_id, bet_type):
        calls.append(bet_type)
        tables = {
            '単勝': {'07': ['2.9', '3.0', '1']},
            '馬連': {'0711': ['11.2', '11.5', '3'], '0207': ['13.1', '13.4', '5']},
        }
        return {'status': 'middle', 'reason': None,
                'official_datetime': '14:30:00', 'odds': tables[bet_type]}

    bet_odds, win_table, meta = odds_module.collect(race, fetcher=fake_fetch)

    assert sorted(calls) == ['単勝', '馬連']     # 券種ごとに1回だけ
    assert bet_odds == [11.2, 13.1, 2.9]
    assert win_table[7] == (2.9, 1)
    assert meta['status'] == 'middle'


def test_collect_survives_a_failing_type():
    race = make_race(bets=[Bet('3連複', [1, 2, 3])])

    def flaky(race_id, bet_type):
        if bet_type == '3連複':
            raise odds_module.OddsError('3連複が取れません')
        return {'status': 'middle', 'reason': None,
                'official_datetime': '14:30:00', 'odds': {'01': ['2.9', '3.0', '1']}}

    bet_odds, win_table, meta = odds_module.collect(race, fetcher=flaky)

    assert bet_odds == [None]
    assert meta['errors']
    assert win_table[1] == (2.9, 1)


# ----------------------------------------------------------------------
# データ契約
# ----------------------------------------------------------------------

def test_parse_sheet_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path))

    sheet = BetSheet(
        date=date(2026, 8, 2),
        races=[make_race(
            marks=marks_of(items=[('◎', 7), ('○', 11)]),
            bets=[Bet('馬連', [7, 11], stake=100)],
        )],
        generated_at='2026-08-02T07:31:00+09:00',
        source='cowork',
    )

    path = bets.save_sheet(sheet)
    loaded = bets.load_sheet(date(2026, 8, 2))

    assert loaded.source == 'cowork'
    assert loaded.races[0].horses_for('◎') == [7]
    assert str(loaded.races[0].bets[0]) == '馬連 7-11'
    assert '馬連' in open(path, encoding='utf-8').read()


def test_missing_sheet_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path))
    assert bets.load_sheet(date(2026, 8, 2)) is None


@pytest.mark.parametrize('payload,expected', [
    ({'races': []}, 'date'),
    ({'date': '2026/08/02', 'races': []}, 'YYYY-MM-DD'),
    ({'date': '2026-08-02', 'races': [{'race_id': '123'}]}, 'race_id'),
    ({'date': '2026-08-02',
      'races': [{'race_id': '202601020811', 'confidence': 'Z'}]}, '勝負度'),
    ({'date': '2026-08-02',
      'races': [{'race_id': '202601020811',
                 'bets': [{'type': '3連単', 'horses': [1, 2, 3]}]}]}, '券種'),
    ({'date': '2026-08-02',
      'races': [{'race_id': '202601020811',
                 'marks': [{'mark': '×', 'umaban': 1}]}]}, '印'),
])
def test_contract_violations_are_rejected(payload, expected):
    with pytest.raises(bets.BetsError) as excinfo:
        parse_sheet(payload)
    assert expected in str(excinfo.value)


# ----------------------------------------------------------------------
# 通し実行
# ----------------------------------------------------------------------

def test_missing_morning_sheet_alerts_and_fails(tmp_path, monkeypatch, capsys):
    """朝の買い目が無い日は、警報を出して異常終了すること。"""
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path))

    exit_code = check.main(['--date', '2026-08-02', '--now', '2026-08-02T14:30',
                            '--no-email', '--no-save'])

    assert exit_code == check.EXIT_NEEDS_ATTENTION
    output = capsys.readouterr().out
    assert '朝の買い目が届いていません' in output
    assert '2026-08-01' in output      # 何が起きたかの説明が入っている


def test_end_to_end_blocks_the_queen_stakes_bet(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path / 'bets'))
    monkeypatch.setattr(bets, 'CHECKS_DIR', str(tmp_path / 'checks'))

    bets.save_sheet(BetSheet(
        date=date(2026, 8, 2),
        races=[make_race(
            name='クイーンステークス',
            venue='札幌', race_no=11,
            marks=marks_of(items=[('◎', 7), ('○', 11), ('▲', 2), ('△', 14)]),
            bets=[Bet('馬連', [7, 2]), Bet('馬連', [7, 14])],
            subjective_hit_rate=0.16,
        )],
        generated_at='2026-08-02T07:31:00+09:00',
    ))

    def fake_fetch(race_id, bet_type):
        tables = {
            '単勝': {'07': ['2.9', '3.0', '1'], '11': ['8.8', '9.0', '3']},
            '馬連': {'0207': ['13.1', '13.4', '5'], '0714': ['10.7', '11.0', '4']},
        }
        return {'status': 'middle', 'reason': None,
                'official_datetime': '14:28:00', 'odds': tables[bet_type]}

    monkeypatch.setattr(odds_module, 'fetch', fake_fetch)
    monkeypatch.setattr(conditions_module, 'fetch',
                        lambda rid, **kw: {'going': '良', 'weather': '晴',
                                          'surface': '芝', 'distance': 1800})

    exit_code = check.main(['--date', '2026-08-02', '--now', '2026-08-02T14:30',
                            '--no-email'])

    assert exit_code == check.EXIT_NEEDS_ATTENTION
    output = capsys.readouterr().out
    assert '発注を止めました' in output
    assert '○11番' in output
    assert '発売中の途中経過' in output

    # 検算の記録が残ること
    saved = (tmp_path / 'checks' / '2026-08-02.json')
    assert saved.exists()
    assert 'mark_contradiction' in saved.read_text(encoding='utf-8')


def test_end_to_end_passes_a_clean_sheet(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path / 'bets'))
    monkeypatch.setattr(bets, 'CHECKS_DIR', str(tmp_path / 'checks'))

    bets.save_sheet(BetSheet(
        date=date(2026, 8, 2),
        races=[make_race(
            name='クイーンステークス',
            marks=marks_of(items=[('◎', 7), ('○', 11), ('△', 14)]),
            bets=[Bet('馬連', [7, 11]), Bet('ワイド', [7, 14])],
            subjective_hit_rate=0.35,
        )],
    ))

    def fake_fetch(race_id, bet_type):
        tables = {
            '単勝': {'07': ['2.9', '3.0', '1']},
            '馬連': {'0711': ['11.2', '11.5', '3']},
            'ワイド': {'0714': ['9.0', '9.4', '4']},
        }
        return {'status': 'middle', 'reason': None,
                'official_datetime': '14:28:00', 'odds': tables[bet_type]}

    monkeypatch.setattr(odds_module, 'fetch', fake_fetch)
    monkeypatch.setattr(conditions_module, 'fetch',
                        lambda rid, **kw: {'going': '良', 'weather': '晴',
                                          'surface': '芝', 'distance': 1800})

    exit_code = check.main(['--date', '2026-08-02', '--now', '2026-08-02T14:30',
                            '--no-email', '--no-save'])

    assert exit_code == check.EXIT_OK
    assert '規律をクリア' in capsys.readouterr().out


def test_clean_check_sends_a_short_one_line_email(tmp_path, monkeypatch):
    """規律クリアの回もメールは送るが、フルレポートではなく1行サマリにすること。

    完全に無音にすると「規律クリア」と「まだ実行されていない／遅延中」の
    区別が受信側でつかない（2026-08-13、定時実行の遅延と重なって誤認させた）。
    """
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path / 'bets'))
    monkeypatch.setattr(bets, 'CHECKS_DIR', str(tmp_path / 'checks'))

    sent = []
    monkeypatch.setattr(check.Mailer, 'is_configured', lambda self: True)
    monkeypatch.setattr(check.Mailer, 'send',
                        lambda self, subject, body: sent.append(subject) or True)

    bets.save_sheet(BetSheet(
        date=date(2026, 8, 2),
        races=[make_race(
            name='クイーンステークス',
            marks=marks_of(items=[('◎', 7), ('○', 11), ('△', 14)]),
            bets=[Bet('馬連', [7, 11]), Bet('ワイド', [7, 14])],
            subjective_hit_rate=0.35,
        )],
    ))

    def fake_fetch(race_id, bet_type):
        tables = {
            '単勝': {'07': ['2.9', '3.0', '1']},
            '馬連': {'0711': ['11.2', '11.5', '3']},
            'ワイド': {'0714': ['9.0', '9.4', '4']},
        }
        return {'status': 'middle', 'reason': None,
                'official_datetime': '14:28:00', 'odds': tables[bet_type]}

    monkeypatch.setattr(odds_module, 'fetch', fake_fetch)
    monkeypatch.setattr(conditions_module, 'fetch',
                        lambda rid, **kw: {'going': '良', 'weather': '晴',
                                          'surface': '芝', 'distance': 1800})

    exit_code = check.main(['--date', '2026-08-02', '--now', '2026-08-02T14:30', '--no-save'])

    assert exit_code == check.EXIT_OK
    assert sent == ['直前検算 問題なし（14:30）']


def test_blocked_check_still_sends_an_email(tmp_path, monkeypatch):
    """発注を止めた回は、これまでどおり必ずメールすること。"""
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path / 'bets'))
    monkeypatch.setattr(bets, 'CHECKS_DIR', str(tmp_path / 'checks'))

    sent = []
    monkeypatch.setattr(check.Mailer, 'is_configured', lambda self: True)
    monkeypatch.setattr(check.Mailer, 'send',
                        lambda self, subject, body: sent.append(subject) or True)

    bets.save_sheet(BetSheet(
        date=date(2026, 8, 2),
        races=[make_race(
            name='クイーンステークス',
            venue='札幌', race_no=11,
            marks=marks_of(items=[('◎', 7), ('○', 11), ('▲', 2), ('△', 14)]),
            bets=[Bet('馬連', [7, 2]), Bet('馬連', [7, 14])],
            subjective_hit_rate=0.16,
        )],
    ))

    def fake_fetch(race_id, bet_type):
        tables = {
            '単勝': {'07': ['2.9', '3.0', '1'], '11': ['8.8', '9.0', '3']},
            '馬連': {'0207': ['13.1', '13.4', '5'], '0714': ['10.7', '11.0', '4']},
        }
        return {'status': 'middle', 'reason': None,
                'official_datetime': '14:28:00', 'odds': tables[bet_type]}

    monkeypatch.setattr(odds_module, 'fetch', fake_fetch)
    monkeypatch.setattr(conditions_module, 'fetch',
                        lambda rid, **kw: {'going': '良', 'weather': '晴',
                                          'surface': '芝', 'distance': 1800})

    exit_code = check.main(['--date', '2026-08-02', '--now', '2026-08-02T14:30', '--no-save'])

    assert exit_code == check.EXIT_NEEDS_ATTENTION
    assert sent == ['【要確認】直前検算で発注を止めた買い目があります']


def test_deliver_logs_failure(caplog, monkeypatch):
    """メール送信の成否が必ずログに残ること。

    実行ログに出ないと、SMTPが落ちても気づけない。
    """
    class Broken:
        def is_configured(self): return True
        def send(self, subject, body): return False

    with caplog.at_level('ERROR'):
        assert check.deliver(Broken(), '件名', '本文') is False
    assert 'メール送信に失敗' in caplog.text


def test_deliver_reports_missing_credentials(caplog):
    class Unconfigured:
        def is_configured(self): return False
        def send(self, subject, body): return True

    with caplog.at_level('ERROR'):
        assert check.deliver(Unconfigured(), '件名', '本文') is False
    assert 'GitHub Secrets' in caplog.text


# ----------------------------------------------------------------------
# 終了コードの意味
# ----------------------------------------------------------------------

def test_missing_sheet_is_not_a_failure_when_the_alert_was_delivered(tmp_path, monkeypatch):
    """買い目が無い日を「ジョブの失敗」にしないこと。

    毎回赤くなると赤に慣れてしまい、本当の障害が同じ色に埋もれる。
    検知して知らせられたなら、このジョブは役目を果たしている。
    """
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path))
    monkeypatch.setattr(check.Mailer, 'is_configured', lambda self: True)
    monkeypatch.setattr(check.Mailer, 'send', lambda self, subject, body: True)

    exit_code = check.main(['--date', '2026-08-02', '--now', '2026-08-02T14:30',
                            '--no-save'])

    assert exit_code == check.EXIT_NEEDS_ATTENTION


def test_missing_sheet_is_a_failure_when_the_alert_could_not_be_sent(tmp_path, monkeypatch):
    """知らせられなかったときだけ赤くすること。"""
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path))
    monkeypatch.setattr(check.Mailer, 'is_configured', lambda self: True)
    monkeypatch.setattr(check.Mailer, 'send', lambda self, subject, body: False)

    exit_code = check.main(['--date', '2026-08-02', '--now', '2026-08-02T14:30',
                            '--no-save'])

    assert exit_code == check.EXIT_ERROR


# ----------------------------------------------------------------------
# 手動作成ツール（Claudeが動かないときの代替経路）
# ----------------------------------------------------------------------

def test_make_bets_output_is_valid_for_the_checker(tmp_path, monkeypatch):
    """make_bets.py が作ったJSONを check.py がそのまま読めること。

    アプリが固まった日でも、この経路だけで検算まで繋がる必要がある。
    """
    import make_bets

    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path))

    sheet = BetSheet(
        date=date(2026, 8, 8),
        races=[RaceBets(
            race_id='202601020811', name='クイーンステークス', start_time='15:25',
            marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11}],
            bets=[Bet('馬連', [7, 11], 100)],
            confidence='B', subjective_hit_rate=0.35,
            venue='札幌', race_no=11,
        )],
        generated_at=bets.now_jst().isoformat(),
        source='manual',
    )
    bets.save_sheet(sheet)

    loaded = bets.load_sheet(date(2026, 8, 8))
    assert loaded.source == 'manual'          # 朝タスク作成分と区別できる
    assert loaded.races[0].subjective_hit_rate == 0.35
    assert make_bets.VENUES['01'] == '札幌'


@pytest.mark.parametrize('line,expected_type,expected_horses', [
    ('馬連 7-11 100', '馬連', [7, 11]),
    ('3連複 1-3-8', '3連複', [1, 3, 8]),
    ('３連複 1-3-8', '3連複', [1, 3, 8]),   # 全角の3も受ける
    ('単勝 16 200', '単勝', [16]),
])
def test_make_bets_parses_bet_lines(line, expected_type, expected_horses, monkeypatch):
    import make_bets

    supplied = iter([line, ''])
    monkeypatch.setattr('builtins.input', lambda _: next(supplied))

    entries = make_bets.ask_bets()
    assert entries[0].type == expected_type
    assert entries[0].horses == expected_horses


def test_make_bets_parses_marks(monkeypatch):
    import make_bets

    monkeypatch.setattr('builtins.input', lambda _: '◎7 ○11 ▲2 △3 △14')
    marks = make_bets.ask_marks()

    assert [m['mark'] for m in marks] == ['◎', '○', '▲', '△', '△']
    assert [m['umaban'] for m in marks] == [7, 11, 2, 3, 14]


@pytest.mark.parametrize('raw,expected', [
    ('0.16', 0.16),
    ('16%', 0.16),
    ('35', 0.35),      # 1より大きければ百分率とみなす
])
def test_make_bets_accepts_hit_rate_in_either_form(raw, expected, monkeypatch):
    import make_bets

    monkeypatch.setattr('builtins.input', lambda _: raw)
    assert make_bets.ask_rate() == pytest.approx(expected)


# ----------------------------------------------------------------------
# まとめて貼り付ける経路
# ----------------------------------------------------------------------

PASTED = """202601020811 クイーンステークス 15:25 B 35%
◎7 ○11 ▲2 △3 △14
馬連 7-11 100
ワイド 7-14 100

202604020207 アイビスサマーダッシュ 15:45 A 29%
◎16 ○6
単勝 16 200
"""


def test_parse_text_reads_multiple_races():
    import make_bets

    races = make_bets.parse_text(PASTED)

    assert len(races) == 2
    first, second = races
    assert first.name == 'クイーンステークス'
    assert first.venue == '札幌' and first.race_no == 11
    assert first.start_time == '15:25'
    assert first.confidence == 'B'
    assert first.subjective_hit_rate == 0.35
    assert first.horses_for('◎') == [7]
    assert [str(b) for b in first.bets] == ['馬連 7-11', 'ワイド 7-14']
    assert second.bets[0].stake == 200


@pytest.mark.parametrize('header', [
    '202601020811 クイーンステークス 15:25 B 35%',
    '15:25 202601020811 B 35% クイーンステークス',   # 並び順は自由
    'クイーンステークス 35% B 202601020811 15:25',
    '202601020811 クイーンステークス 15:25 B 0.35',  # 小数でも可
])
def test_header_fields_are_recognised_in_any_order(header):
    import make_bets

    parsed = make_bets.parse_header(header)

    assert parsed['race_id'] == '202601020811'
    assert parsed['start_time'] == '15:25'
    assert parsed['confidence'] == 'B'
    assert parsed['subjective_hit_rate'] == pytest.approx(0.35)
    assert parsed['name'] == 'クイーンステークス'


def test_header_defaults_to_confidence_b():
    import make_bets
    parsed = make_bets.parse_header('202601020811 テスト 15:25 20%')
    assert parsed['confidence'] == 'B'


def test_stake_defaults_to_100():
    import make_bets
    assert make_bets.parse_bet_line('馬連 7-11').stake == 100
    assert make_bets.parse_bet_line('馬連 7-11 300円').stake == 300


@pytest.mark.parametrize('text,expected', [
    ('クイーンステークス 15:25 B 35%', 'race_id'),          # race_id が無い
    ('202601020811 クイーンステークス B 35%', '発走時刻'),   # 発走時刻が無い
])
def test_bad_header_is_rejected_with_a_reason(text, expected):
    import make_bets
    with pytest.raises(BetsError) as excinfo:
        make_bets.parse_header(text)
    assert expected in str(excinfo.value)


def test_unreadable_line_reports_which_race(monkeypatch):
    import make_bets
    bad = '202601020811 テスト 15:25 B 20%\n◎7\nよくわからない行\n'
    with pytest.raises(BetsError) as excinfo:
        make_bets.parse_text(bad)
    assert '1件目のレース' in str(excinfo.value)
    assert 'よくわからない行' in str(excinfo.value)


def test_pasted_sheet_survives_a_round_trip(tmp_path, monkeypatch):
    """貼り付けたものが check.py の読める JSON になること。"""
    import make_bets

    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path))
    races = make_bets.parse_text(PASTED)
    bets.save_sheet(BetSheet(date=date(2026, 8, 8), races=races, source='manual'))

    loaded = bets.load_sheet(date(2026, 8, 8))
    assert [r.name for r in loaded.races] == ['クイーンステークス', 'アイビスサマーダッシュ']
    assert loaded.races[1].subjective_hit_rate == 0.29


# ----------------------------------------------------------------------
# 馬場状態（朝の予想ログが「直前タスクで要確認」と名指しする項目）
# ----------------------------------------------------------------------

RACE_DATA_HEAVY = """
<div class="RaceData01">
  <span>15:25発走</span> / <span>ダ右1700m</span> / 天候:雨 / 馬場:重
</div>
"""

RACE_DATA_MORNING = """
<div class="RaceData01">
  <span>15:25発走</span> / <span>ダ右1700m</span>
</div>
"""


def test_conditions_are_parsed_on_race_day():
    parsed = conditions_module.parse(RACE_DATA_HEAVY)
    assert parsed['going'] == '重'
    assert parsed['weather'] == '雨'
    assert parsed['surface'] == 'ダート'
    assert parsed['distance'] == 1700


def test_conditions_are_empty_before_they_are_published():
    """朝は馬場状態が出ていないので None のままになること。"""
    parsed = conditions_module.parse(RACE_DATA_MORNING)
    assert parsed['going'] is None
    assert parsed['distance'] == 1700     # コースは朝でも分かる


@pytest.mark.parametrize('going,heavy', [
    ('良', False), ('稍重', True), ('重', True), ('不良', True),
])
def test_is_heavy(going, heavy):
    assert conditions_module.is_heavy({'going': going}) is heavy


def test_heavy_going_is_warned_but_not_judged():
    """馬場が渋っていたら知らせる。ただしどの馬を上げ下げするかは判断しない。"""
    race = make_race(name='エルムステークス', bets=[Bet('馬連', [2, 11])],
                     marks=marks_of(items=[('◎', 2), ('○', 11)]))
    verdict = discipline.review_race(
        race, [35.2], {}, {}, datetime(2026, 8, 8, 14, 10, tzinfo=JST),
        date(2026, 8, 8),
        {'going': '重', 'weather': '雨', 'surface': 'ダート', 'distance': 1700})

    finding = next(f for f in verdict.warnings if f.code == 'heavy_going')
    assert '馬場が「重」です' in finding.message
    assert '第5章' in finding.remedy and '第10章' in finding.remedy
    # 知らせるだけで発注は止めない（判断は人がする）
    assert not verdict.blocked


def test_good_going_is_reported_without_a_warning():
    race = make_race(bets=[Bet('馬連', [2, 11])])
    verdict = discipline.review_race(
        race, [35.2], {}, {}, datetime(2026, 8, 8, 14, 10, tzinfo=JST),
        date(2026, 8, 8), {'going': '良', 'surface': 'ダート', 'distance': 1700})

    assert 'heavy_going' not in [f.code for f in verdict.warnings]
    assert verdict.conditions['going'] == '良'


def test_check_continues_when_conditions_cannot_be_fetched(tmp_path, monkeypatch):
    """馬場が取れなくても検算は続くこと。"""
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path / 'bets'))
    monkeypatch.setattr(bets, 'CHECKS_DIR', str(tmp_path / 'checks'))
    bets.save_sheet(BetSheet(
        date=date(2026, 8, 8),
        races=[make_race(bets=[Bet('馬連', [2, 11])], subjective_hit_rate=0.30)],
    ))

    def broken(race_id, **kwargs):
        raise conditions_module.ConditionsError('取得できません')

    monkeypatch.setattr(conditions_module, 'fetch', broken)
    monkeypatch.setattr(odds_module, 'fetch', lambda rid, bt: {
        'status': 'middle', 'reason': None, 'official_datetime': '14:10:00',
        'odds': {'0211': ['35.2', '36.0', '9'], '02': ['16.4', '17.0', '7']}})

    assert check.main(['--date', '2026-08-08', '--now', '2026-08-08T14:10',
                       '--no-email', '--no-save']) == check.EXIT_OK


# ----------------------------------------------------------------------
# 各馬の馬場状態別の実績（第12章の分析シート項目）
# ----------------------------------------------------------------------

HORSE_RESULTS = """
<table class="db_h_race_results">
  <tr><th>日付</th><th>開催</th><th>レース名</th><th>距離</th><th>馬場</th><th>着順</th></tr>
  <tr><td>2026/07/12</td><td>函館</td><td>マリンS</td><td>ダ1700</td><td>良</td><td>2</td></tr>
  <tr><td>2026/06/14</td><td>函館</td><td>大沼S</td><td>ダ1700</td><td>稍重</td><td>2</td></tr>
  <tr><td>2026/05/03</td><td>東京</td><td>オープン</td><td>ダ1600</td><td>良</td><td>6</td></tr>
  <tr><td>2026/03/01</td><td>中山</td><td>オープン</td><td>ダ1800</td><td>重</td><td>1</td></tr>
  <tr><td>2026/01/20</td><td>中京</td><td>オープン</td><td>ダ1800</td><td>良</td><td>7</td></tr>
  <tr><td>2025/12/01</td><td>阪神</td><td>オープン</td><td>ダ1800</td><td>良</td><td>中止</td></tr>
</table>
"""

SHUTUBA_ROWS = """
<table class="Shutuba_Table">
  <tr class="HorseList">
    <td class="Waku1">1</td>
    <td class="Umaban Umaban1">2</td>
    <td class="HorseInfo"><a href="https://db.netkeiba.com/horse/2021104321/">レヴォントゥレット</a></td>
    <td class="Weight">486<small>(+4)</small></td>
  </tr>
  <tr class="HorseList">
    <td class="Waku6">6</td>
    <td class="Umaban Umaban11">11</td>
    <td class="HorseInfo"><a href="https://db.netkeiba.com/horse/2020103111/">ペリエール</a></td>
    <td class="Weight">512<small>(-2)</small></td>
  </tr>
</table>
"""


def test_going_record_is_tallied_per_track_condition():
    import form
    record = form.parse_going_record(HORSE_RESULTS)

    assert record['良'] == [0, 1, 0, 2]      # 2着1回・6着・7着。中止は数えない
    assert record['稍重'] == [0, 1, 0, 0]
    assert record['重'] == [1, 0, 0, 0]
    assert '不良' not in record              # 出走が無い馬場は入らない


def test_entries_map_umaban_to_horse_id_and_weight():
    import form
    entries = form.parse_entries(SHUTUBA_ROWS)

    assert entries[2]['horse_id'] == '2021104321'
    assert entries[2]['name'] == 'レヴォントゥレット'
    assert entries[2]['weight'] == 486
    assert entries[2]['weight_diff'] == 4
    assert entries[11]['weight_diff'] == -2


def test_summarise_puts_todays_going_first():
    import form
    record = form.parse_going_record(HORSE_RESULTS)

    assert form.summarise(record, '重').startswith('重[1-0-0-0]')
    assert form.summarise(record, '不良').startswith('不良は出走なし')


@pytest.mark.parametrize('going,expected', [
    ('重', True), ('稍重', True), ('不良', False),
])
def test_has_experience(going, expected):
    import form
    record = form.parse_going_record(HORSE_RESULTS)
    assert form.has_experience(record, going) is expected


def test_missing_going_experience_is_warned_for_senior_marks():
    """馬場が渋ったとき、◎○にその馬場の実績が無ければ知らせる。"""
    import form
    record = form.parse_going_record(HORSE_RESULTS)
    race = make_race(name='エルムステークス',
                     marks=marks_of(items=[('◎', 2), ('○', 11)]),
                     bets=[Bet('馬連', [2, 11])])

    verdict = discipline.review_race(
        race, [35.2], {}, {}, datetime(2026, 8, 8, 14, 10, tzinfo=JST),
        date(2026, 8, 8), {'going': '不良', 'surface': 'ダート'},
        {2: {'name': 'レヴォントゥレット', 'record': record},
         11: {'name': 'ペリエール', 'record': record}})

    warned = [f for f in verdict.warnings if f.code == 'no_going_experience']
    assert len(warned) == 2
    assert '「不良」での出走がありません' in warned[0].message
    assert '第6章' in warned[0].remedy
    # 知らせるだけで発注は止めない
    assert not verdict.blocked


def test_no_warning_when_the_horse_has_run_on_that_going():
    import form
    record = form.parse_going_record(HORSE_RESULTS)
    race = make_race(marks=marks_of(items=[('◎', 2)]), bets=[Bet('馬連', [2, 11])])

    verdict = discipline.review_race(
        race, [35.2], {}, {}, datetime(2026, 8, 8, 14, 10, tzinfo=JST),
        date(2026, 8, 8), {'going': '重'},
        {2: {'name': 'レヴォントゥレット', 'record': record}})

    assert 'no_going_experience' not in [f.code for f in verdict.warnings]


def test_form_is_not_fetched_on_good_going(tmp_path, monkeypatch):
    """良馬場なら各馬の戦績は引かない（不要なアクセスをしない）。"""
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path / 'bets'))
    monkeypatch.setattr(bets, 'CHECKS_DIR', str(tmp_path / 'checks'))
    bets.save_sheet(BetSheet(
        date=date(2026, 8, 8),
        races=[make_race(marks=marks_of(items=[('◎', 2)]),
                         bets=[Bet('馬連', [2, 11])], subjective_hit_rate=0.30)],
    ))

    called = []
    monkeypatch.setattr(conditions_module, 'fetch',
                        lambda rid, **kw: {'going': '良', 'surface': 'ダート'})
    monkeypatch.setattr(odds_module, 'fetch', lambda rid, bt: {
        'status': 'middle', 'reason': None, 'official_datetime': '14:10:00',
        'odds': {'0211': ['35.2', '36.0', '9'], '02': ['16.4', '17.0', '7']}})
    monkeypatch.setattr('form.collect', lambda rid, nums: called.append(rid) or {})

    check.main(['--date', '2026-08-08', '--now', '2026-08-08T14:10',
                '--no-email', '--no-save'])

    assert called == []


# ----------------------------------------------------------------------
# 馬場状態の読み取り（2026-08-08 に札幌だけ読めなかった件）
# ----------------------------------------------------------------------

@pytest.mark.parametrize('text,expected', [
    ('15:25発走 / ダ右1700m / 天候:曇 / 馬場:良', '良'),
    ('15:35発走 / 芝左外1800m / 天候:晴 / 芝:稍重', '稍重'),
    ('ダート : 重 天候 : 雨', '重'),
    ('馬場状態:不良', '不良'),          # 「馬場状態」でも拾う
    ('馬場 重', '重'),                  # 区切りが無くても拾う
    ('15:25発走 / ダ右1700m / 天候:曇', None),   # 未発表なら None
])
def test_find_going_handles_variations(text, expected):
    assert conditions_module.find_going(text) == expected


def test_furyo_is_not_misread_as_ryo():
    """「不良」を「良」と読み違えないこと。"""
    assert conditions_module.find_going('馬場:不良') == '不良'
    assert conditions_module.find_going('ダート:不良 天候:雨') == '不良'


def test_falls_back_to_db_page_when_shutuba_has_no_going():
    """出馬表で馬場が読めなければ db.netkeiba.com に当たること。

    2026-08-08、札幌は天候だけ読めて馬場が読めなかった。
    """
    shutuba = '<div class="RaceData01">15:25発走 / ダ右1700m / 天候:曇</div>'
    db_page = '<div class="data_intro">ダ右1700m / 天候 : 曇 / ダート : 稍重</div>'
    fetched = []

    def fake_get(url, timeout=30, opener=None):
        fetched.append(url)
        return db_page if 'db.netkeiba.com' in url else shutuba

    original = conditions_module._get
    try:
        conditions_module._get = fake_get
        result = conditions_module.fetch('202601010511')
    finally:
        conditions_module._get = original

    assert len(fetched) == 2                    # 予備にも当たった
    assert result['going'] == '稍重'             # 予備から補えた
    assert result['surface'] == 'ダート'         # 先に取れていた項目は残る
    assert result['distance'] == 1700


def test_does_not_hit_the_fallback_when_the_going_is_already_known():
    shutuba = '<div class="RaceData01">15:35発走 / 芝左外1800m / 天候:晴 / 馬場:良</div>'
    fetched = []

    def fake_get(url, timeout=30, opener=None):
        fetched.append(url)
        return shutuba

    original = conditions_module._get
    try:
        conditions_module._get = fake_get
        result = conditions_module.fetch('202604020507')
    finally:
        conditions_module._get = original

    assert len(fetched) == 1                    # 余計なアクセスをしない
    assert result['going'] == '良'


def test_keeps_what_it_has_when_the_fallback_also_fails():
    shutuba = '<div class="RaceData01">15:25発走 / ダ右1700m / 天候:曇</div>'

    def fake_get(url, timeout=30, opener=None):
        if 'db.netkeiba.com' in url:
            raise conditions_module.ConditionsError('予備も駄目')
        return shutuba

    original = conditions_module._get
    try:
        conditions_module._get = fake_get
        result = conditions_module.fetch('202601010511')
    finally:
        conditions_module._get = original

    assert result['going'] is None
    assert result['weather'] == '曇'             # 取れているぶんは返す
    assert result['distance'] == 1700
