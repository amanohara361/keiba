#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""印ペアの受け皿と合成オッズ基準のバックテスト（2026-09-15）。

2026-09-14 の週次検証で、買い目構成ミス11件のうち6件が「◎と、実際に3着以内へ
来たもう一方の印（○▲△）の組み合わせを買っていなかった」という同一パターン
だった。仮説（docs/決定ログ.md「週次検証（2026-09-14分）の2課題への方針」課題B）は
**バグではなく構造**である——ワイドの相手集合は ○ → ○▲ → ○▲△ と広げるが、
合成オッズ = 1 ÷ Σ(1/各オッズ) は券を足すほど下がるので、人気馬同士のワイド
（1点3〜6倍程度）では2点目を足した時点で合成が3.0倍を割り、規律で落ちて
「◎-○の1点」だけが生き残る——というもの。

このスクリプトは**判断材料になる数字を出すだけ**で、基準もロジックも変えない。
出力は2部構成。

  第1部  6件の個別検証。購入セットの発走前オッズ（data/checks の記録）から
         合成オッズを出し、買い漏らした◎-印ワイドを足すと3.0倍を割るかを見る。
  第2部  2026-08-14 以降（bet_builder が買い目を組むようになった日）の
         結果確定済み全レースで、V0〜V3 の投資・回収・回収率・的中数を出す。

