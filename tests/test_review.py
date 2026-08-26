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
# 配当の偏り（少数の高配当に引っ張られていないか）
# ----------------------------------------------------------------------

def test_的中が無ければ配当統計はNone():
    assert review.payout_stats([]) is None
    assert review.payout_stats([entry(category=review.PREDICT_MISS, staked=300)]) is None


def test_配当の中央値と最小最大():
    rows = [
        entry(category=review.HIT, staked=100, returned=210),
        entry(category=review.HIT, staked=100, returned=900),
        entry(category=review.HIT, staked=100, returned=1830),
        entry(category=review.PREDICT_MISS, staked=100, returned=0),
    ]
    stats = review.payout_stats(rows)
    assert stats['count'] == 3
    assert stats['median'] == 900
    assert stats['min'] == 210
    assert stats['max'] == 1830


def test_上位1件と3件を除いた回収率():
    """賭け金は減らさない。買ったこと自体は事実として残す。"""
    rows = [
        entry(category=review.HIT, staked=100, returned=210),
        entry(category=review.HIT, staked=100, returned=900),
        entry(category=review.HIT, staked=100, returned=1830),
        entry(category=review.PREDICT_MISS, staked=100, returned=0),
    ]
    stats = review.payout_stats(rows)
    # 総投資400円、総払戻2,940円。上位1件(1830円)を除くと1,110円。
    assert stats['roi_excl_top1'] == pytest.approx(1110 / 400)
    # 上位3件（＝全的中）を除くと払戻0円。
    assert stats['roi_excl_top3'] == pytest.approx(0.0)


def test_的中が3件未満でも上位3件除外はあるだけ除いて計算する():
    rows = [entry(category=review.HIT, staked=100, returned=500)]
    stats = review.payout_stats(rows)
    assert stats['roi_excl_top3'] == pytest.approx(0.0)   # 1件しかないので全部除く


def test_summarizeがpayout_statsを規律適用後の的中から作る():
    """止めた(blocked)レースの配当は診断に含めない。規律適用後の実運用が対象。"""
    entries = [
        entry(category=review.HIT, staked=100, returned=900),
        entry(category=review.HIT, staked=100, returned=300, blocked=True),
    ]
    summary = review.summarize(entries)
    assert summary['payout_stats']['count'] == 1
    assert summary['payout_stats']['median'] == 900


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


def test_配当の偏りがrenderの規律の節のすぐ下に出る():
    summary = review.summarize([
        entry(category=review.HIT, staked=100, returned=210),
        entry(category=review.HIT, staked=100, returned=1830),
    ])
    text = review.render([], summary, summary, (date(2026, 8, 3), date(2026, 8, 9)))
    effect_idx = text.index('## 規律は収支に効いたか')
    payout_idx = text.index('### 配当の偏り')
    race_idx = text.index('## レース別')
    # 「規律は収支に効いたか」のすぐ下、「レース別」より上に置く
    assert effect_idx < payout_idx < race_idx
    assert '中央値' in text
    assert '1,020円' in text            # (210+1830)/2


def test_配当の偏りは的中0件でも表を壊さない():
    summary = review.summarize([entry(category=review.PREDICT_MISS)])
    text = review.render([], summary, summary, (date(2026, 8, 3), date(2026, 8, 9)))
    assert '### 配当の偏り' in text
    assert '0件' in text


def test_メール本文にも配当の偏りが出る():
    week = review.summarize([
        entry(category=review.HIT, staked=100, returned=210),
        entry(category=review.HIT, staked=100, returned=1830),
    ])
    body = review.render_mail(week, (date(2026, 8, 3), date(2026, 8, 9)), 'x.md',
                              total_summary=week)
    assert '配当(規律適用後) 今週' in body
    assert '配当(規律適用後) 通算' in body


# ----------------------------------------------------------------------
# 件名サフィックス（結果取得の再試行、2026-08-18 追加）
# ----------------------------------------------------------------------

def test_件名にサフィックスを付けられる(tmp_path, monkeypatch):
    """月曜のレビューで結果が欠けていた場合、火曜に再取得して送るメールが
    月曜と同じ件名だと、単なる誤送信・重複に見えてしまう
    （実例：2026-08-17、12レースの結果が取得できておらず、2日後の再取得で
    全て揃った。件名で「再試行」と分かるようにする）。"""
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path / 'bets'))
    monkeypatch.setattr(review, 'REVIEW_DIR', str(tmp_path / 'review'))

    import mailer as mailer_module
    sent = []
    monkeypatch.setattr(mailer_module.Mailer, 'is_configured', lambda self: True)
    monkeypatch.setattr(mailer_module.Mailer, 'send',
                        lambda self, subject, body: sent.append(subject) or True)

    exit_code = review.main(['--end', '2026-08-17', '--days', '7', '--mail',
                             '--subject-suffix', '（結果取得の再試行）'])

    assert exit_code in (review.EXIT_OK, review.EXIT_NEEDS_ATTENTION)
    assert sent == ['週次レビュー 2026-08-11〜2026-08-17（結果取得の再試行）']


