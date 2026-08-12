#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""週次レビューの集計が正しいか確かめる。ネットワークには接続しない。

着順のフィクスチャは架空のものを使う。実在レースの着順を手で書き起こすと、
書き間違いがそのまま「実績」として data/results/ に残りかねない
（過去に data_jra を手で上げて20レース分を壊した事故がある）。
実データを使う検証は、着順を必要としない部分——検算記録の読み方——に限る。
"""

import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bets  # noqa: E402
import results as results_module  # noqa: E402
import review  # noqa: E402
from bets import Bet, RaceBets  # noqa: E402


# ----------------------------------------------------------------------
# フィクスチャ
# ----------------------------------------------------------------------

def make_race(marks=(), bet_list=(), **kwargs):
    defaults = dict(
        race_id='202601020811',
        name='テストステークス',
        start_time='15:25',
        confidence='B',
        subjective_hit_rate=0.20,
    )
    defaults.update(kwargs)
    return RaceBets(
        marks=[{'mark': m, 'umaban': u} for m, u in marks],
        bets=[Bet(t, h, s) for t, h, s in bet_list],
        **defaults,
    )


def make_result(order, payouts=None, race_id='202601020811'):
    """order = [(着順, 馬番), ...]"""
    return {
        'race_id': race_id,
        'finishing_order': [
            {'rank': rank, 'umaban': umaban, 'name': f'ウマ{umaban}',
             'ninki': rank, 'odds': 5.0}
            for rank, umaban in order
        ],
        'payouts': payouts or {},
    }


PAY_TABLE = '''
<table class="pay_table_01">
<tr><th>単勝</th><td>4</td><td>390</td><td>2</td></tr>
<tr><th>複勝</th><td>4<br>11<br>2</td><td>150<br>800<br>210</td><td>2<br>9<br>3</td></tr>
<tr><th>馬連</th><td>4 - 11</td><td>3,660</td><td>14</td></tr>
<tr><th>ワイド</th><td>4 - 11<br>4 - 2</td><td>1,120<br>420</td><td>13<br>4</td></tr>
<tr><th>馬単</th><td>4 → 11</td><td>6,010</td><td>21</td></tr>
<tr><th>三連複</th><td>2 - 4 - 11</td><td>8,940</td><td>30</td></tr>
</table>
'''


# ----------------------------------------------------------------------
# 払戻のパース
# ----------------------------------------------------------------------

def test_払戻表を券種ごとに読む():
    payouts = results_module.parse_payouts(PAY_TABLE)
    assert payouts['単勝'] == [{'combination': [4], 'yen': 390}]
    assert payouts['馬連'] == [{'combination': [4, 11], 'yen': 3660}]
    # 複勝・ワイドは1セルに複数入る
    assert [e['yen'] for e in payouts['複勝']] == [150, 800, 210]
    assert len(payouts['ワイド']) == 2


def test_三連複は3連複という表記に揃える():
    payouts = results_module.parse_payouts(PAY_TABLE)
    assert '3連複' in payouts
    assert '三連複' not in payouts


def test_馬単は並び順まで一致しないと的中にしない():
    payouts = results_module.parse_payouts(PAY_TABLE)
    assert results_module.payout_for(payouts, '馬単', [4, 11]) == 6010
    assert results_module.payout_for(payouts, '馬単', [11, 4]) == 0
    # 馬連は順不同
    assert results_module.payout_for(payouts, '馬連', [11, 4]) == 3660


def test_払戻は賭け金に応じて按分する():
    """払戻表は100円あたり。200円買っていれば倍になる。"""
    race = make_race(bet_list=[('単勝', [4], 200)])
    result = make_result([(1, 4), (2, 11)], results_module.parse_payouts(PAY_TABLE))
    settlement = results_module.settle(race, result)
    assert settlement['staked'] == 200
    assert settlement['returned'] == 780        # 390 × 2
    assert settlement['hit'] is True


def test_外れた買い目は0円():
    race = make_race(bet_list=[('馬連', [2, 9], 100)])
    result = make_result([(1, 4), (2, 11)], results_module.parse_payouts(PAY_TABLE))
    settlement = results_module.settle(race, result)
    assert settlement['returned'] == 0
    assert settlement['hit'] is False


# ----------------------------------------------------------------------
# 分類（第13章の定義）
# ----------------------------------------------------------------------

def classify(race, result):
    return review.classify(race, results_module.settle(race, result), result)


def test_買い目が当たれば的中():
    race = make_race(marks=[('◎', 4)], bet_list=[('単勝', [4], 100)])
    result = make_result([(1, 4), (2, 11), (3, 2)], results_module.parse_payouts(PAY_TABLE))
    assert classify(race, result) == review.HIT


def test_本命が3着以内なのに全外れなら買い目構成ミス():
    """馬は見えていたのに買い方で落とした形。予想ミスと混ぜない。"""
    race = make_race(marks=[('◎', 4), ('○', 7)], bet_list=[('馬連', [4, 7], 100)])
    result = make_result([(1, 11), (2, 4), (3, 2)])
    assert classify(race, result) == review.BET_MISS


def test_印が2頭3着以内なら本命が飛んでいても買い目構成ミス():
    race = make_race(marks=[('◎', 4), ('○', 11), ('▲', 2)],
                     bet_list=[('馬連', [4, 11], 100)])
    result = make_result([(1, 11), (2, 2), (3, 9)])
    assert classify(race, result) == review.BET_MISS


def test_印が1頭も来ていなければ予想ミス():
    race = make_race(marks=[('◎', 4), ('○', 7)], bet_list=[('馬連', [4, 7], 100)])
    result = make_result([(1, 11), (2, 2), (3, 9)])
    assert classify(race, result) == review.PREDICT_MISS


def test_買い目が無ければ見送り():
    race = make_race(marks=[('◎', 4)], bet_list=[])
    result = make_result([(1, 11), (2, 2), (3, 9)])
    assert classify(race, result) == review.SKIPPED


# ----------------------------------------------------------------------
# しきい値カウンタ
# ----------------------------------------------------------------------

def test_3着以内の無印馬を数える():
    race = make_race(marks=[('◎', 4), ('○', 11)], bet_list=[('単勝', [4], 100)])
    result = make_result([(1, 4), (2, 8), (3, 9)])
    unmarked = review.unmarked_good_runs(race, result)
    assert [h['umaban'] for h in unmarked] == [8, 9]


def test_本命一極集中で全滅した形を数える():
    """2026-08-08 エルムSと同じ形。◎着外・○と△が3着以内・買い目は全て◎絡み。"""
    race = make_race(
        marks=[('◎', 2), ('○', 11), ('▲', 9), ('△', 3)],
        bet_list=[('馬連', [2, 11], 100), ('馬連', [2, 9], 100), ('馬連', [2, 3], 100)],
    )
    result = make_result([(1, 3), (2, 11), (3, 5), (4, 2)])
    settlement = results_module.settle(race, result)
    assert review.honmei_only_wipeout(race, settlement, result) is True


def test_本命が走っていれば一極集中とは数えない():
    """それは軸の選び方ではなく買い目の広げ方の問題なので別に数える。"""
    race = make_race(marks=[('◎', 2), ('○', 11)],
                     bet_list=[('馬連', [2, 11], 100)])
    result = make_result([(1, 3), (2, 2), (3, 5)])
    settlement = results_module.settle(race, result)
    assert review.honmei_only_wipeout(race, settlement, result) is False


def test_単勝1点買いは一極集中とは数えない():
    """第8章は「思考の集中と資金の持ち」を理由に単勝1点を選んでいる。
    1点しかない買い目に「分散すれば拾えた」は言いがかり。"""
    race = make_race(marks=[('◎', 6), ('○', 12), ('△', 4)],
                     bet_list=[('単勝', [6], 200)])
    result = make_result([(1, 4), (2, 11), (3, 12), (4, 6)])
    settlement = results_module.settle(race, result)
    assert review.honmei_only_wipeout(race, settlement, result) is False


def test_軸を分散していれば一極集中とは数えない():
    race = make_race(marks=[('◎', 2), ('○', 11), ('▲', 9)],
                     bet_list=[('馬連', [2, 11], 100), ('馬連', [11, 9], 100)])
    result = make_result([(1, 9), (2, 5), (3, 7)])
    settlement = results_module.settle(race, result)
    assert review.honmei_only_wipeout(race, settlement, result) is False


# ----------------------------------------------------------------------
# 集計
# ----------------------------------------------------------------------

def entry(category=review.PREDICT_MISS, staked=300, returned=0, blocked=False,
          honmei_rank=None, unmarked=(), wipeout=False):
    return {
        'category': category, 'staked': staked, 'returned': returned,
        'blocked': blocked, 'honmei_rank': honmei_rank,
        'unmarked_good_runs': list(unmarked), 'honmei_wipeout': wipeout,
    }


def test_規律で止めたレースは規律適用後の収支から外れる():
    entries = [
        entry(category=review.HIT, staked=300, returned=900),
        entry(staked=300, returned=0, blocked=True),
    ]
    summary = review.summarize(entries)
    assert summary['virtual']['staked'] == 600
    assert summary['virtual']['profit'] == 300
    # 止めた300円は投資していない
    assert summary['disciplined']['staked'] == 300
    assert summary['disciplined']['profit'] == 600
    assert summary['blocked']['races'] == 1


def test_止めたのに当たっていたら逃した上振れとして数える():
    entries = [entry(category=review.HIT, staked=300, returned=1500, blocked=True)]
    summary = review.summarize(entries)
    assert summary['missed_upside'] == 1
    # 規律適用後は1レースも買っていないので回収率は出さない
    assert summary['disciplined']['roi'] is None


def test_結果未確定のレースは集計に入れない():
    entries = [entry(category=review.UNSETTLED, staked=0),
               entry(category=review.HIT, staked=100, returned=300)]
    summary = review.summarize(entries)
    assert summary['unsettled'] == 1
    assert summary['virtual']['races'] == 1


def test_本命の勝率と複勝率():
    entries = [entry(honmei_rank=1), entry(honmei_rank=3), entry(honmei_rank=8),
               entry(honmei_rank=None)]
    summary = review.summarize(entries)
    assert summary['honmei_races'] == 3     # ◎が完走した3レース
    assert summary['honmei_win'] == 1
    assert summary['honmei_place'] == 2


# ----------------------------------------------------------------------
# 検算記録の読み方（実データを使う）
# ----------------------------------------------------------------------

def test_同じ日に何度検算していても最後の判定を採る():
    """2026-08-08 の関越Sは朝は通り、14:15以降にBLOCKへ変わった。
    発注するかを決めたのは最後の判定なので、それが残らなければならない。"""
    checks = review.load_final_checks(date(2026, 8, 8))
    assert checks['202604020507']['blocked'] is True
    assert checks['202601010511']['blocked'] is False


def test_検算記録が無い日は空で返す():
    assert review.load_final_checks(date(2020, 1, 1)) == {}


# ----------------------------------------------------------------------
# 出力
# ----------------------------------------------------------------------

def test_レビューを書き出せる(tmp_path, monkeypatch):
    monkeypatch.setattr(review, 'REVIEW_DIR', str(tmp_path))
    summary = review.summarize([entry(category=review.HIT, staked=300, returned=900)])
    text = review.render([], summary, summary, (date(2026, 8, 3), date(2026, 8, 9)))
    path = review.write_review(text, date(2026, 8, 9))
    assert os.path.exists(path)
    body = open(path, encoding='utf-8').read()
    assert '規律は収支に効いたか' in body
    assert '仮想成績' in body


def test_メール本文に収支と差引が入る():
    summary = review.summarize([
        entry(category=review.HIT, staked=300, returned=900),
        entry(staked=300, blocked=True),
    ])
    body = review.render_mail(summary, (date(2026, 8, 3), date(2026, 8, 9)), 'data/review/x.md')
    assert '仮想成績' in body
    assert '規律適用後' in body
    assert '差引' in body


def test_しきい値到達はメール本文にも出す():
    # .md ファイルにしか出ないと、メールしか見ない運用では気づけない。
    week = review.summarize([])
    total = review.summarize(
        [entry(wipeout=True) for _ in range(review.COUNTER_THRESHOLD)])
    body = review.render_mail(week, (date(2026, 8, 3), date(2026, 8, 9)), 'x.md',
                              total_summary=total)
    assert 'メソッド見直しの検討どきです' in body
    assert '◎一極集中で全滅' in body


def test_しきい値未満ならメール本文に検討どき欄を出さない():
    week = total = review.summarize([entry(wipeout=True)])
    body = review.render_mail(week, (date(2026, 8, 3), date(2026, 8, 9)), 'x.md',
                              total_summary=total)
    assert 'メソッド見直しの検討どきです' not in body


def test_total_summaryを渡さなければ従来どおり静か():
    week = review.summarize([entry(wipeout=True)])
    body = review.render_mail(week, (date(2026, 8, 3), date(2026, 8, 9)), 'x.md')
    assert 'メソッド見直しの検討どきです' not in body