ネットワークには出ない（data/results/ の保存済み結果しか読まない）。
標準ライブラリと本リポジトリの既存モジュールのみを使う。
"""

import itertools
import os
import re
import sys
from datetime import date, timedelta

import bets
import discipline
import results as results_module
import review


# bet_builder が買い目を組むようになった日（それ以前は手組みで、
# 「印ペアを候補に入れたか」を同じ土俵で比べられない）。
BACKTEST_START = date(2026, 8, 14)

OUTPUT_PATH = os.path.join('data', 'review', 'pair_coverage_2026-09-15.md')

STAKE = 100

# 第1部の対象。2026-09-14 エントリ「印同士の組み合わせを買い目から漏らした
# （6件、11件中最多）」に挙がっているレース。日付は買い目ファイルの日付。
CASE_RACES = [
    (date(2026, 9, 10), '202609104511', '若武者賞'),
    (date(2026, 9, 13), '202606040409', '習志野特別'),
    (date(2026, 9, 13), '202609040410', '仲秋ステークス'),
    (date(2026, 9, 13), '202609040411', 'ローズステークス'),
    (date(2026, 9, 13), '202609134606', 'サラブレッド大賞典'),
    (date(2026, 9, 13), '202609135407', '建依別賞'),
]

# 第1部で「下限をいくつまで下げれば通ったか」を見るための刻み。
THRESHOLDS = [3.0, 2.5, 2.0, 1.5, 1.0]


# ----------------------------------------------------------------------
# 入力
# ----------------------------------------------------------------------

def marked_order(race):
    """印馬を印の並び順（◎○▲△）のまま、重複を除いて返す。"""
    out = []
    for m in race.marks:
        if m['umaban'] not in out:
            out.append(m['umaban'])
    return out


def axis_of(race):
    horses = race.horses_for('◎')
    return horses[0] if horses else None


def parse_bet_label(label):
    """検算記録の 'ワイド 3-11' を ('ワイド', [3, 11]) にする。"""
    parts = label.split()
    if len(parts) < 2:
        return None, []
    return parts[0], [int(n) for n in re.findall(r'\d+', parts[1])]


def priced_check(day, race_id):
    """その日の検算記録から、**発走前オッズが入っている最後の記録**を採る。

    review.load_final_checks() は文字どおり最後の記録を採るが、発走後の検算は
    オッズを取りに行かない（odds_meta に「発走済みのため取得せず」が入り
    bet_odds が空になる）ため、そのままでは購入時の値が取れない。ここでは
    bet_odds が空でない最後の記録＝**実際に発注判断をした時点の値**を使う。
    """
    path = os.path.join(bets.CHECKS_DIR, f'{day.isoformat()}.json')
    if not os.path.exists(path):
        return None
    import json
    with open(path, encoding='utf-8') as f:
        history = json.load(f)
    found = None
    for entry in history:
        for race in entry.get('races', []):
            if str(race.get('race_id')) != race_id:
                continue
            if race.get('bet_odds'):
                found = dict(race, checked_at=entry.get('checked_at'))
    return found


def settled_days():
    """バックテストの対象日（買い目ファイルがあり、基準日以降）。"""
    return [d for d in review.all_bet_days() if d >= BACKTEST_START]


def week_end(day):
    """月曜締めの週。review.py の週次レビューは月曜を終端に7日分を集計する
    （data/review/2026-08-24.md 〜 2026-09-14.md がいずれも月曜）。"""
    return day + timedelta(days=(0 - day.weekday()) % 7)


# ----------------------------------------------------------------------
# 精算
# ----------------------------------------------------------------------

def wide_payout(result, combo):
    return results_module.payout_for(result['payouts'], 'ワイド', combo)


def settle_wide_set(combos, result, stake=STAKE):
    """ワイドn点を精算する。払戻は100円あたりなので賭け金で按分する。"""
    staked = stake * len(combos)
    returned = 0
    for combo in combos:
        returned += wide_payout(result, combo) * stake // 100
    return {'staked': staked, 'returned': returned, 'hit': returned > 0}


def v1_combos(race):
    """V1 ◎軸ワイド全印流し。◎が無ければ買わない。"""
    axis = axis_of(race)
    if axis is None:
        return []
    return [sorted([axis, h]) for h in marked_order(race) if h != axis]


def v2_combos(race):
    """V2 全印ペアのワイド（印馬同士の全組み合わせ）。"""
    return [sorted(list(pair)) for pair in itertools.combinations(marked_order(race), 2)]


def bought_wide_combos(race):
    return [sorted(b.horses) for b in race.bets if b.type == 'ワイド']


def v3_extra_combos(race):
    """V3 実際の買い目に足す「買い漏らした◎-印ワイド」。"""
    bought = [tuple(c) for c in bought_wide_combos(race)]
    return [c for c in v1_combos(race) if tuple(c) not in bought]


def settle_v3(race, result, stake=STAKE):
    base = results_module.settle(race, result)
    extra = settle_wide_set(v3_extra_combos(race), result, stake=stake)
    return {
        'staked': base['staked'] + extra['staked'],
        'returned': base['returned'] + extra['returned'],
        'hit': base['hit'] or extra['hit'],
    }


# ----------------------------------------------------------------------
# 第1部：6件の個別検証
# ----------------------------------------------------------------------

def case_rows():
    rows = []
    for day, race_id, short in CASE_RACES:
        sheet = bets.load_sheet(day)
        race = next((r for r in sheet.races if r.race_id == race_id), None) if sheet else None
        result = review.cached_result(race_id)
        if race is None or result is None:
            rows.append({'short': short, 'error': '買い目または結果が見つかりません'})
            continue

        check = priced_check(day, race_id)
        bought = [(parse_bet_label(b['bet']), b['odds']) for b in (check or {}).get('bet_odds', [])]
        bought_odds = [o for (_t, _h), o in bought]
        composite = discipline.composite_odds(bought_odds)

        ranks = {h['umaban']: h['rank'] for h in result['finishing_order']}
        top3 = {u for u, r in ranks.items() if r <= 3}
        axis = axis_of(race)
        bought_wides = {tuple(c) for c in bought_wide_combos(race)}

        missed = []
        if axis in top3:
            for h in marked_order(race):
                if h == axis or h not in top3:
                    continue
                combo = tuple(sorted([axis, h]))
                if combo in bought_wides:
                    continue
                yen = wide_payout(result, list(combo))
                missed.append({
                    'combo': combo,
                    'mark': race.mark_of(h),
                    'rank_axis': ranks[axis],
                    'rank_other': ranks[h],
                    'yen': yen,
                    'proxy': yen / 100.0 if yen else None,
                })

        added = None
        if composite and missed and all(m['proxy'] for m in missed):
            added = discipline.composite_odds(bought_odds + [m['proxy'] for m in missed])

        rows.append(dict(_flow_upper_bound(race, axis, bought, missed),
                         **_allpairs_upper_bound(race, bought, missed), **{
            'short': short,
            'day': day,
            'race': race,
            'check': check,
            'bought': bought,
            'composite': composite,
            'missed': missed,
            'added': added,
            'ranks': ranks,
        }))
    return rows


def _allpairs_upper_bound(race, bought, missed):
    """V2（印ペア全網羅）の合成オッズの上限。考え方は _flow_upper_bound と同じ。

    全網羅はどの◎軸流しよりも点数が多い（＝上位集合）ので、合成はそれ以下になる。
    """
    marked = set(marked_order(race))
    known = {}
    for (bet_type, horses), odds in bought:
        if bet_type == 'ワイド' and len(horses) == 2 and set(horses) <= marked:
            known[tuple(sorted(horses))] = odds
    for m in missed:
        if m['proxy']:
            known[m['combo']] = m['proxy']

    pairs = [tuple(c) for c in v2_combos(race)]
    vals = [known[c] for c in pairs if c in known]
    unknown = len(pairs) - len(vals)
    required = None
    if vals and unknown:
        slack = 1.0 / discipline.MIN_COMPOSITE_ODDS - sum(1.0 / v for v in vals)
        required = (unknown / slack) if slack > 0 else float('inf')
    return {
        'pairs_points': len(pairs),
        'pairs_known': len(vals),
        'pairs_upper': discipline.composite_odds(vals),
        'pairs_required': required,
    }


def _flow_upper_bound(race, axis, bought, missed):
    """◎軸ワイド流しを「買い漏らしたペアに届くまで」広げたときの合成オッズの上限。

    bet_builder は相手集合を `pool[:n]`（印の序列順）で広げるので、買い漏らした
    印を拾うには、その印より上位の印を全部含む n 点を買うことになる。n点のうち
    発走前オッズが分かるのは「実際に買った◎絡みのワイド」だけで、残りは記録が
    無い。合成オッズは券を足すほど**単調に下がる**ので、**分かっている点だけで
    計算した合成は、n点セット全体の合成の上限**になる。上限が既に3.0倍を割って
    いれば、そのn点セットは（未知の点がどんなに高オッズでも）規律を通らない。
    """
    pool = race.partner_pool
    known = {}
    for (bet_type, horses), odds in bought:
        if bet_type == 'ワイド' and axis in horses:
            known[tuple(sorted(horses))] = odds
    for m in missed:
        if m['proxy']:
            known[m['combo']] = m['proxy']

    need = 0
    for m in missed:
        other = m['combo'][0] if m['combo'][1] == axis else m['combo'][1]
        if other in pool:
            need = max(need, pool.index(other) + 1)
    if not need:
        return {'flow_points': None, 'flow_upper': None, 'flow_known': 0,
                'flow_unknown': 0, 'flow_required': None}

    legs = [tuple(sorted([axis, q])) for q in pool[:need]]
    vals = [known[c] for c in legs if c in known]
    unknown = need - len(vals)

    # 未知の点が「全部同じオッズ x」だとしたとき、n点セットの合成が3.0倍を
    # 保つのに必要な x。1/3.0 - Σ(1/既知) が0以下なら、xがいくら高くても届かない。
    required = None
    if unknown:
        slack = 1.0 / discipline.MIN_COMPOSITE_ODDS - sum(1.0 / v for v in vals)
        required = (unknown / slack) if slack > 0 else float('inf')

    return {
        'flow_points': need,
        'flow_upper': discipline.composite_odds(vals),
        'flow_known': len(vals),
        'flow_unknown': unknown,
        'flow_required': required,
    }


def render_cases(rows):
    out = []
    out.append('## 第1部　6件の個別検証（購入セット＋買い漏らした◎-印ワイド）')
    out.append('')
    out.append('| レース | 購入セット（発走前オッズ） | 購入セットの合成 | 買い漏らした◎-印ワイド | 払戻(円) | 代理オッズ※ | 追加後の合成 | 3.0倍を割るか |')
    out.append('|---|---|---|---|---|---|---|---|')
    for row in rows:
        if row.get('error'):
            out.append(f"| {row['short']} | {row['error']} | - | - | - | - | - | - |")
            continue
        bought_txt = ' / '.join(
            f'{t} {"-".join(str(n) for n in h)} {o:.1f}倍' for (t, h), o in row['bought']) or '（記録なし）'
        missed_txt = ' / '.join(
            f"◎{m['combo'][0] if m['combo'][0] == axis_of(row['race']) else m['combo'][1]}"
            f"-{m['mark']}{m['combo'][1] if m['combo'][0] == axis_of(row['race']) else m['combo'][0]}"
            f"（{m['rank_axis']}着-{m['rank_other']}着）" for m in row['missed']) or '（なし）'
        yen_txt = ' / '.join(str(m['yen']) for m in row['missed']) or '-'
        proxy_txt = ' / '.join(f"{m['proxy']:.1f}倍" for m in row['missed'] if m['proxy']) or '-'
        comp_txt = f"{row['composite']:.2f}倍" if row['composite'] else '-'
        added_txt = f"{row['added']:.2f}倍" if row['added'] else '-'
        if row['added'] is None:
            verdict = '-'
        elif row['added'] < discipline.MIN_COMPOSITE_ODDS:
            verdict = f"**割る**（< {discipline.MIN_COMPOSITE_ODDS}）"
        else:
            verdict = '割らない'
        out.append(f"| {row['short']} | {bought_txt} | {comp_txt} | {missed_txt} "
                   f"| {yen_txt} | {proxy_txt} | {added_txt} | {verdict} |")
    out.append('')

    broke = [r for r in rows if r.get('added') and r['added'] < discipline.MIN_COMPOSITE_ODDS]
    ok = [r for r in rows if r.get('added') and r['added'] >= discipline.MIN_COMPOSITE_ODDS]
    out.append(f'**{len(broke)}件 / {len([r for r in rows if r.get("added")])}件**（追加後の合成が'
               f'{discipline.MIN_COMPOSITE_ODDS}倍未満）。'
               f'{len(ok)}件は追加しても基準を満たしていた。')
    out.append('')

    # 各券の最小値で見た場合（改訂案(b)の判断材料）。
    proxies = [m['proxy'] for r in rows if not r.get('error')
               for m in r['missed'] if m['proxy']]
    over = [p for p in proxies if p >= discipline.MIN_COMPOSITE_ODDS]
    race_all_over = [r for r in rows if not r.get('error') and r['missed']
                     and all(m['proxy'] and m['proxy'] >= discipline.MIN_COMPOSITE_ODDS
                             for m in r['missed'])]
    out.append(f'**参考（基準を「各券の最小値」にした場合）**：買い漏らした◎-印ワイドは'
               f'全{len(proxies)}点で、代理オッズが{discipline.MIN_COMPOSITE_ODDS}倍以上の点は'
               f'{len(over)}点（{"／".join(f"{p:.1f}" for p in sorted(proxies))}倍）。'
               f'買い漏らした点が全部{discipline.MIN_COMPOSITE_ODDS}倍以上だったレースは'
               f'{len(race_all_over)} / {len([r for r in rows if not r.get("error")])}件。')
    out.append('')
    out.append('※代理オッズ＝**確定払戻 ÷ 100**。買わなかった券の発走前オッズは保存されていない'
               'ため、CLAUDE.md「検算：払戻金 ÷ 100 が該当組の確定オッズと一致する」に従って'
               '確定オッズを代理値として使った。**購入セット側は発走前オッズ（data/checks の記録）**'
               'なので、1行の中で2種類のオッズが混在している。発走前→確定でオッズは動くので、'
               '追加後の合成オッズは概算であり、当時 bet_builder が見ていた値とは一致しない。')
    out.append('')
    out.append('※購入セットのオッズは、その日の検算記録のうち **bet_odds が空でない最後の記録**'
               'から採った（発走後の検算はオッズを取得しないため bet_odds が空になる）。')
    out.append('')

    out.append('### ◎軸ワイド流しで当該ペアまで届かせた場合（bet_builder が実際に採る形）')
    out.append('')
    out.append('bet_builder は相手集合を `pool[:n]`（印の序列順）で広げるので、'
               '買い漏らした印を拾うには**その印より上位の印を全部含むn点**を買うことになる。'
               'n点のうち発走前オッズが分かるのは実際に買った◎絡みのワイドだけなので、'
               '分かっている点だけで計算した合成を**上限**として示す'
               '（合成オッズは券を足すほど単調に下がるため、実際の値は必ずこれ以下）。')
    out.append('')
    out.append('| レース | 必要な点数 n | オッズが分かる点 | 合成の上限 | 未知の点に必要な最低オッズ※2 | 期待値1.2に必要な的中率※3 | 判定 |')
    out.append('|---|---|---|---|---|---|---|')
    flow_broke = 0
    flow_scored = 0
    for row in rows:
        if row.get('error') or not row.get('flow_points'):
            continue
        flow_scored += 1
        upper = row['flow_upper']
        req = row.get('flow_required')
        if req is None:
            req_txt = '（未知の点なし）'
        elif req == float('inf'):
            req_txt = '**到達不能**'
        else:
            req_txt = f'{req:.1f}倍以上'
        if upper is None:
            verdict, ev_txt = '判定不能', '-'
        else:
            ev_txt = f'{1.2 / upper * 100:.1f}%以上'
            if upper < discipline.MIN_COMPOSITE_ODDS:
                verdict = f'**合成で落ちる**（上限 {upper:.2f} < {discipline.MIN_COMPOSITE_ODDS}）'
                flow_broke += 1
            else:
                verdict = '合成では落ちない'
        upper_txt = f'{upper:.2f}倍' if upper else '-'
        out.append(f"| {row['short']} | {row['flow_points']}点 | {row['flow_known']}点 | "
                   f"{upper_txt} | {req_txt} | {ev_txt} | {verdict} |")
    out.append('')
    out.append(f'**{flow_broke}件 / {flow_scored}件**が、上限の時点で'
               f'{discipline.MIN_COMPOSITE_ODDS}倍を割っている＝'
               f'未知の点のオッズがどれだけ高くても合成オッズの規律を通らない。')
    out.append('')
    out.append('境界の2件は記録から決着をつけられない。若武者賞の未知の点（ワイド5-2）は'
               '同レースで記録のある ワイド5-10 が14.0倍・ワイド10-2 が19.2倍、必要値は15.2倍'
               'で、上回るか下回るかは**要確認**。サラブレッド大賞典の未知の点（◎-○の'
               'ワイド7-11）も必要値6.2倍に対して記録が無く**要確認**。')
    out.append('')
    out.append('※2　未知の点が全部同じオッズだと仮定したときに、n点セットの合成が'
               f'{discipline.MIN_COMPOSITE_ODDS}倍を保つのに必要なオッズ。'
               'これを下回れば合成で落ちる。')
    out.append('')
    out.append(f'※3　合成が上限どおりだったとして、期待値{discipline.MIN_EXPECTED_VALUE}を'
               '満たすのに必要な主観的中率（＝1.2 ÷ 合成）。**実際の見積りがこれに届いていたかは'
               '要確認**——当時の単勝オッズが保存されておらず（`data/checks` は bet_odds しか'
               '残さない）、Harville モデルに通す市場勝率を再現できないため、この n点セットの'
               '的中率を後から計算できない。合成で落ちなかったレースについては、'
               '**合成オッズではなく期待値1.2、あるいは「的中率最大の候補を採る」選択の側で'
               '落ちた可能性が残る**。')
    out.append('')

    out.append('### 参考：V2（印ペア全網羅）を候補にした場合の合成オッズの上限')
    out.append('')
    out.append('全網羅はどの◎軸流しよりも点数が多い（上位集合）ので、合成はそれ以下になる。'
               'ここでも、オッズが分かる点だけで計算した値を上限として示す。')
    out.append('')
    out.append('| レース | 全網羅の点数 | オッズが分かる点 | 合成の上限 | 残る未知の点に必要な最低オッズ | 判定 |')
    out.append('|---|---|---|---|---|---|')
    pairs_broke = 0
    pairs_scored = 0
    for row in rows:
        if row.get('error') or not row.get('pairs_upper'):
            continue
        pairs_scored += 1
        upper = row['pairs_upper']
        req = row.get('pairs_required')
        if req is None:
            req_txt = '（未知の点なし）'
        elif req == float('inf'):
            req_txt = '**到達不能**'
        else:
            req_txt = f'各{req:.0f}倍以上'
        if upper < discipline.MIN_COMPOSITE_ODDS:
            verdict = '**割る**'
            pairs_broke += 1
        else:
            verdict = '上限では割らない（未知の点次第）'
        out.append(f"| {row['short']} | {row['pairs_points']}点 | {row['pairs_known']}点 | "
                   f"{upper:.2f}倍 | {req_txt} | {verdict} |")
    out.append('')
    reqs = [r['pairs_required'] for r in rows
            if r.get('pairs_required') not in (None, float('inf'))]
    tail = ''
    if reqs:
        tail = (f'割っていない{pairs_scored - pairs_broke}件も、残る未知の点に'
                f'各{min(reqs):.0f}〜{max(reqs):.0f}倍のワイドオッズを要求する'
                f'（印馬同士のワイドでは現実的な値ではない）。')
    out.append(f'**{pairs_broke}件 / {pairs_scored}件**が上限の時点で割っている。{tail}'
               f'つまり印ペア全網羅は、セット基準を{discipline.MIN_COMPOSITE_ODDS}倍のまま'
               'にした場合ほぼ確実に規律で落ちる。')
    out.append('')

    out.append('### 下限をいくつまで下げれば通ったか（追加後の合成オッズ基準）')
    out.append('')
    out.append('| 下限 | 通る件数 |')
    out.append('|---|---|')
    scored = [r for r in rows if r.get('added')]
    for th in THRESHOLDS:
        passed = sum(1 for r in scored if r['added'] >= th)
        out.append(f'| {th:.1f}倍 | {passed} / {len(scored)}件 |')
    out.append('')
    out.append('（代理オッズ混在の概算。**期待値1.2の条件は当てていない**——買い漏らした券の'
               '発走前オッズが無く、当時の主観勝率から期待値を再計算できないため。）')
    out.append('')
    return out


# ----------------------------------------------------------------------
# 第2部：バックテスト
# ----------------------------------------------------------------------

VARIANTS = ['V0', 'V1', 'V2', 'V3']

VARIANT_LABEL = {
    'V0': 'V0 実際の買い目（基準線）',
    'V1': 'V1 ◎軸ワイド全印流し',
    'V2': 'V2 全印ペアのワイド',
    'V3': 'V3 実際の買い目＋買い漏らした◎-印ワイド',
}


def backtest_rows():
    rows = []
    for day in settled_days():
        sheet = bets.load_sheet(day)
        if not sheet:
            continue
        for race in sheet.races:
            result = review.cached_result(race.race_id)
            if not result:
                continue
            v0 = results_module.settle(race, result)
            row = {
                'day': day,
                'week': week_end(day),
                'name': race.name,
                'confidence': race.confidence,
                'no_bets': not race.bets,
                'V0': {'staked': v0['staked'], 'returned': v0['returned'], 'hit': v0['hit']},
                'V1': settle_wide_set(v1_combos(race), result),
                'V2': settle_wide_set(v2_combos(race), result),
                'V3': settle_v3(race, result),
            }
            rows.append(row)
    return rows


def totals(rows, key):
    staked = sum(r[key]['staked'] for r in rows)
    returned = sum(r[key]['returned'] for r in rows)
    hits = sum(1 for r in rows if r[key]['hit'])
    buying = sum(1 for r in rows if r[key]['staked'] > 0)
    return {'staked': staked, 'returned': returned, 'hits': hits,
            'races': buying, 'roi': (returned / staked * 100) if staked else None}


def _roi(t):
    return f"{t['roi']:.1f}%" if t['roi'] is not None else '-'


def render_backtest(rows):
    out = []
    out.append('## 第2部　バックテスト（2026-08-14以降・結果確定済み全レース）')
    out.append('')
    days = sorted({r['day'] for r in rows})
    out.append(f'対象 {len(rows)}レース（{days[0]}〜{days[-1]}、買い目ファイルのある{len(days)}日）。'
               f'うち買い目なし（見送り・未評価）が{sum(1 for r in rows if r["no_bets"])}レース。')
    out.append('')
    out.append('| 案 | 購入レース数 | 投資 | 回収 | 回収率 | 的中レース数 |')
    out.append('|---|---|---|---|---|---|')
    for key in VARIANTS:
        t = totals(rows, key)
        out.append(f"| {VARIANT_LABEL[key]} | {t['races']} | {t['staked']:,}円 | "
                   f"{t['returned']:,}円 | {_roi(t)} | {t['hits']} |")
    out.append('')

    out.append('### 週別（月曜締め）')
    out.append('')
    out.append('| 週（〜月曜） | レース数 | ' + ' | '.join(
        f'{k} 投資/回収/回収率/的中' for k in VARIANTS) + ' |')
    out.append('|---|---|' + '---|' * len(VARIANTS))
    for week in sorted({r['week'] for r in rows}):
        wrows = [r for r in rows if r['week'] == week]
        cells = []
        for key in VARIANTS:
            t = totals(wrows, key)
            cells.append(f"{t['staked']:,}/{t['returned']:,}/{_roi(t)}/{t['hits']}")
        out.append(f'| {week.isoformat()} | {len(wrows)} | ' + ' | '.join(cells) + ' |')
    out.append('')

    skipped = [r for r in rows if r['no_bets']]
    sk1 = totals(skipped, 'V1')
    out.append('**脚注（この表の限界）**')
    out.append('')
    out.append(f'1. **V1〜V3は事後の数字であり、規律（合成オッズ{discipline.MIN_COMPOSITE_ODDS}倍・'
               f'期待値{discipline.MIN_EXPECTED_VALUE}）を当てていない。** '
               '買わなかった券の発走前オッズは保存されていないため、当時これらのセットが'
               '規律を通ったかどうかは計算できない。つまりV1〜V3の回収率は'
               '**「規律を全部外したらどうなったか」**であって、採用したときの見込みではない。')
    out.append(f'2. 払戻は data/results/ の `payouts`（＝**当たった組しか分からない**）から取る。'
               '外れた組は0円として扱う。的中した組の払戻は確定値なので、回収額そのものは正確。')
    out.append(f'3. **見送り（買い目なし）のレースの扱いが案によって違う。** '
               f'V0では投資0円だが、V1/V2は印さえあれば買う想定なので投資が発生する。'
               f'該当は{len(skipped)}レースで、V1ではそこだけで投資{sk1["staked"]:,}円・'
               f'回収{sk1["returned"]:,}円（回収率{_roi(sk1)}・的中{sk1["hits"]}レース）。'
               'V0とV1/V2の比較はこの差を含んでいる。')
    out.append('4. V1/V2は印が無いレース（◎なし等）では買わない。1点100円固定。')
    out.append('')
    return out


# ----------------------------------------------------------------------

def build_report():
    out = ['# 印ペアの受け皿と合成オッズ基準（2026-09-15）', '']
    out.append('`docs/決定ログ.md`「週次検証（2026-09-14分）の2課題への方針」課題Bの'
               '判断材料。**基準もロジックも変更していない**（数字を出しただけ）。')
    out.append('')
    out.append('仮説：ワイドの相手集合を ○ → ○▲ → ○▲△ と広げると、合成オッズ'
               '（1 ÷ Σ(1/各オッズ)）は券を足すほど下がる。人気馬同士のワイドは1点3〜6倍'
               '程度なので、2点目を足した時点で合成が3.0倍を割って規律に落ち、'
               '「◎-○の1点」だけが生き残る——つまり第13章の「合成オッズはセット全体で'
               '3.0倍以上」という基準が、印ペアの受け皿を構造的に禁じている。')
    out.append('')
    out.extend(render_cases(case_rows()))
    out.extend(render_backtest(backtest_rows()))
    return '\n'.join(out) + '\n'


def main(argv=None):
    text = build_report()
    print(text)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        f.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
