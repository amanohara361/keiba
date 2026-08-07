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
import odds as odds_module  # noqa: E402
from bets import JST, Bet, BetSheet, RaceBets, parse_sheet  # noqa: E402


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

    assert exit_code == check.EXIT_ERROR
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

    exit_code = check.main(['--date', '2026-08-02', '--now', '2026-08-02T14:30',
                            '--no-email'])

    assert exit_code == check.EXIT_BLOCKED
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

    exit_code = check.main(['--date', '2026-08-02', '--now', '2026-08-02T14:30',
                            '--no-email', '--no-save'])

    assert exit_code == check.EXIT_OK
    assert '規律をクリア' in capsys.readouterr().out
