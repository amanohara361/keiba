#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""印(marks)＋中穴候補(partners)＋実オッズから、買い目を機械的に組み立てる。

2026-08-14 ユーザー承認：印は7ステップ適用時（朝タスク）に確定するが、
買い目（券種・組み合わせ・勝負度）は実オッズが確定してから（直前検算）に
一本化する。朝時点の暫定オッズで買い目まで固めると、第13章の各基準
（合成オッズ・期待値）が入力として使う「オッズ」自体が仮の値になり、
規律が本来の意味で機能しない。

印を打つ判断には一切関与しない。ここが決めるのは「印が決まったあと、
どう買うか」だけ（第13章）。

## 組み立ての手順（F案の優先順位をそのまま機械化）

軸は◎固定。相手プールは `RaceBets.partner_pool`（○▲△→中穴候補の優先順）。
第13章「相手の広げ方」（345〜350行目）が指示する既定形＝
「◎を軸に、相手には印馬に加えて中穴を最低1頭含める」を第1段階とし、
それが規律（合成オッズ3.0倍・期待値1.2）を満たさなければ、
「印と買い目の矛盾解消（F案）」（354〜360行目）の優先順位で段階的に絞る。

  1. 相手プール全部（印馬＋中穴候補）で馬連
  2. 相手を上位3頭に絞って馬連（F案①「◎○の2〜3頭に絞る」）
  3. 同じ相手でワイドに切り替える（F案②）
  4. 相手をさらに2頭・1頭と絞る（F案②「点数を2点以下に絞る」）
  5. どれも満たせなければ見送り（勝負度C、買い目なし。F案③）

相手プールは常に○→▲→△→中穴の優先順で並んでいるため、絞り込みは
必ず上位印から残る。印の上位馬が相手から外れ下位印が残る、という
2026-08-02クイーンSの事故（矛盾）は構造的に起こらない。

**各段は、相手全員ぶんのオッズが揃わなければ不成立として次の段へ進む
（2026-08-14 ユーザー指摘・承認）。** 一部の相手のオッズだけで組んだ場合、
オッズが引けなかった馬（それが◎○かもしれない）を無言で相手から落とす
ことになる。無印の中穴候補だけにオッズがあり印馬に無い場合、買い目に
下位印が1つも残らず discipline.check_matched_marks（上位印が買い目に無い
のに下位印が残っている、を検知する仕組み）もすり抜けてしまう。
実オッズ優先の原則（第13章）に沿って、完全に揃わない段は使わない。
この結果、discipline.py の合成オッズ・期待値・印の矛盾チェックは
bet_builder の出力に対しては構造的に違反しなくなる（安全網としては
make_bets.py での手組みなど、bet_builder を経由しない買い目に対して
引き続き効く）。

勝負度の最終確定もここで行う（2026-08-14ユーザー承認）。
期待値1.5以上をA（勝負）、1.2以上1.5未満をB（縮小）とする。
この1.5という線は検証で緩和・変更が承認されるまで変えない。
"""

import discipline
from bets import Bet

STRONG_EXPECTED_VALUE = 1.5  # これ以上なら勝負度A（2026-08-14 ユーザー承認）


def _clears_discipline(composite, ev):
    return (composite is not None and composite >= discipline.MIN_COMPOSITE_ODDS
            and ev is not None and ev >= discipline.MIN_EXPECTED_VALUE)


def _confidence_for(ev):
    return 'A' if ev >= STRONG_EXPECTED_VALUE else 'B'


def _odds_for_pairs(axis, partners, bet_type, lookup):
    """軸と各相手の2頭組み合わせ（馬連・ワイド）の実オッズをまとめて引く。"""
    return [lookup(bet_type, [axis, p]) for p in partners]


def build_bets(race, lookup, stake=100):
    """1レース分の買い目を組み立てる。

    race: marks・partners・subjective_hit_rate を持つ RaceBets。
          race.bets は使わない（呼び出し前の値に関わらず組み直す）。
    lookup: (bet_type, [umaban, umaban]) -> オッズ or None。
    戻り値: (confidence, [Bet, ...], note)
    """
    hit_rate = race.subjective_hit_rate
    axis_horses = race.horses_for('◎')
    if not axis_horses:
        return 'C', [], '◎が無いため買い目を組めません'
    axis = axis_horses[0]

    pool = race.partner_pool
    if not pool:
        return 'C', [], '相手候補（印馬・中穴候補）が無いため買い目を組めません'

    has_dark_horse = any(p['umaban'] not in race.marked_horses for p in race.partners)

    stages = [
        (pool, '馬連'),
        (pool[:3], '馬連'),
        (pool[:3], 'ワイド'),
        (pool[:2], 'ワイド'),
        (pool[:1], 'ワイド'),
    ]

    any_fully_priced = False   # 相手全員ぶんオッズが揃った段が一度でもあったか
    for partners, bet_type in stages:
        partners = list(dict.fromkeys(partners))  # 重複除去（順序維持）
        if not partners:
            continue
        odds_values = _odds_for_pairs(axis, partners, bet_type, lookup)
        if any(o is None or o <= 0 for o in odds_values):
            # 相手の誰か1頭でもオッズが引けなければこの段は使わない
            # （実オッズ優先の原則。上のdocstring参照）。
            # 0以下は「まだ売り出されていない」等の値で、実際のオッズでは
            # あり得ない（券面は必ず1倍超）。discipline.composite_odds も
            # 0以下を計算から除外するため、ここで先に弾かないと全点が
            # 0以下だった場合に composite が None のまま次の掛け算に渡り、
            # TypeError で検算全体が落ちる（2026-08-15、地方の1レースで
            # オッズCSVが未発売のため全点0.0を返し再現）。
            continue
        any_fully_priced = True
        composite = discipline.composite_odds(odds_values)
        ev = None if hit_rate is None else composite * hit_rate
        if _clears_discipline(composite, ev):
            selected = [Bet(bet_type, [axis, p], stake) for p in partners]
            note = (f'{bet_type} {axis}軸-{"/".join(str(p) for p in partners)}'
                    f'（合成{composite:.2f}倍・期待値{ev:.2f}）')
            if partners == pool and not has_dark_horse:
                note += ' ※中穴候補が無く印馬のみで構成'
            return _confidence_for(ev), selected, note

    if not any_fully_priced:
        return 'C', [], ('相手の実オッズが揃わず買い目を組めませんでした'
                         '（要・時間を置いて再検算）')

    return 'C', [], ('第13章の基準（合成オッズ3.0倍・期待値1.2）を満たす買い目を'
                     '組めませんでした。見送り（勝負度C）。')
