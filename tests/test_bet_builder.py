#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bet_builder.py（印＋中穴候補＋実オッズから買い目を組み立てる）を確かめる。

2026-08-14 ユーザー承認：印は7ステップ適用時に確定するが、買い目は実オッズが
確定してから（直前検算）組む。ここはその組み立てロジックの単体テスト。
ネットワークには接続しない。

2026-08-26 改修：主観的中率の自己申告（レース単位の1つの数字）をやめ、馬番ごとの
主観勝率を Harville モデルに通して券種ごとに的中率を出す方式へ変更した。
実測29件の検証で、申告値の平均28.9%に対し実際の的中率が7.1%（市場推定13.6%）と
判明し、期待値の判定が機能していなかったため。詳細は bet_builder.py の冒頭。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bet_builder  # noqa: E402
from bets import RaceBets  # noqa: E402

# 出走馬の単勝オッズ。市場勝率（Harville の入力）はここから作られる。
# **実際のレースに近い頭数にすること。** 少頭数だとワイドの的中率が極端に
# 高く出て（6頭立てだと2頭が3着以内に入る確率は50%を超える）、テストが
# 現実と乖離する。逆数の和が1.0前後＝控除率込みの実際の単勝オッズらしい形。
WIN_ODDS = {7: 3.0, 11: 5.0, 2: 8.0, 14: 12.0, 9: 15.0, 5: 20.0,
            1: 25.0, 3: 30.0, 4: 40.0, 6: 50.0, 8: 60.0, 10: 80.0,
            12: 100.0, 13: 150.0}


def race(marks, partners=None, win_probabilities=None):
    return RaceBets(
        race_id='202601020811', name='テストステークス', start_time='15:25',
        marks=marks, bets=[], partners=partners or [],
        win_probabilities=win_probabilities,
    )


def lookup_from(table):
    """{(bet_type, frozenset(horses)): odds} からlookup関数を作る。"""
    def lookup(bet_type, horses):
        return table.get((bet_type, frozenset(horses)))
    return lookup


def build(r, table, win_odds=None):
    return bet_builder.build_bets(r, lookup_from(table),
                                  WIN_ODDS if win_odds is None else win_odds)


# ----------------------------------------------------------------------
# 勝率モデル
# ----------------------------------------------------------------------

def test_市場勝率は控除率を除いて1になる():
    p = bet_builder.market_win_probabilities(WIN_ODDS)
    assert abs(sum(p.values()) - 1.0) < 1e-9
    assert p[7] > p[11] > p[2]   # 単勝が安い馬ほど勝率が高い


def test_主観の上書きは残りの馬で辻褄を合わせる():
    market = bet_builder.market_win_probabilities(WIN_ODDS)
    p = bet_builder.apply_subjective(market, {7: 0.20})
    assert abs(sum(p.values()) - 1.0) < 1e-9
    assert p[7] == 0.20
    # 指定しなかった馬は市場比のまま押し上げられる（順序は保たれる）
    assert p[11] > p[2] > p[14]


def test_券種ごとに的中率が変わる():
    """旧実装の最大の不備：全券種で同じ的中率を使い回していた。"""
    p = bet_builder.market_win_probabilities(WIN_ODDS)
    quinella = bet_builder.p_quinella(p, (7, 11))
    wide = bet_builder.p_wide(p, (7, 11))
    trio = bet_builder.p_trio(p, (7, 11, 2))
    assert wide > quinella > trio
    for v in (quinella, wide, trio):
        assert 0.0 < v < 1.0


# ----------------------------------------------------------------------
# 組み立て
# ----------------------------------------------------------------------

