#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bet_builder.py（印＋中穴候補＋実オッズから買い目を組み立てる）を確かめる。

2026-08-14 ユーザー承認：印は7ステップ適用時に確定するが、買い目は実オッズが
確定してから（直前検算）組む。ここはその組み立てロジックの単体テスト。
ネットワークには接続しない。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bet_builder  # noqa: E402
from bets import RaceBets  # noqa: E402


def race(marks, partners=None, subjective_hit_rate=0.3):
    return RaceBets(
        race_id='202601020811', name='テストステークス', start_time='15:25',
        marks=marks, bets=[], partners=partners or [],
        subjective_hit_rate=subjective_hit_rate,
    )


def lookup_from(table):
    """{(bet_type, frozenset(horses)): odds} からlookup関数を作る。"""
    def lookup(bet_type, horses):
        return table.get((bet_type, frozenset(horses)))
    return lookup


def test_相手プール全部で規律を満たせばそのまま組む():
    r = race(
        marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11}],
        partners=[{'umaban': 9, 'reason': '前走出遅れ'}],
        subjective_hit_rate=0.3,
    )
    lookup = lookup_from({
        ('馬連', frozenset({7, 11})): 8.0,
        ('馬連', frozenset({7, 9})): 8.0,
    })
    confidence, built, note = bet_builder.build_bets(r, lookup)
    assert confidence in ('A', 'B')
    assert {b.combination for b in built} == {'7-11', '7-9'}
    assert all(b.type == '馬連' for b in built)


def test_期待値1_5以上で勝負度A():
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11}],
             subjective_hit_rate=0.5)
    lookup = lookup_from({('馬連', frozenset({7, 11})): 4.0})  # 期待値2.0
    confidence, built, _ = bet_builder.build_bets(r, lookup)
    assert confidence == 'A'
    assert built


def test_期待値1_2以上1_5未満で勝負度B():
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11}],
             subjective_hit_rate=0.3)
    lookup = lookup_from({('馬連', frozenset({7, 11})): 4.5})  # 期待値1.35
    confidence, built, _ = bet_builder.build_bets(r, lookup)
    assert confidence == 'B'
    assert built


def test_相手全員のオッズが低いと絞っても救えず見送りになる():
    """相手2頭とも馬連3.0倍だと、合成では1.5倍まで沈み基準を割る。

    ワイドのオッズを渡していないので券種の切り替え（F案②）も不成立。
    どの段でも規律を満たせず見送り（C）になることを確認する。
    """
    r = race(
        marks=[
            {'mark': '◎', 'umaban': 7},
            {'mark': '○', 'umaban': 11},
            {'mark': '▲', 'umaban': 2},
        ],
        subjective_hit_rate=0.4,
    )
    lookup = lookup_from({
        ('馬連', frozenset({7, 11})): 3.0,
        ('馬連', frozenset({7, 2})): 3.0,
    })
    confidence, built, note = bet_builder.build_bets(r, lookup)
    assert confidence == 'C'
    assert built == []


def test_馬連で沈んだらワイドへ振り替える():
    r = race(
        marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11},
               {'mark': '▲', 'umaban': 2}],
        subjective_hit_rate=0.3,
    )
    lookup = lookup_from({
        # 馬連は期待値未達
        ('馬連', frozenset({7, 11})): 3.0,
        ('馬連', frozenset({7, 2})): 3.0,
        # ワイドなら妙味がある
        ('ワイド', frozenset({7, 11})): 6.0,
        ('ワイド', frozenset({7, 2})): 6.0,
    })
    confidence, built, note = bet_builder.build_bets(r, lookup)
    assert built
    assert all(b.type == 'ワイド' for b in built)
    assert 'ワイド' in note


def test_相手を1頭にまで絞っても駄目なら見送り():
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11}],
             subjective_hit_rate=0.1)
    lookup = lookup_from({
        ('馬連', frozenset({7, 11})): 3.5,
        ('ワイド', frozenset({7, 11})): 3.5,
    })
    confidence, built, note = bet_builder.build_bets(r, lookup)
    assert confidence == 'C'
    assert built == []


def test_オッズが1件も取れなければ見送りかつその旨をnoteに書く():
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11}],
             subjective_hit_rate=0.3)
    lookup = lookup_from({})  # 何も返さない
    confidence, built, note = bet_builder.build_bets(r, lookup)
    assert confidence == 'C'
    assert built == []
    assert 'オッズ' in note


def test_軸がいなければ組めない():
    r = race(marks=[{'mark': '○', 'umaban': 11}], subjective_hit_rate=0.3)
    confidence, built, note = bet_builder.build_bets(r, lookup_from({}))
    assert confidence == 'C'
    assert built == []
    assert '◎' in note


def test_相手候補が無ければ組めない():
    r = race(marks=[{'mark': '◎', 'umaban': 7}], subjective_hit_rate=0.3)
    confidence, built, note = bet_builder.build_bets(r, lookup_from({}))
    assert confidence == 'C'
    assert built == []


def test_上位印のオッズが無くても下位印だけを勝手に残さない矛盾は起きない():
    """○のオッズが取れない場合、bet_builderは○を除いた組み合わせで組む。

    discipline.check_matched_marks（上位印が買い目に無いのに下位印が残る）は
    ここでは検出できない（bet_builderは印の序列を知らない）。安全網は
    discipline側に残す設計であることの確認を兼ねる。
    """
    r = race(
        marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11},
               {'mark': '▲', 'umaban': 2}],
        subjective_hit_rate=0.3,
    )
    lookup = lookup_from({
        # ○11のオッズが無い。▲2だけが埋まる。
        ('馬連', frozenset({7, 2})): 10.0,
    })
    confidence, built, note = bet_builder.build_bets(r, lookup)
    assert {b.combination for b in built} == {'7-2'}
