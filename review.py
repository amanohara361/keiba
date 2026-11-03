#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""週次レビュー（GitHub Actions から火曜に実行される。2026-09-08〜。
以前は月曜実行＋netkeiba側の結果反映遅れに備えた火曜再取得の2本立て
だったが、火曜まで待てば確実に反映されているため火曜1本に統一した）。

**数字の下ごしらえだけをする。文章は書かない。**

検証ノートの反省文は人（と Claude）が書くもので、機械が代わりに書くと
「それらしいが根拠のない総括」が毎週積み上がる。ここがやるのは、
検証ノートを書くときに手で数えていた部分だけ——着順との突き合わせ、
的中／不的中の分類、回収率、印の成績、しきい値カウンタ——である。

手で数えるのをやめる理由は実際にズレたからで、検証ノートには
「当初『6件』と記載していたが列挙は5レースで…正しくは5件」という
自己修正が残っている。数え間違いは反省の前提を壊す。

いちばん見たい数字は最後に置いた **仮想成績 vs 規律適用後** である。
「規律（第13章）を守ると本当に収支が良くなるのか」を毎週測り続ける。
守った結果として取り逃した上振れも同時に数え、10件たまったら基準値を
見直す——という運用（予想メソッド 第13章）を機械側で支える。

外部ライブラリは使わない。
"""

import argparse
import glob
import json
import logging
import os
import statistics
import sys
from datetime import date, timedelta

import bet_builder
import bet_gates
import bet_type_map
import bets
import edge_check
import jockey_edge
import nar as nar_module
import nar_csv
import results as results_module
import re
import weight_going

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))
REVIEW_DIR = os.path.join(ROOT, 'data', 'review')
RESULTS_DIR = os.path.join(ROOT, 'data', 'results')

# レースの分類。第13章の定義をそのまま使う。
HIT = '的中'
BET_MISS = '買い目構成ミス'      # 印は当たっていたのに買い目が拾えなかった
PREDICT_MISS = '予想ミス'        # 印そのものが外れた
SKIPPED = '見送り'               # 印だけ記録し購入しなかった（勝負度C など）
UNSETTLED = '結果未確定'

# しきい値。ここを超えたらメソッド側の見直しを検討する（第13章）。
COUNTER_THRESHOLD = 10

# 案A・案D（予想メソッド第2章運用ルール、2026-09-27 追加）が入った日。
# 週次レビューはこの日以降だけの列も出し、改訂が効いているかを通算と分けて見る。
MARK_RULES_SINCE = date(2026, 9, 28)

# 仮想の買い目「◎×partners（中穴候補）のワイド」の観察（2026-10-04〜）。
# 凱旋門賞（◎ダリズ1着・妙味馬ベイシティローラー2着）から出た発想で、
# バックテスト（data/review/wide_axis_backtest_2026-10-04.md）では現行の買い目より
# 良かったが、偶然と区別できなかった。実際には買わず、ここで数え続ける。
# 9/16 以降（発走前オッズが記録に残る期間）で VIRTUAL_WIDE_REVIEW_AT レースに
# 達したら、bet_builder の候補に加えるかを判断する。
VIRTUAL_WIDE_SINCE = date(2026, 9, 16)
VIRTUAL_WIDE_REVIEW_AT = 150

# 中穴候補（partners）の成績を人気帯ごとに、同じレースの同じ人気帯の全馬と比べる（2026-10-14〜）。
# 3連単「◎1着固定・5〜8番人気から2頭を2・3着」を検討した際、8/15〜10/12 の集計で
# 5〜8番人気の partners の連対率は 9.2%（185頭）と、同じレースの5〜8番人気全体（11.8%）を
# 上回っていなかった。穴の選別力が育つかを毎週同じ物差しで見る。判定はしない、数字だけ。
# (キー, 見出し, 下限人気, 上限人気。None は上限なし)
PARTNER_BANDS = [
    ('fav', '1〜4番人気', 1, 4),
    ('mid', '5〜8番人気', 5, 8),
    ('long', '9番人気以下', 9, None),
]

# ◎以外の保険ワイド（bet_builder._axis_hedge）の相手を、印の順で選ぶか勝率の順で選ぶか（2026-10-27〜）。
# 今は印の順（○▲△の先頭）。菊花賞では▲5（勝率0.075）が選ばれ、△10（0.13）なら当たっていた。
# 9/16〜10/25 の136レースのうち相手が食い違った47レースでは、3着内は印の順11・勝率の順18、
# 回収率は84%対72%（最大払戻1回を抜くと37%対57%）で、偶然と区別できなかった。
# 実際の買い方は変えず、相手が食い違ったレースが HEDGE_REVIEW_AT に達したら比べ直す。
HEDGE_REVIEW_AT = 100

# render() とメールの両方がここを見る。**片方だけ書き換えて対応が崩れる、という
# 事故を構造的に防ぐ。**
#
# **しきい値で知らせるのは「最後に判断した日（since）以降の件数」だけ（2026-11-03・ユーザー承認）。**
# 以前は通算件数で判定していたので、一度10件を超えると毎週「検討どき」が出続け、
# 判断した後も通知が止まらなかった（減点・原則で切った馬の好走は通算286件、◎一極集中は32件）。
# 判断したら since をその日に進め、数え直す。since=None は「まだ一度も判断していない」で、通算で数える。
#
# alert=False は参考の件数で、しきい値では知らせない。
# 「減点・原則で切った馬の好走」は「3着以内に来た無印馬の数」＝印で拾えなかった3着内の数で、
# 1レースに印が5頭前後なら毎週十数件は必ず増える。原則が厳しすぎるかは、
# 同じ頭数を人気上位から選んだ場合との比較（render_mark_popularity の表）で判断する。
THRESHOLD_COUNTERS = [
    {'label': '減点・原則で切った馬の好走（3着以内の無印馬）', 'key': 'unmarked_good_runs',
     'note': '参考の件数（しきい値では知らせない）。原則が厳しすぎるかは「印と人気上位の比較」の表で判断する',
     'alert': False, 'since': None},
    {'label': '規律遵守により逃した上振れ（止めたが的中していた）', 'key': 'missed_upside',
     'note': '基準値（合成3.0倍・期待値1.2）が厳しすぎないかを検討する',
     'alert': True, 'since': None},
    # 2026-09-08 に保険ワイド（bet_builder._axis_hedge）を入れた。2026-11-03 に、
    # 全滅の割合と保険が付いた数で効き目を見ることにして、ここから数え直す。
    {'label': '◎一極集中で全滅（◎着外・他の印は3着以内）', 'key': 'honmei_wipeout',
     'note': '保険ワイド（2026-09-08〜）が効いているかを、全滅の割合と保険の付いた数で見直す',
     'alert': True, 'since': date(2026, 11, 3)},
]


def counter_status(total_summary, since_counts=None):
    """しきい値カウンタの状態を1件ずつ返す。

    since_counts は {key: since 以降の件数}。since のあるカウンタで渡されなければ
    0件として扱う（数え直した後の件数が分からないのに、通算で知らせて誤報するより静かな方がよい）。
    """
    rows = []
    for c in THRESHOLD_COUNTERS:
        total = total_summary[c['key']]
        if c['since']:
            count = (since_counts or {}).get(c['key'], 0)
        else:
            count = total
        rows.append({**c, 'total': total, 'count': count,
                     'reached': c['alert'] and count >= COUNTER_THRESHOLD})
    return rows


def since_counts_for(history):
    """main() 用：since のあるカウンタについて、since 以降の件数を数える。"""
    out = {}
    for c in THRESHOLD_COUNTERS:
        if c['since']:
            entries = [e for day in history if day['date'] >= c['since'] for e in day['entries']]
            out[c['key']] = summarize(entries)[c['key']]
    return out

# 終了コードの意味は check.py と揃える。
EXIT_OK = 0
EXIT_ERROR = 1             # 知らせられなかった（送信失敗・設定不備）
EXIT_NEEDS_ATTENTION = 3   # 結果が1件も取れなかった等、要確認だが通知はできた


# ----------------------------------------------------------------------
# 入力をそろえる
# ----------------------------------------------------------------------

def load_prestart_checks(day):
    """その日の検算記録から、レースごとに単勝オッズを持つ最後の判定を取り出す。

    発走後の検算は win_odds が空になるので、最後の判定ではなく
    「発走前の最後」を採る（保険の相手の比較で勝率を出すのに使う）。
    """
    path = os.path.join(bets.CHECKS_DIR, f'{day.isoformat()}.json')
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        history = json.load(f)

    latest = {}
    for entry in history:
        for race in entry.get('races', []):
            if race.get('win_odds'):
                latest[str(race.get('race_id'))] = race
    return latest


def load_final_checks(day):
    """その日の検算記録から、レースごとの **最後の** 判定を取り出す。

    data/checks/YYYY-MM-DD.json は1日に何度も追記される（朝・12時・直前）。
    発注するかどうかを決めたのは最後の判定なので、それだけを採る。
    """
    path = os.path.join(bets.CHECKS_DIR, f'{day.isoformat()}.json')
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        history = json.load(f)

    latest = {}
    for entry in history:
        for race in entry.get('races', []):
            latest[str(race.get('race_id'))] = race
    return latest


def cached_result(race_id):
    path = os.path.join(RESULTS_DIR, f'{race_id}.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save_result(result):
    """確定結果はレースごとに保存する。

    着順は二度と変わらないので、一度取れば十分。取り直さないことで、
    レビューを何度流し直しても netkeiba への負荷が増えず、
    テストもネットワーク無しで実データを使える。
    """
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"{result['race_id']}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return path


def get_result(race_id, fetcher=None, use_cache=True, org='jra'):
    """確定結果を返す。まだ出ていなければ None。

    取得元は主催者で決まる。中央は netkeiba の結果ページ、地方は
    公式CSVの払戻金・出馬表。**着順と払戻の形は同じ**なので、
    その先の精算（results.settle）も集計も分岐しない。

    org は race_id からは判別できない（どちらも12桁）。買い目ファイルの
    org をそのまま渡すこと。
    """
    if use_cache:
        hit = cached_result(race_id)
        if hit:
            return hit
    if org == 'nar':
        result = nar_module.results_for(race_id)
    else:
        result = results_module.fetch(race_id, fetcher=fetcher)
    if result:
        save_result(result)
    return result


# ----------------------------------------------------------------------
# 1レースの評価
# ----------------------------------------------------------------------

def top_finishers(result, n=3):
    return [h for h in result['finishing_order'] if h['rank'] <= n]


def classify(race, settlement, result):
    """第13章の定義でレースを分類する。

    買い目構成ミス … 買い目は全外れだが、◎が3着以内 または 印が2頭以上3着以内。
                      「馬は見えていたのに買い方で落とした」局面。
    予想ミス       … それ以外の不的中。印そのものが違った。

    この2つを混ぜると反省の向きを間違える。買い方の問題を馬の見方の
    問題として反省しても、翌週また同じ買い方で落とす。
    """
    if not race.bets:
        return SKIPPED
    if settlement['hit']:
        return HIT

    top3 = top_finishers(result)
    honmei = race.horses_for('◎')
    honmei_in_top3 = any(h['umaban'] in honmei for h in top3)
    marked_in_top3 = sum(1 for h in top3 if h['umaban'] in race.marked_horses)

    if honmei_in_top3 or marked_in_top3 >= 2:
        return BET_MISS
    return PREDICT_MISS


def unmarked_good_runs(race, result):
    """減点・原則で切った馬が好走した件数（3着以内の無印馬）。

    第13章のカウンタその1。10件たまったら「原則そのものが厳しすぎないか」を
    検討する。個別のレースで悔しがるのではなく、件数で判断するための仕組み。
    """
    return [h for h in top_finishers(result) if race.mark_of(h['umaban']) is None]


def honmei_only_wipeout(race, settlement, result):
    """◎を軸に一極集中し、◎が飛んで全滅したか。

    2026-08-08のエルムSがこれだった。◎2が着外に沈み、○11が2着・△3が1着
    だったのに、買い目が全て◎絡みだったので何も残らなかった。
    現行の規律（lone_partner）はこの形を検知できない——相手は広げてあり、
    軸が1頭に集中していることを見ていないため。

    **単勝1点買いは数えない。** 買い目が1点しかないレースに
    「軸を分散すれば拾えた」は言いがかりで、第8章はむしろ
    「思考の集中と資金の持ち」を理由に単勝1点を選んでいる。
    軸の置き方を問えるのは、複数点に分けるという選択があった場合だけ。
    最初この条件を入れておらず、2026-08-09のUHB賞とレパードS
    （どちらも単勝1点）まで数えて3件と出た。本当は1件である。

    検証ノートの改訂候補として件数だけ数えておく。規律に足すかどうかは
    人が決めることなので、ここでは判定も警告もしない。

    【2026-09-15 変更】以前は `len(race.bets) < 2` で単一買い目のレースを
    丸ごと除外していたが、上の「単勝1点買いは数えない」根拠は単勝にしか
    当てはまらない。bet_builderが実オッズで買い目を組む今の方式では、
    3連複1点・ワイド1点になるのは合成オッズ・期待値という規律の結果で
    あって、「軸を分散する選択が無かった」わけではない。そのため除外を
    「単勝1点買いだけ」に絞った。2026-09-07エントリのパターン①4件のうち
    3件（セプテンバーS・紫苑S・石川テレビ杯）と2026-09-14のセントライト
    記念（3連複2-5-10の1点）が、実態は◎一極集中の全滅なのにこの条件で
    数え漏れていた。
    """
    if not race.bets or settlement['hit']:
        return False
    if len(race.bets) == 1 and race.bets[0].type == '単勝':
        return False
    honmei = set(race.horses_for('◎'))
    if not honmei:
        return False
    # 全ての買い目が◎絡み
    if not all(honmei & set(bet.horses) for bet in race.bets):
        return False
    top3 = {h['umaban'] for h in top_finishers(result)}
    if honmei & top3:
        return False          # ◎は走っている（それは買い目構成の問題）
    # ◎以外の印が3着以内に来ていた＝軸を変えれば拾えた
    others = race.marked_horses - honmei
    return bool(others & top3)


def wipeout_eligible(race):
    """◎一極集中を問えるレースか（honmei_only_wipeout と同じ前提）。

    買い目があり、単勝1点買いではなく、◎がいるレース。全滅の割合の分母にする。
    """
    if not race.bets or not race.horses_for('◎'):
        return False
    return not (len(race.bets) == 1 and race.bets[0].type == '単勝')


def has_hedge(race):
    """◎を含まない買い目（保険ワイド）が付いていたか。

    bet_builder._axis_hedge が成立すると、◎以外の2頭のワイドが1点足される。
    保険が付いたレースは、◎が飛んでも全滅に数えられない（honmei_only_wipeout）。
    """
    honmei = set(race.horses_for('◎'))
    return wipeout_eligible(race) and any(not (honmei & set(b.horses)) for b in race.bets)


def mark_vs_popularity(race, result):
    """印を人気順と比べる（2026-09-27〜、案A・案Dの効き目を測る物差し）。

    145レースを集計したところ、3着以内に来た馬のうち印の中にいたのは59%で、
    印と同じ頭数を人気上位から選んだ場合（71%）に負けていた。原因は、人気上位を
    外して人気薄と入れ替えていたこと（`data/review/mark_rules_2026-09-27.md`）。
    この比較を毎週出して、印が人気順に追いつくかを見る。

    人気は確定時の人気で、印を打った朝の人気とは違うことがある。着順表に
    人気が欠けている馬がいれば比べられないので None を返す。
    """
    order = result['finishing_order']
    if not race.marks or not order or any(h.get('ninki') is None for h in order):
        return None
    top3 = {h['umaban'] for h in order if h['rank'] <= 3}
    marked = race.marked_horses
    by_popularity = sorted(order, key=lambda h: h['ninki'])
    popular_top = {h['umaban'] for h in by_popularity[:len(marked)]}

    # 案A：1〜3番人気を印からも partners からも外した（完全に消した）馬
    kept = marked | {p['umaban'] for p in race.partners}
    cut = [h for h in order if h['ninki'] <= 3 and h['umaban'] not in kept]

    # 案D：地方で◎を1番人気から外したレース
    nar_favorite_off = None
    honmei = set(race.horses_for('◎'))
    favorite = next((h for h in order if h['ninki'] == 1), None)
    if race.org == 'nar' and honmei and favorite and favorite['umaban'] not in honmei:
        nar_favorite_off = {
            'favorite_won': favorite['rank'] == 1,
            'honmei_won': any(h['rank'] == 1 for h in order if h['umaban'] in honmei),
        }

    return {
        'favorite_won': bool(favorite and favorite['rank'] == 1),
        'top3': len(top3),
        'marked_top3': len(top3 & marked),
        'popular_top3': len(top3 & popular_top),
        'popular_cut': len(cut),
        'popular_cut_top3': sum(1 for h in cut if h['rank'] <= 3),
        'nar_favorite_off': nar_favorite_off,
    }


# **仮想：◎を軸にした単純な買い方（2026-11-03〜・観察中・実際には買っていない）。**
# 2026-08-08〜11-01 の261レースで、今の実運用（中央42%・地方5%）より、◎の単勝を全レースで
# 買うだけの方が良かった（中央112%・最大払戻を除いて104%、地方55%）。買い目の規律が主観勝率で
# 期待値を出すため、主観が市場とずれた（＝見立てが外れやすい）レースほど買っていた（決定ログ 2026-11-03）。
# 「損しなかった」ではなく、同じレースで単純に買っていたらいくら当たったかを毎週並べる。
# (名前, [(券種, 印の並び)])。印の並びは ◎・○ の順に馬番を取る。全点100円。
SIMPLE_BETS = [
    ('◎単勝', [('単勝', ('◎',))]),
    ('◎複勝', [('複勝', ('◎',))]),
    ('◎単勝＋◎複勝', [('単勝', ('◎',)), ('複勝', ('◎',))]),
    ('ワイド◎-○', [('ワイド', ('◎', '○'))]),
    ('馬連◎-○', [('馬連', ('◎', '○'))]),
]


def simple_axis_bets(race, result):
    """SIMPLE_BETS を確定払戻で精算する。印が足りない買い方は入れない。"""
    payouts = result.get('payouts') or {}
    out = {}
    for name, legs in SIMPLE_BETS:
        horses_by_mark = {m: race.horses_for(m) for _, marks in legs for m in marks}
        if any(not h for h in horses_by_mark.values()):
            continue
        returned = sum(results_module.payout_for(payouts, bet_type,
                                                 [horses_by_mark[m][0] for m in marks])
                       for bet_type, marks in legs)
        out[name] = {'staked': 100 * len(legs), 'returned': returned}
    return out


def virtual_axis_partner_wide(race, result):
    """◎×partners 全頭のワイドを各100円買っていたらどうだったか。

    ◎か partners が無いレースは None（買い目が組めない）。払戻は確定払戻で精算する。
    """
    honmei = race.horses_for('◎')
    partners = [p['umaban'] for p in race.partners if p.get('umaban') not in honmei]
    if not honmei or not partners:
        return None
    axis = honmei[0]
    payouts = result.get('payouts') or {}
    returned = [results_module.payout_for(payouts, 'ワイド', [axis, p]) for p in partners]
    return {'bets': len(partners), 'hits': sum(1 for y in returned if y > 0),
            'staked': 100 * len(partners), 'returned': sum(returned)}


def hedge_partner_compare(race, result, prestart_check):
    """保険ワイドの相手を「印の順」と「勝率の順」で選んだら、どちらが当たったか。

    相棒の軸（○、無ければ▲△）は bet_builder._axis_hedge と同じに選び、相手の候補も
    同じ相手プール（印の序列順 → partners）から取る。勝率は発走前の最後の検算の
    単勝オッズに朝の主観勝率を当てたもの。**実際に買ったかどうかとは関係なく**、
    全レースで同じ物差しを当てる。ワイドのオッズが取れたかや、馬体重による
    上乗せの取り消しは見ない（選び方の比較だけをする）。

    比べられないレース（主観勝率・発走前の単勝オッズ・相棒の軸・相手が無い）は None。
    2つの選び方が同じ馬になれば {'differ': False}。
    """
    if not prestart_check or not race.win_probabilities:
        return None
    win_odds = {int(k): v for k, v in (prestart_check.get('win_odds') or {}).items() if v}
    market = bet_builder.market_win_probabilities(win_odds)
    honmei = race.horses_for('◎')
    if not market or not honmei or honmei[0] not in market:
        return None
    axis = honmei[0]
    overrides = {k: v for k, v in race.win_probabilities.items() if k in market}
    p = bet_builder.apply_subjective(market, overrides)
    co_axis = next((h for mark in bets.PARTNER_ORDER for h in race.horses_for(mark)
                    if h != axis and h in p), None)
    if co_axis is None:
        return None
    pool = [h for h in race.partner_pool if h in p and h not in (axis, co_axis)]
    if not pool:
        return None
    by_mark = pool[0]
    by_prob = max(pool, key=lambda h: p[h])   # 同率なら印の順で先の馬
    if by_mark == by_prob:
        return {'differ': False}

    ranks = {h['umaban']: h['rank'] for h in result['finishing_order']}
    payouts = result.get('payouts') or {}

    def side(h):
        rank = ranks.get(h)
        return {'umaban': h, 'top3': isinstance(rank, int) and rank <= 3,
                'returned': results_module.payout_for(payouts, 'ワイド', [co_axis, h])}

    return {'differ': True, 'co_axis': co_axis,
            'mark': side(by_mark), 'prob': side(by_prob)}


def _band_of(ninki):
    for key, _, low, high in PARTNER_BANDS:
        if ninki >= low and (high is None or ninki <= high):
            return key
    return None


def _empty_band_counts():
    return {key: {'n': 0, 'win': 0, 'quin': 0, 'place': 0} for key, *_ in PARTNER_BANDS}


def partner_popularity(race, result):
    """中穴候補（partners）と、同じレースの全馬を、人気帯ごとに着順で数える。

    全馬の側は「何も考えずにその人気帯から選んだら」の物差し。partners が
    これを上回らなければ、穴を選ぶ力は市場（人気）以上には無いことになる。
    partners が無いレースは None。人気や着順が欠けた馬（取消・中止など）は数えない。
    人気は確定時のもので、partners を書いた朝の人気とは違うことがある。
    """
    partners = {p.get('umaban') for p in race.partners}
    if not partners:
        return None
    counts = {'partners': _empty_band_counts(), 'field': _empty_band_counts()}
    for h in result['finishing_order']:
        ninki, rank = h.get('ninki'), h.get('rank')
        if not isinstance(ninki, int) or not isinstance(rank, int):
            continue
        band = _band_of(ninki)
        if band is None:
            continue
        sides = ['field'] + (['partners'] if h['umaban'] in partners else [])
        for side in sides:
            c = counts[side][band]
            c['n'] += 1
            c['win'] += rank == 1
            c['quin'] += rank <= 2
            c['place'] += rank <= 3
    return counts


def review_race(race, result, check, prestart_check=None):
    """1レース分の評価をまとめる。結果が未確定なら分類だけ空にする。"""
    entry = {
        'race_id': race.race_id,
        'org': race.org or 'jra',
        'name': race.name,
        'venue': race.venue,
        'confidence': race.confidence,
        'marks': race.marks,
        'blocked': bool(check.get('blocked')) if check else False,
        'block_reasons': [
            f['message'] for f in (check or {}).get('findings', [])
            if f.get('severity') == 'BLOCK'
        ],
        'staked': 0,
        'returned': 0,
        'bets': [],
    }

    if not result:
        entry['category'] = UNSETTLED
        return entry

    settlement = results_module.settle(race, result)
    entry.update({
        'staked': settlement['staked'],
        'returned': settlement['returned'],
        'bets': settlement['bets'],
        'category': classify(race, settlement, result),
        'top3': [
            {
                'rank': h['rank'],
                'umaban': h['umaban'],
                'name': h.get('name', ''),
                'odds': h.get('odds'),
                'ninki': h.get('ninki'),
                'mark': race.mark_of(h['umaban']),
            }
            for h in top_finishers(result)
        ],
        'unmarked_good_runs': [h['umaban'] for h in unmarked_good_runs(race, result)],
        'honmei_wipeout': honmei_only_wipeout(race, settlement, result),
        'wipeout_eligible': wipeout_eligible(race),
        'hedged': has_hedge(race),
        'mark_vs_popularity': mark_vs_popularity(race, result),
        'virtual_wide': virtual_axis_partner_wide(race, result),
        'simple_bets': simple_axis_bets(race, result),
        'partner_popularity': partner_popularity(race, result),
        'hedge_compare': hedge_partner_compare(race, result, prestart_check),
        # 券種の解禁条件（bet_gates）の材料。印・人気・着順・単勝オッズを全馬分
        'horse_rows': bet_gates.horse_rows(race, result),
    })

    # 印の成績。◎が1着だったか、3着以内だったか。
    honmei = race.horses_for('◎')
    if honmei:
        ranks = {h['umaban']: h['rank'] for h in result['finishing_order']}
        best = min((ranks[h] for h in honmei if h in ranks), default=None)
        entry['honmei_rank'] = best

    return entry


# ----------------------------------------------------------------------
# 配当の偏り
# ----------------------------------------------------------------------

def payout_stats(rows):
    """的中したレースの配当分布。少数の高配当に引っ張られて回収率を
    過大評価していないかを見るための診断。**判定はしない、数字を出すだけ。**

    回収率は「上位N件の配当を除いても、賭け金は変えずに」計算する
    （その馬券を買ったこと自体は事実なので、賭け金は減らさない）。
    的中がN件に満たない場合は、あるだけ除く（0件ならROIは0%になる。
    それも正しい数字なので隠さない）。
    """
    hits = sorted((e['returned'] for e in rows if e['category'] == HIT), reverse=True)
    if not hits:
        return None
    staked = sum(e['staked'] for e in rows)
    returned = sum(hits)

    def roi_excluding(n):
        if not staked:
            return None
        excluded = sum(hits[:min(n, len(hits))])
        return (returned - excluded) / staked

    return {
        'count': len(hits),
        'median': statistics.median(hits),
        'min': hits[-1],
        'max': hits[0],
        'roi_excl_top1': roi_excluding(1),
        'roi_excl_top3': roi_excluding(3),
    }


# ----------------------------------------------------------------------
# 集計
# ----------------------------------------------------------------------

def summarize(entries):
    """レース評価の一覧から KPI を出す。

    収支は2通り出す。
      仮想成績   … 買い目をそのまま全部買った場合
      規律適用後 … 規律が止めた（BLOCK）レースを除いた場合
    この差が「規律を守る価値」そのものである。差がマイナスに振れ続ける
    なら基準値が厳しすぎるということなので、そのときは基準を見直す。
    """
    settled = [e for e in entries if e['category'] != UNSETTLED]
    purchased = [e for e in settled if e['staked'] > 0]
    kept = [e for e in purchased if not e['blocked']]
    blocked = [e for e in purchased if e['blocked']]

    def totals(rows):
        staked = sum(e['staked'] for e in rows)
        returned = sum(e['returned'] for e in rows)
        return {
            'races': len(rows),
            'staked': staked,
            'returned': returned,
            'profit': returned - staked,
            'roi': (returned / staked) if staked else None,
            'hits': sum(1 for e in rows if e['category'] == HIT),
        }

    honmei_ranked = [e['honmei_rank'] for e in settled if e.get('honmei_rank')]
    compared = [e['mark_vs_popularity'] for e in settled if e.get('mark_vs_popularity')]
    favorite_off = [c['nar_favorite_off'] for c in compared if c['nar_favorite_off']]
    virtual = [e['virtual_wide'] for e in settled if e.get('virtual_wide')]
    hedges = [e['hedge_compare'] for e in settled if e.get('hedge_compare')]
    differ = [h for h in hedges if h['differ']]

    def hedge_side(key):
        returned = [h[key]['returned'] for h in differ]
        return {'top3': sum(1 for h in differ if h[key]['top3']),
                'hits': sum(1 for y in returned if y > 0),
                'returned': sum(returned),
                'max': max(returned, default=0)}
    partner_bands = {'partners': _empty_band_counts(), 'field': _empty_band_counts()}
    for e in settled:
        pp = e.get('partner_popularity')
        if not pp:
            continue
        for side, bands in pp.items():
            for band, c in bands.items():
                for k, v in c.items():
                    partner_bands[side][band][k] += v

    def simple_totals(rows):
        out = {}
        for e in rows:
            for name, v in (e.get('simple_bets') or {}).items():
                s = out.setdefault(name, {'races': 0, 'hits': 0, 'staked': 0, 'returned': 0, 'max': 0})
                s['races'] += 1
                s['hits'] += v['returned'] > 0
                s['staked'] += v['staked']
                s['returned'] += v['returned']
                s['max'] = max(s['max'], v['returned'])
        return out
    simple = simple_totals(settled)
    # 案E：従来の組み立てが買うと判定したか（legacy_decision）で分ける
    simple_by_legacy = {
        f'{side}{suffix}': {'simple': simple_totals(
            [e for e in settled if e.get(key) is flag])}
        for key, suffix in (('legacy_buy', ''), ('legacy_buy_no_floor', '_no_floor'))
        for side, flag in (('buy', True), ('skip', False))
    }

    return {
        'races': len(entries),
        'settled': len(settled),
        # 仮想：◎を軸にした単純な買い方（SIMPLE_BETS）
        'simple': simple,
        'simple_by_legacy': simple_by_legacy,
        'unsettled': sum(1 for e in entries if e['category'] == UNSETTLED),
        'skipped': sum(1 for e in settled if e['category'] == SKIPPED),
        'virtual': totals(purchased),
        'disciplined': totals(kept),
        'blocked': totals(blocked),
        # 規律適用後（実際に運用した場合）の的中に対する診断。
        # 「回収率（規律適用後）」が少数の高配当に引っ張られていないかを見る。
        'payout_stats': payout_stats(kept),
        'bet_miss': sum(1 for e in settled if e['category'] == BET_MISS),
        'predict_miss': sum(1 for e in settled if e['category'] == PREDICT_MISS),
        'honmei_races': len(honmei_ranked),
        'honmei_win': sum(1 for r in honmei_ranked if r == 1),
        'honmei_place': sum(1 for r in honmei_ranked if r <= 3),
        # しきい値カウンタ
        'unmarked_good_runs': sum(len(e.get('unmarked_good_runs', [])) for e in settled),
        'missed_upside': sum(1 for e in blocked if e['returned'] > e['staked']),
        'honmei_wipeout': sum(1 for e in settled if e.get('honmei_wipeout')),
        # ◎一極集中の割合を見るための分母と、保険ワイドが付いた数（2026-11-03〜）
        'wipeout_eligible': sum(1 for e in settled if e.get('wipeout_eligible')),
        'hedged': sum(1 for e in settled if e.get('hedged')),
        # 印と人気順の比較（案A・案D）
        'compared_races': len(compared),
        'favorite_win': sum(1 for c in compared if c['favorite_won']),
        'top3_total': sum(c['top3'] for c in compared),
        'top3_marked': sum(c['marked_top3'] for c in compared),
        'top3_popular': sum(c['popular_top3'] for c in compared),
        'popular_cut': sum(c['popular_cut'] for c in compared),
        'popular_cut_top3': sum(c['popular_cut_top3'] for c in compared),
        'nar_favorite_off': len(favorite_off),
        'nar_favorite_off_favorite_won': sum(1 for f in favorite_off if f['favorite_won']),
        'nar_favorite_off_honmei_won': sum(1 for f in favorite_off if f['honmei_won']),
        # 仮想：◎×partners のワイド（観察中、実際には買っていない）
        'vwide_races': len(virtual),
        'vwide_bets': sum(v['bets'] for v in virtual),
        'vwide_hits': sum(v['hits'] for v in virtual),
        'vwide_staked': sum(v['staked'] for v in virtual),
        'vwide_returned': sum(v['returned'] for v in virtual),
        # 中穴候補の人気帯別の成績と、同じレースの同じ人気帯の全馬
        'partner_races': sum(1 for e in settled if e.get('partner_popularity')),
        'partner_bands': partner_bands,
        # 保険ワイドの相手：印の順 vs 勝率の順（観察中、実際の買い方は印の順）
        'hedge_races': len(hedges),
        'hedge_differ': len(differ),
        'hedge_mark': hedge_side('mark'),
        'hedge_prob': hedge_side('prob'),
    }


# **案E の記録（2026-11-03・ユーザー承認）。** 中央の買い目を◎単複の固定形にした後も、
# 従来の組み立て（bet_builder.build_bets）が「買う」と判定したかを検算の買い目メモに残している。
# 「従来なら買うレース」と「従来なら見送るレース」で◎単複の成績が違うなら、build_bets は
# 買うレースを選ぶ役に使える（docs/調査/bet_builder改善案_2026-11-03.md 案E）。
# 11-03 より前は、実際に買ったかどうかがそのまま従来の判定。
FIXED_FORM_SINCE = date(2026, 11, 3)
LEGACY_DECISION_RE = re.compile(
    r'【参考：従来の組み立て】.*?（勝負度([ABC])(?:／足切りなしなら勝負度([ABC]))?）')


def legacy_decision(day, entry, prestart_check, no_floor=False):
    """従来の組み立てならそのレースを買ったか。分からなければ None。

    no_floor=True は足切り（的中率5%）を外した版の判定。検算メモに残るのは 2026-11-03 から。
    """
    if day < FIXED_FORM_SINCE:
        return None if no_floor else entry['staked'] > 0
    match = LEGACY_DECISION_RE.search((prestart_check or {}).get('bet_note') or '')
    if not match:
        return None
    grade = match.group(2) if no_floor else match.group(1)
    return grade in ('A', 'B') if grade else None


def collect(days, fetcher=None, use_cache=True):
    """指定した日付のレースを評価して、日ごとにまとめる。"""
    collected = []
    for day in days:
        sheet = bets.load_sheet(day)
        if not sheet:
            continue
        checks = load_final_checks(day)
        prestart = load_prestart_checks(day)
        entries = []
        for race in sheet.races:
            try:
                result = get_result(race.race_id, fetcher=fetcher,
                                    use_cache=use_cache, org=race.org)
            except (results_module.ResultsError, nar_module.NarError) as exc:
                logger.warning('%s: %s', race.race_id, exc)
                result = None
            entry = review_race(race, result, checks.get(race.race_id),
                                prestart.get(race.race_id))
            entry['legacy_buy'] = legacy_decision(day, entry, prestart.get(race.race_id))
            entry['legacy_buy_no_floor'] = legacy_decision(day, entry, prestart.get(race.race_id),
                                                           no_floor=True)
            entries.append(entry)
        collected.append({'date': day, 'source': sheet.source, 'entries': entries})
    return collected


def all_bet_days():
    """買い目ファイルがある日を古い順に返す（通算の集計に使う）。"""
    if not os.path.isdir(bets.BETS_DIR):
        return []
    days = []
    for name in os.listdir(bets.BETS_DIR):
        stem, ext = os.path.splitext(name)
        if ext != '.json':
            continue
        try:
            days.append(date.fromisoformat(stem))
        except ValueError:
            continue
    return sorted(days)


# ----------------------------------------------------------------------
# 出力
# ----------------------------------------------------------------------

def _yen(n):
    return f'{n:,}円'

def _pct(num, den):
    return f'{num}/{den}（{num / den * 100:.0f}%）' if den else '—'

def _roi(t):
    return f"{t['roi'] * 100:.0f}%" if t['roi'] is not None else '—'


def _payout_roi(pct):
    return f'{pct * 100:.0f}%' if pct is not None else '—'


# (見出し, payout_stats() の戻り値 -> 表示文字列)。None（的中0件）は "—"。
# render() の表と render_mail() の両方がここから作る。
PAYOUT_STAT_ROWS = [
    ('的中', lambda s: f"{s['count']}件" if s else '0件'),
    ('配当の中央値', lambda s: _yen(round(s['median'])) if s else '—'),
    ('配当（最小〜最大）', lambda s: f"{_yen(s['min'])}〜{_yen(s['max'])}" if s else '—'),
    ('回収率（上位1件除く）', lambda s: _payout_roi(s['roi_excl_top1']) if s else '—'),
    ('回収率（上位3件除く）', lambda s: _payout_roi(s['roi_excl_top3']) if s else '—'),
]


def _payout_stats_line(label, stats):
    if not stats:
        return f'{label}: 的中0件'
    return (f"{label}: 的中{stats['count']}件 "
            f"中央値{_yen(round(stats['median']))} "
            f"{_yen(stats['min'])}〜{_yen(stats['max'])} / "
            f"上位1件除く{_payout_roi(stats['roi_excl_top1'])} "
            f"上位3件除く{_payout_roi(stats['roi_excl_top3'])}")


def _totals_line(label, t):
    return (f"- {label}：{t['races']}レース 的中{t['hits']} "
            f"投資{_yen(t['staked'])} 払戻{_yen(t['returned'])} "
            f"収支{t['profit']:+,}円 回収率{_roi(t)}")


# (見出し, summarize() の戻り値 -> 表示文字列)。印と人気順の比較の表。
MARK_POPULARITY_ROWS = [
    ('3着以内の馬を印で拾えた割合', lambda s: _pct(s['top3_marked'], s['top3_total'])),
    ('同じ頭数を人気上位から選んだ場合', lambda s: _pct(s['top3_popular'], s['top3_total'])),
    ('1〜3番人気を完全に消した頭数（うち3着以内）',
     lambda s: f"{s['popular_cut']}頭（{s['popular_cut_top3']}頭）"),
    ('地方で◎を1番人気から外したレース（1番人気の勝ち／◎の勝ち）',
     lambda s: (f"{s['nar_favorite_off']}R（{s['nar_favorite_off_favorite_won']}／"
                f"{s['nar_favorite_off_honmei_won']}）")),
]


def render_mark_popularity(week_summary, total_summary, since_summary=None):
    """印と人気順の比較（案A・案Dの効き目）。判定はしない、数字だけ。"""
    lines = [
        f'## 印と人気上位の比較（案A・案D、{MARK_RULES_SINCE.isoformat()}〜）',
        '',
        '人気は確定時点のもの。改訂前（通算）は印59%対人気上位71%で負けていた。',
        '案Aが効いていれば、印の割合が人気上位に近づき、消した人気馬の3着以内は'
        '人気なり（改訂前の集計では、印の無い1〜3番人気の3着内率の見込みがおよそ55%）を'
        '下回る。案Dが効いていれば、地方で'
        '1番人気から◎を外すレース自体が減る。',
        '',
        f'| 指標 | 今週 | {MARK_RULES_SINCE.isoformat()}以降 | 通算 |',
        '|---|---|---|---|',
    ]
    for label, fn in MARK_POPULARITY_ROWS:
        since = fn(since_summary) if since_summary else '—'
        lines.append(f'| {label} | {fn(week_summary)} | {since} | {fn(total_summary)} |')
    lines.append('')
    return lines


ORGS = [('jra', '中央'), ('nar', '地方')]

# (見出し, summarize() の戻り値 -> 表示文字列)。中央・地方別の表。
ORG_ROWS = [
    ('確定レース', lambda s: str(s['settled'])),
    ('◎勝率', lambda s: _pct(s['honmei_win'], s['honmei_races'])),
    ('1番人気の勝率（参考）', lambda s: _pct(s['favorite_win'], s['compared_races'])),
    ('◎複勝率', lambda s: _pct(s['honmei_place'], s['honmei_races'])),
    ('3着以内を印で拾えた割合', lambda s: _pct(s['top3_marked'], s['top3_total'])),
    ('同じ頭数の人気上位なら', lambda s: _pct(s['top3_popular'], s['top3_total'])),
    ('1〜3番人気を完全に消した（うち3着以内）',
     lambda s: f"{s['popular_cut']}頭（{s['popular_cut_top3']}頭）"),
    ('回収率（規律適用後）', lambda s: _roi(s['disciplined'])),
    ('的中', lambda s: f"{s['disciplined']['hits']}/{s['disciplined']['races']}"),
]


def summarize_by_org(entries):
    """中央と地方を分けて集計する（2026-09-27〜）。

    メソッドを中央・地方で分けるべきかを数字で判断するための材料。
    分けるサインは (1) 同じルールが中央と地方で逆に効く、(2)「地方では〜」の
    例外が本文より長くなる、(3) 印の成績が両者で違う方向に動き続ける、の3つ
    （docs/決定ログ.md 2026-09-27）。まとめて集計すると (3) が見えない。
    """
    return {org: summarize([e for e in entries if e.get('org', 'jra') == org])
            for org, _ in ORGS}


def render_by_org(week_by_org, total_by_org):
    """中央・地方別の表。判定はしない、数字だけ。"""
    header = ' | '.join(f'{label} 今週 | {label} 通算' for _, label in ORGS)
    lines = [
        '## 中央・地方別',
        '',
        '1番人気の勝率は、印を比べられたレース（人気が全頭そろっている）での値。',
        '',
        f'| 指標 | {header} |',
        '|---|' + '---|' * (2 * len(ORGS)),
    ]
    for label, fn in ORG_ROWS:
        cells = ' | '.join(f'{fn(week_by_org[org])} | {fn(total_by_org[org])}'
                           for org, _ in ORGS)
        lines.append(f'| {label} | {cells} |')
    lines.append('')
    return lines


def _simple_cell(s, name, drop_max=False):
    v = (s or {}).get('simple', {}).get(name)
    if not v or not v['staked']:
        return '—'
    if drop_max:
        return f"{(v['returned'] - v['max']) / v['staked'] * 100:.0f}%"
    return f"{v['returned'] / v['staked'] * 100:.0f}%（{v['hits']}/{v['races']}R）"


def render_simple_bets(week_by_org, total_by_org):
    """仮想：◎を軸にした単純な買い方を、中央・地方別に並べる。判定はしない。"""
    header = ' | '.join(f'{label} 今週 | {label} 通算 | {label} 通算・最大払戻除く'
                        for _, label in ORGS)
    lines = [
        '## 仮想：◎を軸にした単純な買い方（観察中・実際には買っていない）',
        '',
        '見送りも含めた全レースで、◎（と○）を毎回同じ形で買っていたら、の成績。各点100円、確定払戻で精算。'
        'セルは「回収率（何か当たったレース/レース数）」。比べる相手は下の「実運用」と、控除後の水準'
        '（単勝・複勝 約80%、馬連・ワイド 約77.5%）。根拠は決定ログ 2026-11-03。',
        '',
        f'| 買い方 | {header} |',
        '|---|' + '---|' * (3 * len(ORGS)),
    ]
    for name, _ in SIMPLE_BETS:
        cells = ' | '.join(
            f'{_simple_cell(week_by_org[org], name)} | {_simple_cell(total_by_org[org], name)}'
            f' | {_simple_cell(total_by_org[org], name, drop_max=True)}'
            for org, _ in ORGS)
        lines.append(f'| {name} | {cells} |')
    cells = ' | '.join(
        f"{_roi(week_by_org[org]['disciplined'])}（{week_by_org[org]['disciplined']['hits']}/"
        f"{week_by_org[org]['disciplined']['races']}R） | "
        f"{_roi(total_by_org[org]['disciplined'])}（{total_by_org[org]['disciplined']['hits']}/"
        f"{total_by_org[org]['disciplined']['races']}R） | "
        for org, _ in ORGS)
    lines.append(f'| （実運用・規律適用後） | {cells} |')
    lines.append('')

    # 案E：従来の組み立てが買うと判定したレースと、見送ると判定したレース（中央のみ）
    week, total = week_by_org.get('jra'), total_by_org.get('jra')
    if week and total:
        lines += [
            '### 中央：従来の組み立て（bet_builder）の判定別（案E・観察中）',
            '',
            '従来の組み立てが「買う」と判定したレースと「見送る」と判定したレースで、◎の成績が違うかを見る。'
            '「買う」側がはっきり上回り続けるなら、従来の組み立てをレースの選別に使う（docs/調査/bet_builder改善案_2026-11-03.md 案E）。'
            '2026-11-03 より前は、実際に買ったかどうか。「足切りなし」は的中率5%の足切りを外した版の判定で、2026-11-03 から記録している。',
            '',
            '| 従来の判定 | ◎単勝 今週 | ◎単勝 通算 | ◎単勝 通算・最大払戻除く | ◎単複 今週 | ◎単複 通算 | ◎単複 通算・最大払戻除く |',
            '|---|---|---|---|---|---|---|',
        ]
        for key, label in (('buy', '買う'), ('skip', '見送る'),
                           ('buy_no_floor', '足切りなしなら買う'),
                           ('skip_no_floor', '足切りなしなら見送る')):
            w, tot = week['simple_by_legacy'][key], total['simple_by_legacy'][key]
            cells = ' | '.join(
                f'{_simple_cell(w, n)} | {_simple_cell(tot, n)} | {_simple_cell(tot, n, drop_max=True)}'
                for n in ('◎単勝', '◎単勝＋◎複勝'))
            lines.append(f'| {label} | {cells} |')
        lines.append('')
    return lines


def _vwide_roi(s):
    return f"{s['vwide_returned'] / s['vwide_staked'] * 100:.0f}%" if s['vwide_staked'] else '—'


VIRTUAL_WIDE_ROWS = [
    ('レース数', lambda s: str(s['vwide_races'])),
    ('点数（的中）', lambda s: f"{s['vwide_bets']}点（{s['vwide_hits']}）"),
    ('投資 → 払戻', lambda s: f"{_yen(s['vwide_staked'])} → {_yen(s['vwide_returned'])}"),
    ('回収率', _vwide_roi),
]


def render_virtual_wide(week_summary, wide_since_summary, total_summary):
    """仮想の買い目「◎×partners のワイド」。実際には買っていない。判定はしない。"""
    n = wide_since_summary['vwide_races'] if wide_since_summary else 0
    lines = [
        '## 仮想：◎×中穴候補（partners）のワイド（観察中・実際には買っていない）',
        '',
        f'◎と partners の全頭とのワイドを各100円買っていたら、の成績。'
        f'{VIRTUAL_WIDE_SINCE.isoformat()} 以降で {VIRTUAL_WIDE_REVIEW_AT} レースに達したら、'
        f'買い目の候補に加えるかを判断する（現在 {n} レース）。'
        '比べる相手は、上の「規律適用後」の回収率と、ワイドの控除後の水準（約77.5%）。',
        '',
        f'| 指標 | 今週 | {VIRTUAL_WIDE_SINCE.isoformat()}以降 | 通算 |',
        '|---|---|---|---|',
    ]
    for label, fn in VIRTUAL_WIDE_ROWS:
        since = fn(wide_since_summary) if wide_since_summary else '—'
        lines.append(f'| {label} | {fn(week_summary)} | {since} | {fn(total_summary)} |')
    if n >= VIRTUAL_WIDE_REVIEW_AT:
        lines.append('')
        lines.append(f'> **見直しどき**：{VIRTUAL_WIDE_REVIEW_AT} レースに達した。'
                     'bet_builder の候補に加えるかを検討する（検証ノート「メソッド改訂案」）。')
    lines.append('')
    return lines


def _hedge_roi(s, key, drop_max=False):
    n = s['hedge_differ']
    if not n:
        return '—'
    side = s[key]
    returned = side['returned'] - (side['max'] if drop_max else 0)
    return f"{returned / (100 * n) * 100:.0f}%"


HEDGE_ROWS = [
    ('比べられたレース（うち相手が違う）', lambda s: f"{s['hedge_races']}（{s['hedge_differ']}）"),
    ('相手が3着以内（印の順／勝率の順）',
     lambda s: f"{s['hedge_mark']['top3']}／{s['hedge_prob']['top3']}"),
    ('ワイド的中（印の順／勝率の順）',
     lambda s: f"{s['hedge_mark']['hits']}／{s['hedge_prob']['hits']}"),
    ('回収率（印の順／勝率の順）',
     lambda s: f"{_hedge_roi(s, 'hedge_mark')}／{_hedge_roi(s, 'hedge_prob')}"),
    ('最大払戻1回を除いた回収率',
     lambda s: f"{_hedge_roi(s, 'hedge_mark', True)}／{_hedge_roi(s, 'hedge_prob', True)}"),
]


def render_hedge_compare(week_summary, total_summary):
    """保険ワイドの相手の選び方の比較。実際の買い方は変えていない。判定はしない。"""
    lines = [
        '## 保険ワイドの相手：印の順 vs 勝率の順（観察中・2026-10-27〜）',
        '',
        '◎が飛んだときの保険（○軸のワイド1点）の相手を、今の「印の順（▲が先）」と'
        '「勝率の高い順」で選んだら、の比較。相手が違うレースだけを各100円で数える。'
        f'相手が違うレースが通算 {HEDGE_REVIEW_AT} に達したら、どちらにするかを決める'
        f"（現在 {total_summary['hedge_differ']}）。根拠は決定ログ 2026-10-27。",
        '',
        '| 指標 | 今週 | 通算 |',
        '|---|---|---|',
    ]
    for label, fn in HEDGE_ROWS:
        lines.append(f'| {label} | {fn(week_summary)} | {fn(total_summary)} |')
    lines.append('')
    return lines


def _rate(num, den):
    return f'{num / den * 100:.1f}%' if den else '—'


def _band_rates(c):
    """頭数・連対率・複勝率を1セルにまとめる。"""
    if not c['n']:
        return '—'
    return f"{c['n']}頭 連対{_rate(c['quin'], c['n'])} 複勝{_rate(c['place'], c['n'])}"


def render_partner_popularity(week_summary, total_summary):
    """中穴候補の人気帯別の成績。判定はしない、数字だけ。"""
    lines = [
        '## 中穴候補（partners）の人気帯別成績（2026-10-14〜）',
        '',
        '「同じレースの全馬」は、partners を出したレースで、その人気帯の馬を全部数えたもの'
        '（何も考えずにその人気帯から選んだ場合の物差し）。partners がこれを上回らなければ、'
        '穴を選ぶ力は人気以上には無い。人気は確定時点のもの。'
        '3連単「◎1着・5〜8番人気から2頭を2・3着」を検討するのは、5〜8番人気の連対率が'
        '全体をはっきり上回り続けてから（決定ログ 2026-10-14）。',
        '',
        f"partners を出したレース：今週 {week_summary['partner_races']} ／ "
        f"通算 {total_summary['partner_races']}",
        '',
        '| 人気帯 | 今週 partners | 今週 同じレースの全馬 | 通算 partners | 通算 同じレースの全馬 |',
        '|---|---|---|---|---|',
    ]
    for key, label, *_ in PARTNER_BANDS:
        cells = []
        for s in (week_summary, total_summary):
            b = s['partner_bands']
            cells += [_band_rates(b['partners'][key]), _band_rates(b['field'][key])]
        lines.append(f'| {label} | ' + ' | '.join(cells) + ' |')
    lines.append('')
    return lines


def weight_going_report(data_dir=None):
    """道悪×相対馬体重の集計（weight_going.py）を週次レビューに載せるための行。

    2026-10-12 決定ログ「道悪と馬体重」：重・不良60鞍たまるまで規則にしない。
    毎週同じ物差しで数字を出し、到達したらメールで知らせる。数字を出すだけで判定はしない。
    集計の失敗で週次レビュー全体を落とさないよう、例外はここで止めて (None, 0) を返す。
    """
    try:
        summary, paths = weight_going.analyze(data_dir or os.path.join(ROOT, 'data_jra'))
    except Exception:  # noqa: BLE001 - 付録の集計なので本体を止めない
        logger.exception('道悪×馬体重の集計に失敗しました')
        return None, 0
    if not paths:
        return None, 0
    return weight_going.render_lines(summary, paths), weight_going.heavy_races(summary)


def jockey_edge_report(data_dir=None):
    """騎手別「実勝−オッズからの見込み勝利数」（jockey_edge.py）を週次レビューに載せるための行。

    2026-11-03 決定ログ「騎手の成績変化は見込み勝利数との差で追う」：数字を出すだけで判定はしない。
    メールには載せない（毎週 |z|≥2 の騎手は誰かしら出るので、通知にすると「延々通知だけ来る」に戻る）。
    集計の失敗で週次レビュー全体を落とさないよう、例外はここで止めて None を返す。
    """
    try:
        summary, paths = jockey_edge.analyze(data_dir or os.path.join(ROOT, 'data_jra'))
    except Exception:  # noqa: BLE001 - 付録の集計なので本体を止めない
        logger.exception('騎手別の見込み勝利数の集計に失敗しました')
        return None
    if not paths:
        return None
    return jockey_edge.render_lines(summary, paths)


def nar_bias_report(collected, measure=None):
    """地方のレースがあった日・場のバイアス判定を、その場で測り直して並べる。

    外れ分析で「当日のバイアスは確認できず」と書くことになっていたため（2026-10-20、鎌倉記念）。
    保存済みのファイルには頼らず、公式サイトの月次ファイルから測る（過去の日も同じ手順で測れる）。
    **その日の確定レース全部（対象レースを含む）から測った値**で、発走前に分かっていた値ではない。
    判定はしない、並べるだけ。測れなくても週次レビュー全体は止めない。
    戻り値は (行のリスト, 測れた場の数)。
    """
    measure = measure or nar_csv.day_summaries
    rows = []
    for day in collected:
        venues = sorted({e['venue'] for e in day['entries']
                         if e.get('org') == 'nar' and e.get('venue')})
        if not venues:
            continue
        try:
            found = measure(day['date'].strftime('%Y%m%d'), venues)
        except Exception:  # noqa: BLE001 - 付録の集計なので本体を止めない
            logger.exception('%s の地方バイアスを測れませんでした', day['date'])
            found = {}
        for venue in venues:
            summary = found.get(venue)
            if not summary:
                rows.append(f"| {day['date'].isoformat()} | {venue} | — | 測れなかった（確定レースなし、または取得失敗） |")
                continue
            if summary['races'] < 3:
                text = f"判断保留（判定に使えるレースが{summary['races']}鞍）"
            else:
                text = '<br>'.join(summary['verdicts']) or '判定文なし'
            rows.append(f"| {day['date'].isoformat()} | {venue} | {summary['races']}鞍 | {text} |")
    if not rows:
        return [], 0
    lines = ['## 当日の地方バイアス（その日の確定レースから測ったもの）', '',
             '`nar_csv.py` の第11章の判定。**その日の全確定レース（対象レースを含む）から後で測った値**で、'
             '発走前に分かっていた値ではない。外れ分析で「当日のバイアス」を書くときの材料。'
             '1場3鞍未満は判断保留。', '',
             '| 日付 | 場 | 測定 | 判定 |', '|---|---|---|---|'] + rows + ['']
    return lines, len(rows)


def render_weight_going(wg_lines):
    if not wg_lines:
        return []
    return (['## 道悪×相対馬体重（観察中・規則化前）', '',
             '`weight_going.py` の出力。芝・通算。根拠は決定ログ 2026-10-12。', '', '```']
            + wg_lines + ['```', ''])


def edge_report(week_days=()):
    """主観 vs 確定オッズと、◎のオッズのずれ（edge_check.py）を週次レビューに載せるための行。

    2026-10-16 決定ログ「主観が確定オッズに勝てているかを毎週測る」。数字を出すだけで判定はしない。
    集計に失敗しても週次レビュー全体を落とさないよう、例外はここで止めて (None, None, None) を返す。
    3つ目の戻り値は集計そのもの（保留中の改善案の進み具合に使う）。
    """
    try:
        summary = edge_check.analyze(all_bet_days(), cached_result, week_days=week_days)
    except Exception:  # noqa: BLE001 - 付録の集計なので本体を止めない
        logger.exception('主観 vs 確定オッズの集計に失敗しました')
        return None, None, None
    if not summary['total']['races']:
        return None, None, None
    return edge_check.render_lines(summary), edge_check.mail_line(summary), summary


# 記事由来の保留中の改善案（決定ログ 2026-10-16）。データが目安に届いたらメールで知らせる。
# (キー, 見出し, 単位, 目安, 届いたらすること)
PENDING_PROPOSALS = (
    ('blend', '案2 主観と市場の混ぜ合わせ', 'R', edge_check.READY_RACES,
     '勝率の上限（CAP=1.5倍）を Benter 式の混ぜ合わせに置き換えるか、重みを当てはめて判断する'),
    ('drift', '案3 ◎のオッズのずれを期待値に織り込む', '頭', edge_check.READY_DRIFT,
     '検算の時刻別のずれを見て、割り引きを入れるか、買う判断を発走に近い検算に寄せるかを決める'),
    ('frame', '案5 枠順の減点をコース別にする', '鞍', 1500,
     '記事の枠順の判定（内有利・外有利がはっきりしたコース）と、手元の data_jra で向きが合うかを確かめてから実装する'),
    ('hedge', '保険ワイドの相手を勝率の順にする', 'R', HEDGE_REVIEW_AT,
     '相手が違ったレースで、印の順と勝率の順の3着内・回収率（最大払戻を除いた値も）を比べて、どちらにするかを決める'),
)


def count_jra_races(data_dir=None):
    """data_jra/ にたまったレース数。読めなければ None。"""
    total = 0
    try:
        for path in glob.glob(os.path.join(data_dir or os.path.join(ROOT, 'data_jra'), '*.json')):
            with open(path, encoding='utf-8') as f:
                total += len(json.load(f))
    except Exception:  # noqa: BLE001 - 付録の集計なので本体を止めない
        logger.exception('data_jra の数え上げに失敗しました')
        return None
    return total


def pending_status(edge_summary, jra_races, hedge_races=None):
    """保留中の改善案ごとに、いまの量と目安。量が取れないものは None。"""
    drift = ((edge_summary or {}).get('drift') or {}).get('最後の検算') or {}
    now = {
        'blend': edge_summary['total']['races'] if edge_summary else None,
        'drift': drift.get('n') if edge_summary else None,
        'frame': jra_races,
        'hedge': hedge_races,
    }
    return [{'key': key, 'label': label, 'unit': unit, 'target': target, 'todo': todo,
             'now': now[key], 'ready': now[key] is not None and now[key] >= target}
            for key, label, unit, target, todo in PENDING_PROPOSALS]


def render_pending(status):
    if not status:
        return []
    lines = ['## 保留中の改善案（データ待ち）', '',
             '根拠は決定ログ 2026-10-16（保険ワイドの相手は 2026-10-27）。目安に届いたらメール冒頭で知らせる。', '',
             '| 案 | いま | 目安 | 届いたらすること |', '|---|---|---|---|']
    for p in status:
        now = '—' if p['now'] is None else f"{p['now']}{p['unit']}"
        mark = ' **← 到達**' if p['ready'] else ''
        lines.append(f"| {p['label']} | {now}{mark} | {p['target']}{p['unit']} | {p['todo']} |")
    lines.append('')
    return lines


def gate_report(history, week_start):
    """券種の解禁条件（bet_gates.py）の .md の節とメールの1行。

    決定ログ 2026-11-03「券種の使い分け」。集計に失敗しても週次レビュー全体は止めない。
    """
    try:
        dated = [(day['date'], e['org'], e['horse_rows'])
                 for day in history for e in day['entries'] if e.get('horse_rows')]
        analysis = bet_gates.analyze(dated, week_start=week_start)
        return bet_gates.render_lines(analysis), bet_gates.mail_line(analysis)
    except Exception:  # noqa: BLE001 - 付録の集計なので本体を止めない
        logger.exception('券種の解禁条件の集計に失敗しました')
        return [], None


def bet_type_map_report(data_dir=None):
    """券種×人気帯の「何も考えずに買った場合の回収率」（bet_type_map.py）。.md にだけ載せる。

    決定ログ 2026-11-03「券種の使い分け」。集計に失敗しても週次レビュー全体は止めない。
    """
    try:
        return bet_type_map.report(data_dir)
    except Exception:  # noqa: BLE001 - 付録の集計なので本体を止めない
        logger.exception('券種×人気帯の集計に失敗しました')
        return []


def render_jockey_edge(jk_lines):
    if not jk_lines:
        return []
    return (['## 騎手別 実勝−見込み勝利数（観察のみ・規則にしない）', '',
             '`jockey_edge.py` の出力。中央・通算。回収率ではなくオッズに織り込まれていない分を見る。'
             '根拠は決定ログ 2026-11-03。', '', '```']
            + jk_lines + ['```', ''])


def render_edge(edge_lines):
    if not edge_lines:
        return []
    return (['## 主観は確定オッズに勝てているか（観察中）', '',
             '`edge_check.py` の出力。根拠は決定ログ 2026-10-16。', '']
            + edge_lines)


def render(collected, week_summary, total_summary, period, since_summary=None,
           week_by_org=None, total_by_org=None, wide_since_summary=None,
           wg_lines=None, edge_lines=None, pending=None, nar_bias_lines=None,
           since_counts=None, jk_lines=None, gate_lines=None, map_lines=None):
    lines = []
    start, end = period
    lines.append(f'# 週次レビュー {start.isoformat()} 〜 {end.isoformat()}')
    lines.append('')
    lines.append('機械が数えた部分のみ。反省と改訂は人が書いて 検証ノート.md へ移す。')
    lines.append('')

    # 数字が空なのか、取りに行けなかったのかを取り違えないようにする。
    # 「0件でした」と静かに出すのがいちばん危ない出力なので必ず前に出す。
    if week_summary['unsettled']:
        lines.append(f"> **注意：{week_summary['unsettled']}レースの結果を取得できていない。**"
                     ' 集計はその分だけ欠けている。')
        lines.append('> netkeiba の構造変更か通信の失敗を疑い、実行ログを確認すること。')
        lines.append('')

    # --- いちばん見たい数字を先頭に置く ---
    lines.append('## 規律は収支に効いたか')
    lines.append('')
    lines.append(_totals_line('仮想成績（買い目どおり全部買った場合）', week_summary['virtual']))
    lines.append(_totals_line('規律適用後（BLOCKを除いた実運用）', week_summary['disciplined']))
    diff = week_summary['disciplined']['profit'] - week_summary['virtual']['profit']
    lines.append(f'- **差引：{diff:+,}円**（プラスなら規律が損を防いだ、マイナスなら上振れを逃した）')
    if week_summary['blocked']['races']:
        lines.append(_totals_line('  うち止めた分', week_summary['blocked']))
    lines.append('')

    # --- 配当の偏り ---
    # 「回収率（規律適用後）」が少数の高配当に引っ張られていないかを、
    # すぐ上の数字に対する注記として置く。判定はしない、数字だけ。
    lines.append('### 配当の偏り（規律適用後の的中に対して）')
    lines.append('')
    lines.append('| 指標 | 今週 | 通算 |')
    lines.append('|---|---|---|')
    for label, fn in PAYOUT_STAT_ROWS:
        lines.append(f'| {label} | {fn(week_summary["payout_stats"])} '
                     f'| {fn(total_summary["payout_stats"])} |')
    lines.append('')

    # --- レース別 ---
    lines.append('## レース別')
    lines.append('')
    lines.append('| 日付 | レース | 勝負度 | 判定 | 収支 | 1〜3着（印） |')
    lines.append('|---|---|---|---|---|---|')
    for day in collected:
        for e in day['entries']:
            top3 = ' / '.join(
                f"{h['rank']}着 {h['umaban']}{h['mark'] or '無'}"
                for h in e.get('top3', [])
            ) or '—'
            verdict = e['category'] + ('（発注停止）' if e['blocked'] else '')
            profit = f"{e['returned'] - e['staked']:+,}円" if e['staked'] else '—'
            lines.append(
                f"| {day['date'].isoformat()} | {e['name']} | {e['confidence'] or '未評価'} "
                f"| {verdict} | {profit} | {top3} |")
    lines.append('')

    if nar_bias_lines:
        lines.extend(nar_bias_lines)

    # --- KPI ---
    lines.append('## KPI')
    lines.append('')
    lines.append('| 指標 | 今週 | 通算 |')
    lines.append('|---|---|---|')
    rows = [
        ('◎勝率', lambda s: _pct(s['honmei_win'], s['honmei_races'])),
        ('◎複勝率', lambda s: _pct(s['honmei_place'], s['honmei_races'])),
        ('回収率（規律適用後）', lambda s: _roi(s['disciplined'])),
        ('的中', lambda s: f"{s['disciplined']['hits']}/{s['disciplined']['races']}"),
        ('買い目構成ミス', lambda s: str(s['bet_miss'])),
        ('予想ミス', lambda s: str(s['predict_miss'])),
        ('見送り', lambda s: str(s['skipped'])),
    ]
    for label, fn in rows:
        lines.append(f'| {label} | {fn(week_summary)} | {fn(total_summary)} |')
    lines.append('')

    lines += render_mark_popularity(week_summary, total_summary, since_summary)
    if week_by_org and total_by_org:
        lines += render_by_org(week_by_org, total_by_org)
        lines += render_simple_bets(week_by_org, total_by_org)
    lines += render_virtual_wide(week_summary, wide_since_summary, total_summary)
    lines += render_partner_popularity(week_summary, total_summary)
    lines += gate_lines or []
    lines += map_lines or []
    lines += render_hedge_compare(week_summary, total_summary)
    lines += render_weight_going(wg_lines)
    lines += render_jockey_edge(jk_lines)
    lines += render_edge(edge_lines)
    lines += render_pending(pending)

    # --- しきい値カウンタ ---
    lines.append(f'## カウンタ（最後に判断した日以降で{COUNTER_THRESHOLD}件になったら見直しを検討）')
    lines.append('')
    for row in counter_status(total_summary, since_counts):
        key = row['key']
        flag = ' ← **しきい値到達**' if row['reached'] else ''
        since = (f"／{row['since'].isoformat()}以降{row['count']}件"
                 if row['since'] else '')
        lines.append(f"- {row['label']}：今週{week_summary[key]}件／通算{row['total']}件{since}{flag}")
        if key == 'honmei_wipeout':
            for name, s in (('今週', week_summary), ('通算', total_summary)):
                n = s.get('wipeout_eligible', 0)
                rate = f"{s[key] / n * 100:.0f}%" if n else '—'
                lines.append(f"  - {name}：買い目を出したレース（単勝1点を除く）{n}件のうち"
                             f"全滅{s[key]}件（{rate}）、保険ワイドが付いたレース{s.get('hedged', 0)}件")
        label = 'すること' if not row['alert'] else '到達時にすること'
        lines.append(f"  - {label}：{row['note']}")
    lines.append('')

    # --- 人が書く欄 ---
    lines.append('## ここから先は人が書く')
    lines.append('')
    lines.append('- 買い目構成ミスがあった場合、どの規律なら防げたか')
    lines.append('- 予想ミスの原因（血統／展開／馬場／調教のどれを読み違えたか）')
    lines.append('- 検証ノート.md への転記（基準値は10件たまるまで変えない）')
    lines.append('')
    return '\n'.join(lines)


def _mail_race_lines(collected):
    """メールに入れるレース別の明細。

    **数字だけの要約では何が起きたのか分からない**（2026-08-24、
    「買い目構成ミス4」とだけ書かれても、どのレースでどの印が走って
    どう取りこぼしたのかが読めない、という指摘）。レース名・印・
    1〜3着に来た馬とその印・収支を1行ずつ並べ、メールだけで
    「馬は見えていたのに買い方で落とした」かどうかが分かるようにする。
    """
    lines = []
    for day in collected:
        for e in day['entries']:
            if e['category'] == UNSETTLED:
                continue
            marks = ' '.join(f"{m['mark']}{m['umaban']}" for m in e['marks']) or '印なし'
            top3 = ' / '.join(
                f"{h['rank']}着{h['umaban']}{h['mark'] or '無'}"
                for h in e.get('top3', [])
            ) or '着順不明'
            profit = f"{e['returned'] - e['staked']:+,}円" if e['staked'] else '見送り'
            head = f"[{e['category']}] {e['name']}"
            if e['blocked']:
                head += '（発注停止）'
            lines.append(f'  {head}　{profit}')
            lines.append(f'    印 {marks}　→　{top3}')
    return lines


def render_mail(week_summary, period, path, total_summary=None, collected=None,
                week_by_org=None, wg_heavy=None, edge_line=None, pending=None,
                since_counts=None, total_by_org=None, gate_line=None):
    """メール本文。**しきい値に達したカウンタがあれば、ここで一言で言い切る。**

    通算の件数や「10件」という基準そのものは、レビューの .md ファイルには
    出ているがメールには出ていなかった。メールしか見ない運用だと、
    しきい値を超えても誰も気づかない。数字を読んで判断するのではなく、
    「見直しどき」かどうかだけを機械が言い切る形にする。

    **レース別の明細もメールに入れる**（2026-08-24 の指摘）。要約の数字だけでは
    「買い目構成ミス4」が何のことか分からず、詳細ファイルを開かないと
    判断できなかった。メール単体で意味が通るようにする。
    """
    start, end = period
    v, d = week_summary['virtual'], week_summary['disciplined']
    diff = d['profit'] - v['profit']
    lines = [
        f'週次レビュー {start.isoformat()} 〜 {end.isoformat()}',
        '',
    ]

    # **未確定があるうちは暫定値だと最初に言い切る。** 2026-08-24 は結果が
    # 12件未取得のまま「5R 収支-1,300円」というメールが出た。後で再取得した
    # 実際の数字は16R・-5,400円で、4倍以上ずれていた。数字の大小より先に
    # 「これは確定値ではない」と分かる必要がある。
    if week_summary['unsettled']:
        lines += [
            f"⚠ これは暫定値です（{week_summary['unsettled']}レースの結果が未取得）。",
            f"　 確定した集計は火曜 09:17 の再取得メール（件名に「再取得」）を見てください。",
            '',
        ]

    lines.append(f"対象 {week_summary['settled']}レース"
                 f"（うち購入 {v['races']}／見送り {week_summary['skipped']}"
                 f"／未確定 {week_summary['unsettled']}）")
    lines.append('')

    if total_summary:
        reached = [r for r in counter_status(total_summary, since_counts) if r['reached']]
        if reached:
            lines.append(f'■ メソッド見直しの検討どきです（最後に判断した日以降で{COUNTER_THRESHOLD}件が目安）')
            for r in reached:
                # 何件たまっていて、到達したら何をするのかまで書く。
                # 見出しだけでは「で、どうすれば」が分からない。
                since = f"{r['since'].isoformat()}以降" if r['since'] else '通算'
                lines.append(f"  - {r['label']}：{since}{r['count']}件")
                lines.append(f"    → {r['note'].replace('**', '')}")
            lines.append('  （判断したら THRESHOLD_COUNTERS の since をその日に進めてください）')
            lines.append('')

    if wg_heavy is not None and wg_heavy >= weight_going.READY_RACES:
        lines += [f'■ 道悪×馬体重の検討どきです（重・不良 {wg_heavy}鞍、目安{weight_going.READY_RACES}鞍）',
                  '  → 詳細ファイルの「道悪×相対馬体重」を見て、規則にするか判断してください'
                  '（決定ログ 2026-10-12）',
                  '']

    ready = [p for p in (pending or []) if p['ready']]
    if ready:
        lines.append('■ 保留中の改善案の検討どきです（データが目安に届きました）')
        for p in ready:
            lines.append(f"  - {p['label']}：{p['now']}{p['unit']}（目安{p['target']}{p['unit']}）")
            lines.append(f"    → {p['todo']}")
        lines.append('  （決定ログ 2026-10-16 を見て、実装するかを決めてください）')
        lines.append('')

    lines += [
        '── 収支 ' + '─' * 30,
        f"仮想成績   {v['races']}R 的中{v['hits']} 収支{v['profit']:+,}円 回収率{_roi(v)}"
        '　… 買い目どおり全部買った場合',
        f"規律適用後 {d['races']}R 的中{d['hits']} 収支{d['profit']:+,}円 回収率{_roi(d)}"
        '　… BLOCKを除いた実運用',
        f'差引 {diff:+,}円'
        '　… プラスなら規律が損を防いだ、マイナスなら上振れを逃した',
        '',
        _payout_stats_line('配当(規律適用後) 今週', week_summary['payout_stats']),
    ]
    if total_summary:
        lines.append(_payout_stats_line('配当(規律適用後) 通算', total_summary['payout_stats']))

    lines += [
        '',
        '── 印の精度 ' + '─' * 26,
        f"◎勝率 {_pct(week_summary['honmei_win'], week_summary['honmei_races'])}"
        f" / ◎複勝率 {_pct(week_summary['honmei_place'], week_summary['honmei_races'])}",
        f"買い目構成ミス {week_summary['bet_miss']}件"
        '　… 印は当たっていたのに買い方で取りこぼした',
        f"予想ミス {week_summary['predict_miss']}件"
        '　… 印そのものが外れた',
    ]
    if week_summary.get('top3_total'):
        lines.append(
            f"3着以内を印で拾えた {_pct(week_summary['top3_marked'], week_summary['top3_total'])}"
            f" / 同じ頭数の人気上位なら {_pct(week_summary['top3_popular'], week_summary['top3_total'])}")
        lines.append(
            f"1〜3番人気を完全に消した {week_summary['popular_cut']}頭"
            f"（うち3着以内 {week_summary['popular_cut_top3']}頭）"
            f" / 地方で1番人気から◎を外した {week_summary['nar_favorite_off']}R")
    for org, label in ORGS:
        s = (week_by_org or {}).get(org)
        if s and s['settled']:
            lines.append(
                f"{label} {s['settled']}R：◎勝率 {_pct(s['honmei_win'], s['honmei_races'])}"
                f"（1番人気 {_pct(s['favorite_win'], s['compared_races'])}）"
                f" / 印で拾えた {_pct(s['top3_marked'], s['top3_total'])}"
                f"（人気上位 {_pct(s['top3_popular'], s['top3_total'])}）")
    for org, label in ORGS:
        w = (week_by_org or {}).get(org)
        tot = (total_by_org or {}).get(org)
        if w and w.get('simple', {}).get('◎単勝'):
            line = (f"仮想：{label}の◎単勝を全部買っていたら 今週{_simple_cell(w, '◎単勝')}"
                    f"（実運用 {_roi(w['disciplined'])}）")
            if tot:
                line += (f" / 通算{_simple_cell(tot, '◎単勝')}"
                         f"（実運用 {_roi(tot['disciplined'])}）")
            lines.append(line)
    if week_summary.get('vwide_bets'):
        lines.append(
            f"仮想：◎×中穴候補のワイド {week_summary['vwide_bets']}点 的中{week_summary['vwide_hits']}"
            f" 回収率{_vwide_roi(week_summary)}（観察中・買っていない）")

    mid = [(label, s['partner_bands']) for label, s in
           (('今週', week_summary), ('通算', total_summary)) if s]
    if any(b['partners']['mid']['n'] for _, b in mid):
        lines.append('中穴候補の5〜8番人気 連対率：' + ' / '.join(
            f"{label} {_rate(b['partners']['mid']['quin'], b['partners']['mid']['n'])}"
            f"（{b['partners']['mid']['n']}頭。同じレースの全馬 "
            f"{_rate(b['field']['mid']['quin'], b['field']['mid']['n'])}）"
            for label, b in mid))

    if wg_heavy is not None:
        lines.append(f'道悪×馬体重（観察中）：重・不良 {wg_heavy}/{weight_going.READY_RACES}鞍')
    if edge_line:
        lines.append(edge_line)
    if gate_line:
        lines.append(gate_line)
    if pending:
        lines.append('保留中の改善案：' + ' ／ '.join(
            f"{p['label'].split(' ')[0]} {'—' if p['now'] is None else p['now']}/{p['target']}{p['unit']}"
            for p in pending))

    if collected:
        race_lines = _mail_race_lines(collected)
        if race_lines:
            lines += ['', '── レース別 ' + '─' * 26] + race_lines

    lines += ['', f'詳細: {path}']
    return '\n'.join(lines)


def write_review(text, day):
    os.makedirs(REVIEW_DIR, exist_ok=True)
    path = os.path.join(REVIEW_DIR, f'{day.isoformat()}.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


# ----------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description='週次レビューを作る')
    parser.add_argument('--days', type=int, default=7, help='さかのぼる日数（既定7）')
    parser.add_argument('--end', help='集計の終わり（YYYY-MM-DD、既定は今日）')
    parser.add_argument('--mail', action='store_true', help='結果をメールする')
    parser.add_argument('--no-cache', action='store_true',
                        help='保存済みの結果を使わず取り直す')
    parser.add_argument('--subject-suffix', default='',
                        help='件名に付け足す文字列（例：再取得回だと分かるように）')
    args = parser.parse_args(argv)

    end = date.fromisoformat(args.end) if args.end else bets.now_jst().date()
    start = end - timedelta(days=args.days - 1)
    week_days = [start + timedelta(days=i) for i in range(args.days)]

    use_cache = not args.no_cache
    week = collect(week_days, use_cache=use_cache)
    if not week:
        logger.info('%s〜%s に買い目ファイルがありません', start, end)

    # 通算はこれまでの全日を対象にする。結果は保存済みなので取り直しは起きない。
    history_days = [d for d in all_bet_days() if d not in set(week_days)]
    history = collect(history_days, use_cache=True) + week

    week_entries = [e for day in week for e in day['entries']]
    all_entries = [e for day in history for e in day['entries']]

    week_summary = summarize(week_entries)
    total_summary = summarize(all_entries)
    since_summary = summarize([e for day in history if day['date'] >= MARK_RULES_SINCE
                               for e in day['entries']])

    wide_since_summary = summarize([e for day in history if day['date'] >= VIRTUAL_WIDE_SINCE
                                    for e in day['entries']])
    week_by_org = summarize_by_org(week_entries)
    total_by_org = summarize_by_org(all_entries)

    wg_lines, wg_heavy = weight_going_report()
    jk_lines = jockey_edge_report()
    edge_lines, edge_line, edge_summary = edge_report(week_days)
    pending = pending_status(edge_summary, count_jra_races(), total_summary['hedge_differ'])
    nar_bias_lines, _ = nar_bias_report(week)
    since_counts = since_counts_for(history)
    gate_lines, gate_line = gate_report(history, start)
    map_lines = bet_type_map_report()
    text = render(week, week_summary, total_summary, (start, end),
                  since_summary=since_summary,
                  week_by_org=week_by_org, total_by_org=total_by_org,
                  wide_since_summary=wide_since_summary,
                  wg_lines=wg_lines, edge_lines=edge_lines, pending=pending,
                  nar_bias_lines=nar_bias_lines, since_counts=since_counts,
                  jk_lines=jk_lines, gate_lines=gate_lines, map_lines=map_lines)
    path = write_review(text, end)
    logger.info('レビューを書き出しました: %s', path)
    print(text)

    if args.mail:
        from mailer import Mailer
        mailer = Mailer()
        if not mailer.is_configured():
            logger.error('メールの認証情報が未設定です')
            return EXIT_ERROR
        subject = f'週次レビュー {start.isoformat()}〜{end.isoformat()}{args.subject_suffix}'
        rel = os.path.relpath(path, ROOT)
        body = render_mail(week_summary, (start, end), rel,
                           total_summary=total_summary, collected=week,
                           week_by_org=week_by_org, total_by_org=total_by_org,
                           wg_heavy=wg_heavy if wg_lines else None,
                           edge_line=edge_line, pending=pending,
                           since_counts=since_counts, gate_line=gate_line)
        if not mailer.send(subject, body):
            logger.error('メール送信に失敗しました')
            return EXIT_ERROR
        logger.info('メールを送信しました')

    # 対象レースはあるのに1件も確定結果が取れていない＝集計ではなく取得が壊れている。
    # 「今週は0件でした」という静かな出力で見過ごさないよう、色を変えて知らせる。
    if week_entries and week_summary['settled'] == 0:
        logger.warning('結果を1件も取得できませんでした。netkeiba への到達を確認してください。')
        return EXIT_NEEDS_ATTENTION

    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