def test_規律を満たすもののうち的中率が最大の案を選ぶ():
    """期待値最大ではなく的中率最大。主観勝率が過大な状態では、点数が少なく

    オッズが高い案ほど期待値が膨らみ、見積り誤差を最も増幅するため。
    ここでは馬連1点（高オッズ・低的中率）と馬連2点（低オッズ・高的中率）の
    両方が規律を満たすが、的中率の高い2点が選ばれることを確認する。
    """
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11},
                    {'mark': '▲', 'umaban': 2}],
             win_probabilities={7: 0.35, 11: 0.22, 2: 0.15})
    confidence, built, note = build(r, {
        ('馬連', frozenset({7, 11})): 12.0,
        ('馬連', frozenset({7, 2})): 14.0,
    })
    assert confidence in ('A', 'B')
    assert {b.combination for b in built} == {'7-11', '7-2'}
    assert all(b.type == '馬連' for b in built)


def test_ワイドの方が的中率が高ければワイドが選ばれる():
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11},
                    {'mark': '▲', 'umaban': 2}],
             win_probabilities={7: 0.35, 11: 0.22, 2: 0.15})
    confidence, built, note = build(r, {
        ('馬連', frozenset({7, 11})): 12.0,
        ('馬連', frozenset({7, 2})): 14.0,
        ('ワイド', frozenset({7, 11})): 5.0,
        ('ワイド', frozenset({7, 2})): 6.0,
    })
    assert all(b.type == 'ワイド' for b in built)
    assert 'ワイド' in note


def test_3連複が候補に入る():
    """第13章は3連複の組み方を規定しているのに、旧実装には候補が無かった。"""
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11}],
             partners=[{'umaban': 9, 'reason': '前走出遅れ'}],
             win_probabilities={7: 0.32, 11: 0.20, 9: 0.12})
    p = bet_builder.apply_subjective(
        bet_builder.market_win_probabilities(WIN_ODDS), r.win_probabilities)
    cands = bet_builder._build_candidates(
        7, [11, 9], set(r.marked_horses), p,
        lookup_from({('3連複', frozenset({7, 11, 9})): 20.0}))
    assert any(c.bet_type == '3連複' for c in cands)


def test_3連複はセット全体が印馬だけにならない():
    """第13章「無印馬が1頭でも絡んだ時点で全点が消滅する」ため判定はセット単位。"""
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11},
                    {'mark': '▲', 'umaban': 2}],
             win_probabilities={7: 0.32, 11: 0.20, 2: 0.14})
    p = bet_builder.apply_subjective(
        bet_builder.market_win_probabilities(WIN_ODDS), r.win_probabilities)
    cands = bet_builder._build_candidates(
        7, [11, 2], set(r.marked_horses), p,
        lookup_from({('3連複', frozenset({7, 11, 2})): 20.0}))
    assert not [c for c in cands if c.bet_type == '3連複'], \
        '◎○▲だけの3連複は候補にしない'


def test_期待値1_5以上で勝負度A():
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11}],
             win_probabilities={7: 0.45, 11: 0.30})
    confidence, built, _ = build(r, {('馬連', frozenset({7, 11})): 8.0})
    assert built
    assert confidence == 'A'


def test_期待値1_2以上1_5未満で勝負度B():
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11}],
             win_probabilities={7: 0.30, 11: 0.18})
    # 的中率14.3%なので、合成9.0倍なら期待値1.29（A の 1.5 に届かない）
    confidence, built, _ = build(r, {('馬連', frozenset({7, 11})): 9.0})
    assert built
    assert confidence == 'B'


def test_算出した的中率をraceへ書き戻す():
    """discipline が同じ数字で期待値を再計算できるようにするため。

    書き戻さないと bet_builder の表示（券種別）と discipline の表示（申告値）が
    食い違う。保存される data/bets の値も「実際に使った的中率」になるので、
    後日の検証で申告値と実測を突き合わせられる。
    """
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11}],
             win_probabilities={7: 0.35, 11: 0.22})
    _confidence, built, _note = build(r, {('馬連', frozenset({7, 11})): 12.0})
    assert built
    expected = bet_builder.p_quinella(
        bet_builder.apply_subjective(
            bet_builder.market_win_probabilities(WIN_ODDS), r.win_probabilities),
        (7, 11))
    assert abs(r.subjective_hit_rate - round(expected, 4)) < 1e-9