def test_サフィックス省略時は従来どおりの件名(tmp_path, monkeypatch):
    monkeypatch.setattr(bets, 'BETS_DIR', str(tmp_path / 'bets'))
    monkeypatch.setattr(review, 'REVIEW_DIR', str(tmp_path / 'review'))

    import mailer as mailer_module
    sent = []
    monkeypatch.setattr(mailer_module.Mailer, 'is_configured', lambda self: True)
    monkeypatch.setattr(mailer_module.Mailer, 'send',
                        lambda self, subject, body: sent.append(subject) or True)

    review.main(['--end', '2026-08-17', '--days', '7', '--mail'])

    assert sent == ['週次レビュー 2026-08-11〜2026-08-17']


# ----------------------------------------------------------------------
# メール本文のレース別明細・暫定値の警告（2026-08-24 の指摘への対応）
# ----------------------------------------------------------------------

def _mail_entry(name, category, marks, top3, staked=300, returned=0, blocked=False):
    """レース別明細つきのエントリ。render_mail が読む形に合わせる。"""
    return {
        'name': name, 'category': category, 'staked': staked, 'returned': returned,
        'blocked': blocked, 'honmei_rank': None, 'unmarked_good_runs': [],
        'honmei_wipeout': False, 'marks': marks, 'top3': top3,
    }


def _collected(entries, day=date(2026, 8, 24)):
    return [{'date': day, 'entries': entries}]


def test_メール本文にレース別の明細が入る():
    """数字の要約だけでは何が起きたのか分からない、という指摘への対応。"""
    e = _mail_entry(
        'テストステークス', review.BET_MISS,
        marks=[{'mark': '◎', 'umaban': 8}, {'mark': '○', 'umaban': 7}],
        top3=[{'rank': 1, 'umaban': 8, 'mark': '◎'},
              {'rank': 2, 'umaban': 3, 'mark': None},
              {'rank': 3, 'umaban': 7, 'mark': '○'}])
    week = review.summarize([e])
    body = review.render_mail(week, (date(2026, 8, 18), date(2026, 8, 24)), 'x.md',
                              collected=_collected([e]))
    assert 'レース別' in body
    assert 'テストステークス' in body
    assert '◎8 ○7' in body        # 打った印が読める
    assert '1着8◎' in body         # 誰が走ったのかと、その印が読める
    assert '3着7○' in body


def test_メール本文の明細は見送りと購入を区別する():
    skipped = _mail_entry(
        '見送りステークス', review.SKIPPED,
        marks=[{'mark': '◎', 'umaban': 1}],
        top3=[{'rank': 1, 'umaban': 1, 'mark': '◎'}], staked=0)
    week = review.summarize([skipped])
    body = review.render_mail(week, (date(2026, 8, 18), date(2026, 8, 24)), 'x.md',
                              collected=_collected([skipped]))
    # 明細の行だけを見る（集計欄の「収支+0円」とは別物なので混ぜて判定しない）
    detail = body[body.index('レース別'):]
    assert '見送りステークス' in detail
    # 買っていないレースに収支の金額を出すと誤解を招く
    assert '+0円' not in detail
    assert '見送り' in detail


def test_未確定があるメールは暫定値だと先頭で言い切る():
    """2026-08-24、結果12件未取得のまま出た数字が確定値の4分の1だった。"""
    entries = [entry(category=review.UNSETTLED, staked=0),
               entry(category=review.HIT, staked=100, returned=500)]
    week = review.summarize(entries)
    body = review.render_mail(week, (date(2026, 8, 18), date(2026, 8, 24)), 'x.md')
    assert '暫定値' in body
    # 警告は数字より前に出す（読み飛ばされては意味がない）
    assert body.index('暫定値') < body.index('仮想成績')


def test_未確定が無ければ暫定値の警告は出さない():
    week = review.summarize([entry(category=review.HIT, staked=100, returned=500)])
    body = review.render_mail(week, (date(2026, 8, 18), date(2026, 8, 24)), 'x.md')
    assert '暫定値' not in body


def test_collectedを渡さなければ従来どおり明細なしで動く():
    week = review.summarize([entry(category=review.HIT, staked=100, returned=500)])
    body = review.render_mail(week, (date(2026, 8, 18), date(2026, 8, 24)), 'x.md')
    assert 'レース別' not in body
    assert '仮想成績' in body