# ----------------------------------------------------------------------
# 見送り・安全側
# ----------------------------------------------------------------------

def test_主観勝率が無ければ市場勝率だけで評価して基本は見送りになる():
    """旧 subjective_hit_rate へはフォールバックしない。

    あの数字は実測検証で棄却されている（申告28.9%に対し実測7.1%）。
    市場勝率だけなら期待値は控除率前後にしかならず、規律を満たさない。
    **オッズは市場勝率と整合した値を使うこと。** 実勢とかけ離れたオッズを
    置くと「市場より甘い賭け」を作ってしまい、検証にならない。
    馬連7-11の市場推定は約17.4%なので、妥当なオッズは 0.775÷0.174 ≒ 4.5倍。
    """
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11}])
    confidence, built, note = build(r, {('馬連', frozenset({7, 11})): 4.5})
    assert confidence == 'C'
    assert built == []
    assert 'win_probabilities' in note


def test_相手全員のオッズが低いと絞っても救えず見送りになる():
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11},
                    {'mark': '▲', 'umaban': 2}],
             win_probabilities={7: 0.40, 11: 0.25, 2: 0.15})
    confidence, built, _note = build(r, {
        ('馬連', frozenset({7, 11})): 3.0,
        ('馬連', frozenset({7, 2})): 3.0,
    })
    assert confidence == 'C'
    assert built == []


def test_オッズが0以下ならNone扱いで見送りクラッシュしない():
    """2026-08-15、地方の1レースでオッズCSVが全点0.0を返し TypeError で

    検算全体が落ちた事故の再発防止。見送り（C）になり例外を投げないこと。
    """
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11}],
             win_probabilities={7: 0.35, 11: 0.22})
    confidence, built, _note = build(r, {
        ('馬連', frozenset({7, 11})): 0.0,
        ('ワイド', frozenset({7, 11})): 0.0,
    })
    assert confidence == 'C'
    assert built == []


def test_下位印のオッズが無ければ絞り込みでその馬を落として救える():
    """△のオッズが無くても、○▲まで絞った段が使えれば組める。"""
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11},
                    {'mark': '▲', 'umaban': 2}, {'mark': '△', 'umaban': 14}],
             win_probabilities={7: 0.35, 11: 0.22, 2: 0.15, 14: 0.10})
    confidence, built, _note = build(r, {
        # △14のオッズはどの券種にも無い。○11・▲2のワイドは絞った段で使える。
        ('ワイド', frozenset({7, 11})): 8.0,
        ('ワイド', frozenset({7, 2})): 9.0,
    })
    assert {b.combination for b in built} == {'7-11', '7-2'}
    assert all(b.type == 'ワイド' for b in built)
    assert all(14 not in b.horses for b in built), 'オッズの無い△14は入らない'


def test_上位印のオッズが無ければその段は使わず絞り込みへ進む():
    """○のオッズが無い場合、○抜きの組み合わせで勝手に組まない。

    部分的に組むと、オッズが引けなかった馬（◎○かもしれない）を無言で相手から
    落とすことになる（実オッズ優先の原則・第13章）。
    """
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11},
                    {'mark': '▲', 'umaban': 2}],
             win_probabilities={7: 0.35, 11: 0.22, 2: 0.15})
    confidence, built, _note = build(r, {
        ('馬連', frozenset({7, 2})): 10.0,
        ('ワイド', frozenset({7, 2})): 10.0,
    })
    assert confidence == 'C'
    assert built == []


def test_単勝オッズが無ければ勝率を推定できないので見送り():
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11}],
             win_probabilities={7: 0.35, 11: 0.22})
    confidence, built, note = build(r, {('馬連', frozenset({7, 11})): 12.0},
                                    win_odds={})
    assert confidence == 'C'
    assert built == []
    assert '単勝' in note


def test_軸がいなければ組めない():
    r = race(marks=[{'mark': '○', 'umaban': 11}])
    confidence, built, note = build(r, {})
    assert confidence == 'C'
    assert built == []
    assert '◎' in note


def test_相手候補が無ければ組めない():
    r = race(marks=[{'mark': '◎', 'umaban': 7}])
    confidence, built, _note = build(r, {})
    assert confidence == 'C'
    assert built == []


# ----------------------------------------------------------------------
# 警告（採否は変えない）
# ----------------------------------------------------------------------

def test_主観が市場から大きく乖離していたら警告を出す():
    r = race(marks=[{'mark': '◎', 'umaban': 7}, {'mark': '○', 'umaban': 11}],
             win_probabilities={7: 0.55, 11: 0.35})
    _confidence, built, note = build(r, {('馬連', frozenset({7, 11})): 8.0})
    assert built
    assert '市場推定の' in note


# ----------------------------------------------------------------------
# 回帰：2026-08-23 キーンランドカップ（旧実装が取りこぼした実例）
# ----------------------------------------------------------------------

KEENLAND_WIN = {1: 17.0, 2: 16.7, 3: 2.0, 4: 11.8, 5: 29.1, 6: 98.8, 7: 217.7,
                8: 10.1, 9: 61.3, 10: 19.1, 11: 35.7, 12: 158.8, 13: 5.8,
                14: 13.6, 15: 18.8, 16: 44.3}
KEENLAND_TABLE = {
    ('3連複', frozenset({3, 13, 15})): 33.6,
    ('3連複', frozenset({3, 13, 14})): 26.1,
    ('3連複', frozenset({3, 8, 13})): 23.1,
    ('馬連', frozenset({13, 3})): 7.2,
    ('馬連', frozenset({13, 15})): 14.6,
    ('馬連', frozenset({13, 14})): 16.9,
    ('ワイド', frozenset({13, 3})): 3.0,
    ('ワイド', frozenset({13, 15})): 12.7,
    ('ワイド', frozenset({13, 14})): 9.8,
    ('ワイド', frozenset({13, 8})): 11.2,
}
KEENLAND_PROBS = {3: 0.33, 13: 0.175, 15: 0.085, 14: 0.075, 8: 0.055,
                  1: 0.033, 4: 0.050, 10: 0.033, 2: 0.035}


def keenland_race():
    return race(
        marks=[{'mark': '◎', 'umaban': 13}, {'mark': '○', 'umaban': 3},
               {'mark': '▲', 'umaban': 15}, {'mark': '△', 'umaban': 14}],
        partners=[{'umaban': 8, 'reason': 'イン前有利の馬場で昨年の当レースを1-1'}],
        win_probabilities=KEENLAND_PROBS,
    )


def test_キーンランドC_見送らずに買い目を組める():
    """旧実装はこの鞍を見送った。馬連の相手全員ぶんのオッズが揃わずワイドへ

    落ち、そこで一律16%の的中率を掛けて期待値0.31〜0.48となり全滅したため。
    実際には期待値1.2を超える3連複が組める配置だった。
    """
    confidence, built, note = bet_builder.build_bets(
        keenland_race(), lookup_from(KEENLAND_TABLE), KEENLAND_WIN)
    assert confidence in ('A', 'B')
    assert built, '買い目が組めるはず'
    assert built[0].type == '3連複'
    assert len(built) == 3


def test_キーンランドC_相手に無印の中穴を含む():
    _confidence, built, _note = bet_builder.build_bets(
        keenland_race(), lookup_from(KEENLAND_TABLE), KEENLAND_WIN)
    marked = set(keenland_race().marked_horses)
    assert any(any(h not in marked for h in b.horses) for b in built)
