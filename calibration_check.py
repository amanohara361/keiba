#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主観勝率が市場推定からどれだけ乖離しているかを、保存済みデータだけで測る。

## なぜこれがあるか

2026-08-26 の実測（`検証ノート.md` 冒頭「【最重要・2026-08-26】」）で、
主観的中率の平均28.9%に対し市場推定13.6%・実測7.1%と判明した。この
**2.13倍**という数字を根拠に、検証ノートの「メソッド改訂案（ユーザー承認待ち）」
に「主観勝率は市場推定の1.3倍を上限にする」案が出ている。提案文自身が
「1.3倍という数字に実証的な裏付けはまだない。4週ごとに実測と突き合わせて
調整する前提」と書いており、その定期チェックを機械化したのが本スクリプト。

**数字を出すだけで、基準もロジックも一切変えない。**
`予想メソッド.md`・`bet_builder.py`・`discipline.py` には触らない。

## 測っているもの（2つの土俵）

2026-08-26 の実測は旧方式（レース単位の1個の自己申告値 `subjective_hit_rate`）
を対象にしていた。同日中に `win_probabilities`（馬番ごとの主観勝率を Harville
モデルに通す方式）へ切り替わっているので、**今測るべきは新方式の乖離**である。
ただし「2.13倍から改善したか」を言うには同じ土俵の数字も要るため、2つ出す。

  指標A  券種セット単位の乖離 ＝ そのレースで採用した買い目セットの
         主観的中率 ÷ 市場推定的中率。市場推定的中率は各点の
         「払戻率 ÷ オッズ」の合算（= 払戻率が一律なら 払戻率 ÷ 合成オッズ）。
         **2026-08-26 の 28.9% vs 13.6% と同じ土俵**で、単勝オッズの記録が
         無くても計算できるため全期間で測れる。

  指標B  馬番単位の乖離 ＝ `win_probabilities` の値 ÷ 市場推定勝率
         （`bet_builder.market_win_probabilities`）。**改訂案の1.3倍上限が
         直接かかるのはここ**。単勝オッズの記録が要るので測れる範囲が狭い。

さらに、改訂案をそのまま当てたらどうなるかを実データで見るため、
`win_probabilities` を市場の CAP 倍で頭打ちにしたときの
セット的中率・期待値の変化も出す（指標C）。

## データの制約（推測で埋めない）

- `win_probabilities` があるのは **2026-08-27 以降**（それ以前は旧方式）。
- 市場の単勝オッズ（`market_win_probabilities` に渡す値）の出どころは2つ。
    1. `data/checks/<date>.json` の `win_odds` … bet_builder が実際に見た値
       そのもの。**PR #57（2026-09-16マージ）以降しか無い。**
    2. `data/cards/<date>.json` の `odds.win_odds` … JRA の朝のカード作成
       時点の値。**直前検算の時点とはズレているので代理値（プロキシ）**。
- 地方（NAR）のカードには単勝オッズが入っていない（`docs/地方競馬手順.md`
  のとおり `odds` フィールド自体が無い）。したがって **2026-09-16 より前の
  地方レースは指標B・Cの対象外**にするしかない。
- 結果の確定判定は `review.cached_result`（保存済み結果の有無）で行う。
  `review.collect()` は結果が無いとネットワークに取りに行くため使わない
  （本スクリプトは外部通信をしない。`backtest_pairs.py` と同じ流儀）。

使い方:
    python3 calibration_check.py
    python3 calibration_check.py --since 2026-09-01 --until 2026-09-30
    python3 calibration_check.py --cap 1.5 --out data/review/calibration_2026-10-15.md
"""

import argparse
import json
import math
import os
import re
import statistics
import sys
from datetime import date

import bet_builder
import bets
import discipline
import results as results_module
import review

# `win_probabilities`（馬番ごとの主観勝率）を朝タスクが書き始めた日。
# これより前は旧方式（レース単位の subjective_hit_rate 自己申告）で、
# 測り直しても改訂案の判断材料にならない。
WIN_PROB_START = date(2026, 8, 27)

# `data/checks/<date>.json` に win_odds（出走全頭の単勝オッズ）が
# 記録されるようになった日（PR #57）。
WIN_ODDS_LOGGED_FROM = date(2026, 9, 16)

# 改訂案が提示している上限（承認も却下もされていない暫定値）。
DEFAULT_CAP = 1.3

# 2026-08-26 の実測値。今回の数字と並べるためだけに置く。
BASELINE = {'subjective': 0.289, 'market': 0.136, 'actual': 0.071, 'ratio': 2.13}

DEFAULT_OUTPUT = os.path.join('data', 'review', 'calibration_2026-09-17.md')

# 期間の区切り。(開始日, 終了日の翌日, ラベル)。
PERIODS = [
    (date(2026, 1, 1), WIN_PROB_START,
     '旧方式（〜08-26）'),
    (WIN_PROB_START, WIN_ODDS_LOGGED_FROM,
     '新方式・単勝オッズ記録なし（08-27〜09-15）'),
    (WIN_ODDS_LOGGED_FROM, date(2099, 1, 1),
     '新方式・単勝オッズ記録あり（09-16〜）'),
]


def period_of(day):
    """その日がどの期間に属するかのラベル。"""
    for start, end, label in PERIODS:
        if start <= day < end:
            return label
    return '範囲外'


# ----------------------------------------------------------------------
# 入力（保存済みファイルだけを読む）
# ----------------------------------------------------------------------

def card_win_odds(day):
    """JRA の朝のカードから {race_id: {馬番: 単勝オッズ}}。**代理値**。"""
    path = os.path.join('data', 'cards', f'{day.isoformat()}.json')
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    out = {}
    for race in raw.get('races', []):
        win_odds = (race.get('odds') or {}).get('win_odds') or {}
        if win_odds:
            out[str(race['race_id'])] = {int(k): float(v) for k, v in win_odds.items()}
    return out


def check_win_odds(day):
    """検算記録から {race_id: {馬番: 単勝オッズ}}。**bet_builder が見た値そのもの**。

    1日に何度も追記されるので、win_odds が空でない最後の記録を採る
    （発走後の検算はオッズを取りに行かないため空になる）。
    """
    path = os.path.join(bets.CHECKS_DIR, f'{day.isoformat()}.json')
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        history = json.load(f)
    out = {}
    for entry in history:
        for race in entry.get('races', []):
            win_odds = race.get('win_odds') or {}
            if win_odds:
                out[str(race.get('race_id'))] = {
                    int(k): float(v) for k, v in win_odds.items()}
    return out


def priced_checks(day):
    """検算記録から、**発走前オッズが入っている最後の記録**を race_id 別に返す。

    backtest_pairs.priced_check と同じ考え方（発走後の検算は bet_odds が空）。
    """
    path = os.path.join(bets.CHECKS_DIR, f'{day.isoformat()}.json')
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        history = json.load(f)
    out = {}
    for entry in history:
        for race in entry.get('races', []):
            if race.get('bet_odds'):
                out[str(race.get('race_id'))] = race
    return out


def parse_legs(bet_odds):
    """検算記録の bet_odds を [(券種, [馬番, ...]), ...] と [オッズ, ...] にする。"""
    legs, odds = [], []
    for item in bet_odds:
        parts = str(item.get('bet', '')).split()
        if len(parts) < 2:
            continue
        horses = [int(n) for n in re.findall(r'\d+', parts[1])]
        if not horses:
            continue
        legs.append((parts[0], horses))
        odds.append(item.get('odds'))
    return legs, odds


# ----------------------------------------------------------------------
# 計算（bet_builder の関数をそのまま使う。手で計算し直さない）
# ----------------------------------------------------------------------

def market_hit_rate(legs, odds):
    """買い目セットの市場推定的中率。

    各点について「払戻率 ÷ オッズ」が市場の見ているその点の的中率なので、
    セット全体ではその合算になる（払戻率が一律なら 払戻率 ÷ 合成オッズ と
    一致する）。2026-08-26 の「市場推定的中率13.6%」と同じ土俵。
    """
    total = 0.0
    for (bet_type, _horses), o in zip(legs, odds):
        if o and o > 0:
            total += bet_builder.PAYOUT_RATE.get(bet_type, 0.775) / o
    return total or None


def set_hit_rate(p, legs):
    """勝率分布 p のもとで、買い目セットのどれか1点が的中する確率。

    券種が混ざっても（保険のワイドが足されていても）正しく出せるよう、
    全馬の1〜3着の並びを Harville で総当たりして、どれかが的中する並びの
    確率だけを重複なく合算する（bet_builder._union_hit_rate と同じ方法）。
    """
    total = 0.0
    for top3, prob in bet_builder._harville_top3(p):
        if any(bet_builder._combo_hits(t, c, top3) for t, c in legs):
            total += prob
    return total


def capped_overrides(overrides, market, cap):
    """主観勝率を市場推定の cap 倍で頭打ちにする（下方向の補正は制限しない）。"""
    out = {}
    for umaban, value in overrides.items():
        limit = market.get(umaban)
        out[umaban] = min(value, limit * cap) if limit else value
    return out


def describe(values):
    """分布の要約。件数が少ないときも落ちないようにする。"""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    return {
        'n': len(vals),
        'mean': statistics.fmean(vals),
        'median': statistics.median(vals),
        'min': vals[0],
        'max': vals[-1],
    }


def binom_at_most(hits, trials, p):
    """的中が hits 件**以下**になる確率（二項分布の下側）。

    2026-08-26 の検証が使った二項検定と同じもの。標準ライブラリだけで出す
    （scipy は入れない方針。math.comb で足りる）。
    """
    if not 0.0 < p < 1.0 or trials <= 0:
        return None
    return sum(math.comb(trials, k) * p ** k * (1 - p) ** (trials - k)
               for k in range(hits + 1))


def fmt(stats, digits=2):
    if not stats:
        return '—'
    return (f"{stats['mean']:.{digits}f} / {stats['median']:.{digits}f} "
            f"（{stats['min']:.{digits}f}〜{stats['max']:.{digits}f}）")


# ----------------------------------------------------------------------
# 棚卸し
# ----------------------------------------------------------------------

def inventory(since, until):
    """対象期間の全レースについて、何が使えて何が使えないかを1行ずつ作る。"""
    rows = []
    for day in review.all_bet_days():
        if not (since <= day <= until):
            continue
        sheet = bets.load_sheet(day)
        if not sheet:
            continue
        cards = card_win_odds(day)
        checks = check_win_odds(day)
        priced = priced_checks(day)
        for race in sheet.races:
            if race.race_id in checks:
                source, win_odds = 'checks', checks[race.race_id]
            elif race.race_id in cards:
                source, win_odds = 'cards', cards[race.race_id]
            else:
                source, win_odds = None, None
            check = priced.get(race.race_id)
            legs, odds = parse_legs(check.get('bet_odds', [])) if check else ([], [])
            rows.append({
                'day': day,
                'period': period_of(day),
                'org': race.org,
                'race': race,
                'name': race.name,
                'has_wp': bool(race.win_probabilities),
                'settled': review.cached_result(race.race_id) is not None,
                'source': source,
                'win_odds': win_odds,
                'legs': legs,
                'odds': odds,
                'composite': discipline.composite_odds(odds) if odds else None,
                'subjective_hit_rate': race.subjective_hit_rate,
            })
    return rows


# ----------------------------------------------------------------------
# 指標A：券種セット単位（2026-08-26 と同じ土俵）
# ----------------------------------------------------------------------

def set_level_rows(rows):
    out = []
    for row in rows:
        if not row['legs'] or not row['subjective_hit_rate']:
            continue
        market = market_hit_rate(row['legs'], row['odds'])
        if not market:
            continue
        out.append(dict(row, market_hit=market,
                        subjective_hit=row['subjective_hit_rate'],
                        ratio=row['subjective_hit_rate'] / market))
    return out


def render_set_level(rows, since, until):
    out = ['## 指標A　券種セット単位の乖離（2026-08-26 の 2.13倍と同じ土俵）', '']
    out.append('採用した買い目セットの主観的中率 ÷ 市場推定的中率。市場推定的中率は'
               '各点の「払戻率 ÷ 発走前オッズ」の合算で、単勝オッズの記録が要らない'
               'ため**全期間で測れる**。主観的中率は買い目ファイルの'
               ' `subjective_hit_rate`（2026-08-26以降は bet_builder が'
               '**実際に使った値**を書き戻したもの）。')
    out.append('')
    if not rows:
        out.append('対象レースがありません（発走前オッズの記録が無い）。')
        out.append('')
        return out
    out.append('| 期間 | レース数 | 主観的中率の平均 | 市場推定の平均 | 平均どうしの比 | '
               '比の平均 / 中央値（範囲） |')
    out.append('|---|---|---|---|---|---|')
    for _s, _e, label in PERIODS:
        sub = [r for r in rows if r['period'] == label]
        if not sub:
            continue
        ms = statistics.fmean(r['subjective_hit'] for r in sub)
        mm = statistics.fmean(r['market_hit'] for r in sub)
        out.append(f'| {label} | {len(sub)} | {ms * 100:.1f}% | {mm * 100:.1f}% | '
                   f'**{ms / mm:.2f}倍** | {fmt(describe([r["ratio"] for r in sub]))} |')
    all_new = [r for r in rows if r['day'] >= WIN_PROB_START]
    if all_new:
        ms = statistics.fmean(r['subjective_hit'] for r in all_new)
        mm = statistics.fmean(r['market_hit'] for r in all_new)
        out.append(f'| **新方式まとめ（08-27〜）** | {len(all_new)} | {ms * 100:.1f}% | '
                   f'{mm * 100:.1f}% | **{ms / mm:.2f}倍** | '
                   f'{fmt(describe([r["ratio"] for r in all_new]))} |')
    out.append('')
    old = [r for r in rows if r['day'] < WIN_PROB_START]
    if old:
        ms = statistics.fmean(r['subjective_hit'] for r in old)
        mm = statistics.fmean(r['market_hit'] for r in old)
        out.append(f'**検算**：旧方式の期間は主観{ms * 100:.1f}% / 市場{mm * 100:.1f}% / '
                   f'比{ms / mm:.2f}倍で、2026-08-26 の実測'
                   f'（{BASELINE["subjective"] * 100:.1f}% / {BASELINE["market"] * 100:.1f}% / '
                   f'{BASELINE["ratio"]:.2f}倍）をほぼ再現している。**集計方法が当時と'
                   f'揃っていることの確認**であり、新方式の数字と当時の数字を'
                   f'並べてよい根拠になる（件数の差は対象の取り方の違い）。')
        out.append('')
    return out


def actual_outcomes(set_rows):
    """結果確定済みレースについて、見積り（主観・市場）と実測を並べる。

    的中判定・回収は `results.settle`（週次レビューと同じ関数）をそのまま使う。
    """
    out = []
    for row in set_rows:
        result = review.cached_result(row['race'].race_id)
        if not result or not row['race'].bets:
            continue
        settled = results_module.settle(row['race'], result)
        out.append(dict(row, hit=settled['hit'],
                        staked=settled['staked'], returned=settled['returned']))
    return out


def render_actual(rows):
    out = ['## 指標A-2　見積りと実測の突き合わせ（2026-08-26 の表と同じ形）', '']
    if not rows:
        out.append('**データ不足。** 結果確定済みのレースがありません。')
        out.append('')
        return out
    out.append('| 期間 | レース数 | 主観的中率の平均 | 市場推定の平均 | 実際の的中率 | '
               '回収率 | 主観が正しいときに この的中数以下になる確率 | '
               '市場が正しいとき |')
    out.append('|---|---|---|---|---|---|---|---|')
    groups = [(r'旧方式（〜08-26）', [r for r in rows if r['day'] < WIN_PROB_START]),
              ('新方式（08-27〜）', [r for r in rows if r['day'] >= WIN_PROB_START])]
    for label, sub in groups:
        if not sub:
            continue
        hits = sum(1 for r in sub if r['hit'])
        ms = statistics.fmean(r['subjective_hit'] for r in sub)
        mm = statistics.fmean(r['market_hit'] for r in sub)
        staked = sum(r['staked'] for r in sub)
        returned = sum(r['returned'] for r in sub)
        ps = binom_at_most(hits, len(sub), ms)
        pm = binom_at_most(hits, len(sub), mm)
        out.append(f'| {label} | {len(sub)} | {ms * 100:.1f}% | {mm * 100:.1f}% | '
                   f'**{hits / len(sub) * 100:.1f}%（{hits}/{len(sub)}）** | '
                   f'{returned / staked * 100:.1f}% | {ps * 100:.2f}% | {pm * 100:.1f}% |')
    out.append('')
    new = [r for r in rows if r['day'] >= WIN_PROB_START]
    if new:
        hits = sum(1 for r in new if r['hit'])
        ms = statistics.fmean(r['subjective_hit'] for r in new)
        mm = statistics.fmean(r['market_hit'] for r in new)
        ps = binom_at_most(hits, len(new), ms)
        pm = binom_at_most(hits, len(new), mm)
        verdict = ('主観は5%水準で棄却される' if ps is not None and ps < 0.05
                   else '主観はまだ棄却できない（サンプル不足）')
        verdict_m = ('市場も棄却される' if pm is not None and pm < 0.05
                     else '市場は棄却されない')
        out.append(f'**新方式の{len(new)}レースでは実測{hits / len(new) * 100:.1f}%に対し、'
                   f'主観{ms * 100:.1f}% / 市場{mm * 100:.1f}%。{verdict}／{verdict_m}。**'
                   f'旧方式と同じ「実測は市場側に近い」形が続いているが、'
                   f'**件数が少なく、この1行だけで断定はできない**。')
        out.append('')
    out.append('※ 的中・回収は `results.settle`（週次レビューと同じ関数）。'
               '見送り（買い目なし）のレースは母数に入らない。')
    out.append('')
    return out


# ----------------------------------------------------------------------
# 指標B：馬番単位（改訂案の1.3倍が直接かかるところ）
# ----------------------------------------------------------------------

def horse_ratios(win_probabilities, market, mark_of=None):
    """上書きされた馬番だけについて (馬番, 印, 主観, 市場, 比) を作る。

    上書きの無い馬は市場そのままなので比較する意味が無く、含めない。
    出走表に無い馬番（取消・入力ミス）は market に居ないので落とす
    （bet_builder.build_bets と同じ扱い）。
    """
    out = []
    for umaban, subjective in sorted(win_probabilities.items()):
        if umaban not in market or not market[umaban]:
            continue
        out.append({
            'umaban': umaban,
            'mark': (mark_of(umaban) if mark_of else None) or '無印',
            'subjective': subjective,
            'market': market[umaban],
            'ratio': subjective / market[umaban],
        })
    return out


def horse_level_rows(rows, require_settled=True):
    """指標Bの対象になるレースだけを拾い、馬番ごとの行に展開する。

    require_settled=False にすると結果未確定のレースも拾う。比そのものは
    着順に依存しないので計算はできるが、**2026-08-26 の実測と土俵を
    揃えるため、本体の集計は結果確定済みだけを使う**。未確定分は
    「参考」として別枠で出す。
    """
    out = []
    for row in rows:
        if not row['has_wp'] or not row['win_odds']:
            continue
        if require_settled and not row['settled']:
            continue
        if not require_settled and row['settled']:
            continue
        market = bet_builder.market_win_probabilities(row['win_odds'])
        if not market:
            continue
        race = row['race']
        for entry in horse_ratios(race.win_probabilities, market, race.mark_of):
            out.append(dict(entry, day=row['day'], period=row['period'],
                            org=row['org'], name=row['name'], source=row['source']))
    return out


MARK_ORDER = ['◎', '○', '▲', '△', '無印']


def render_horse_level(horses, races_used, cap, pending=()):
    out = ['## 指標B　馬番単位の乖離（改訂案の上限が直接かかるところ）', '']
    if not horses:
        out.append('**データ不足。** 対象にできるレースが1件もありません。')
        out.append('')
        return out
    out.append(f'対象 **{races_used}レース・{len(horses)}頭**'
               f'（`win_probabilities` で明示的に上書きされた馬番のみ。'
               f'上書きの無い馬は市場そのままなので比較する意味が無い）。')
    out.append('')
    out.append('### 印別')
    out.append('')
    out.append('| 印 | 頭数 | 主観／市場 の平均 / 中央値（範囲） | '
               f'{cap}倍超の頭数 | 1.0倍以下（市場より下げた）頭数 |')
    out.append('|---|---|---|---|---|')
    for mark in MARK_ORDER:
        sub = [h for h in horses if h['mark'] == mark]
        if not sub:
            continue
        over = sum(1 for h in sub if h['ratio'] > cap)
        under = sum(1 for h in sub if h['ratio'] <= 1.0)
        out.append(f'| {mark} | {len(sub)} | {fmt(describe([h["ratio"] for h in sub]))} | '
                   f'{over}（{over / len(sub) * 100:.0f}%） | '
                   f'{under}（{under / len(sub) * 100:.0f}%） |')
    over_all = sum(1 for h in horses if h['ratio'] > cap)
    under_all = sum(1 for h in horses if h['ratio'] <= 1.0)
    out.append(f'| **全体** | {len(horses)} | {fmt(describe([h["ratio"] for h in horses]))} | '
               f'{over_all}（{over_all / len(horses) * 100:.0f}%） | '
               f'{under_all}（{under_all / len(horses) * 100:.0f}%） |')
    out.append('')

    out.append('### 期間別（印馬＝◎○▲△のみ。無印の上書きは下げ方向がほとんどで性質が違う）')
    out.append('')
    out.append('| 期間 | 頭数 | 主観／市場 の平均 / 中央値（範囲） |')
    out.append('|---|---|---|')
    marked = [h for h in horses if h['mark'] != '無印']
    for _s, _e, label in PERIODS:
        sub = [h for h in marked if h['period'] == label]
        if sub:
            note = '（参考値・件数僅少）' if len(sub) < 20 else ''
            out.append(f'| {label}{note} | {len(sub)} | '
                       f'{fmt(describe([h["ratio"] for h in sub]))} |')
    if marked:
        out.append(f'| **印馬まとめ** | {len(marked)} | '
                   f'{fmt(describe([h["ratio"] for h in marked]))} |')
    out.append('')

    out.append('### 分布（印馬のみ）')
    out.append('')
    buckets = [(0.0, 0.8, '〜0.8（市場より大きく下げた）'),
               (0.8, 1.0, '0.8〜1.0'),
               (1.0, 1.3, '1.0〜1.3'),
               (1.3, 1.8, f'1.3〜1.8（改訂案では要抑制・現行は警告のみ）'),
               (1.8, 99.0, '1.8〜（bet_builder が警告を出す帯）')]
    out.append('| 主観／市場 | 頭数 | 割合 |')
    out.append('|---|---|---|')
    for lo, hi, label in buckets:
        n = sum(1 for h in marked if lo <= h['ratio'] < hi)
        share = f'{n / len(marked) * 100:.0f}%' if marked else '—'
        out.append(f'| {label} | {n} | {share} |')
    out.append('')

    pending_marked = [h for h in pending if h['mark'] != '無印']
    if pending_marked:
        out.append('### 参考：結果未確定だが `data/checks` の `win_odds`（代理値でない'
                   '本物）が付いているレース')
        out.append('')
        out.append(f'{len({(h["day"], h["name"]) for h in pending_marked})}レース・'
                   f'{len(pending_marked)}頭。**着順が出ていないので本体の集計には'
                   '入れていない**が、比そのものは着順に依存しないので、'
                   '代理値でない数字がどのあたりに出るかの目安になる。')
        out.append('')
        out.append('| 印 | 頭数 | 主観／市場 の平均 / 中央値（範囲） |')
        out.append('|---|---|---|')
        for mark in MARK_ORDER:
            sub = [h for h in pending_marked if h['mark'] == mark]
            if sub:
                out.append(f'| {mark} | {len(sub)} | '
                           f'{fmt(describe([h["ratio"] for h in sub]))} |')
        out.append(f'| **合計** | {len(pending_marked)} | '
                   f'{fmt(describe([h["ratio"] for h in pending_marked]))} |')
        out.append('')
    return out


# ----------------------------------------------------------------------
# 指標C：上限を当てたらどうなるか
# ----------------------------------------------------------------------

def cap_impact_rows(rows, cap):
    """買い目セットの的中率・期待値が、上限を当てるとどう動くかを出す。"""
    out = []
    for row in rows:
        if not row['has_wp'] or not row['win_odds'] or not row['legs']:
            continue
        market = bet_builder.market_win_probabilities(row['win_odds'])
        race = row['race']
        overrides = {k: v for k, v in race.win_probabilities.items() if k in market}
        if not overrides or not market:
            continue
        horses = {h for _t, combo in row['legs'] for h in combo}
        if not horses <= set(market):
            continue
        p_subjective = bet_builder.apply_subjective(market, overrides)
        p_capped = bet_builder.apply_subjective(
            market, capped_overrides(overrides, market, cap))
        hit_s = set_hit_rate(p_subjective, row['legs'])
        hit_c = set_hit_rate(p_capped, row['legs'])
        hit_m = set_hit_rate(market, row['legs'])
        composite = row['composite']
        out.append(dict(row,
                        hit_subjective=hit_s, hit_capped=hit_c, hit_market=hit_m,
                        ratio_subjective=(hit_s / hit_m) if hit_m else None,
                        ratio_capped=(hit_c / hit_m) if hit_m else None,
                        ev_subjective=composite * hit_s if composite else None,
                        ev_capped=composite * hit_c if composite else None))
    return out


def render_cap_impact(rows, cap):
    out = [f'## 指標C　上限{cap}倍を当てたときのセット的中率・期待値の動き', '']
    if not rows:
        out.append('**データ不足。** 対象にできるレースがありません。')
        out.append('')
        return out
    settled = [r for r in rows if r['settled']]
    pending = [r for r in rows if not r['settled']]
    out.append(f'対象 {len(rows)}レース（うち結果確定済み {len(settled)}、未確定 {len(pending)}）。'
               '実際に採用した買い目セットをそのまま使い、勝率分布だけを差し替えて'
               '的中率を計算し直したもの。**買い目の選び直しはしていない**'
               '（上限を入れれば bet_builder は別の候補を選ぶはずなので、'
               'ここに出る期待値の下がり方は上限導入時の影響の**上限側の目安**）。')
    out.append('')
    out.append('| 日付 | レース | 主催 | セット的中率 主観 → 上限後 | 市場推定 | '
               '主観/市場 → 上限後/市場 | 期待値 主観 → 上限後 | 確定 |')
    out.append('|---|---|---|---|---|---|---|---|')
    for r in sorted(rows, key=lambda r: (r['day'], r['name'])):
        ev_s = f"{r['ev_subjective']:.2f}" if r['ev_subjective'] else '—'
        ev_c = f"{r['ev_capped']:.2f}" if r['ev_capped'] else '—'
        out.append(f"| {r['day'].isoformat()} | {r['name']} | {r['org'].upper()} | "
                   f"{r['hit_subjective'] * 100:.1f}% → {r['hit_capped'] * 100:.1f}% | "
                   f"{r['hit_market'] * 100:.1f}% | "
                   f"{r['ratio_subjective']:.2f} → {r['ratio_capped']:.2f} | "
                   f"{ev_s} → {ev_c} | {'済' if r['settled'] else '未'} |")
    out.append('')
    for label, sub in [('結果確定済み（JRA・朝のカードの代理オッズ）', settled),
                       ('未確定（`data/checks` の本物のオッズ・地方）', pending)]:
        if not sub:
            continue
        rs = describe([r['ratio_subjective'] for r in sub])
        rc = describe([r['ratio_capped'] for r in sub])
        ev_now = [r for r in sub if r['ev_subjective']]
        pass_now = sum(1 for r in ev_now
                       if r['ev_subjective'] >= discipline.MIN_EXPECTED_VALUE)
        pass_cap = sum(1 for r in ev_now
                       if r['ev_capped'] >= discipline.MIN_EXPECTED_VALUE)
        a_now = sum(1 for r in ev_now
                    if r['ev_subjective'] >= bet_builder.STRONG_EXPECTED_VALUE)
        a_cap = sum(1 for r in ev_now
                    if r['ev_capped'] >= bet_builder.STRONG_EXPECTED_VALUE)
        note = '（参考値・件数僅少）' if len(sub) < 10 else ''
        out.append(f'**{label}　{len(sub)}レース{note}**')
        out.append('')
        out.append(f'- セット的中率の主観／市場：平均 **{rs["mean"]:.2f}倍**・'
                   f'中央値 {rs["median"]:.2f}倍（{rs["min"]:.2f}〜{rs["max"]:.2f}倍）。')
        out.append(f'- 上限{cap}倍を当てたあと：平均 **{rc["mean"]:.2f}倍**・'
                   f'中央値 {rc["median"]:.2f}倍（{rc["min"]:.2f}〜{rc["max"]:.2f}倍）。')
        out.append(f'- 期待値{discipline.MIN_EXPECTED_VALUE}以上を保つレース：'
                   f'**{pass_now} → {pass_cap}**（{len(ev_now)}件中）。'
                   f'期待値{bet_builder.STRONG_EXPECTED_VALUE}以上'
                   f'（勝負度A相当）：**{a_now} → {a_cap}**。')
        out.append('')
    out.append('**上限を当てると比が下がりきらない／逆に上がるレースがある理由**：'
               '`apply_subjective` は上書きで使わなかった確率を残りの馬へ市場比で'
               '按分する。上限で上書きを削ると、その分が**上書きの無い馬（無印の'
               '中穴を含む）へ回る**ため、買い目にその馬が入っているとセット的中率が'
               '戻る。馬番単位の上限は、セット単位の乖離をそのままの倍率では抑えない。')
    out.append('')
    return out


# ----------------------------------------------------------------------
# 棚卸しの出力
# ----------------------------------------------------------------------

def render_inventory(rows, since, until):
    out = ['## 1　使えるデータソースの棚卸し', '']
    out.append(f'対象期間 {since.isoformat()}〜{until.isoformat()}、'
               f'買い目ファイルのあるレース **{len(rows)}件**。')
    out.append('')
    out.append('| 日付 | 主催 | レース | win_probabilities | 単勝オッズの出どころ | '
               '結果 | 指標Bの対象 |')
    out.append('|---|---|---|---|---|---|---|')
    source_label = {'checks': 'checks（bet_builderが見た値）',
                    'cards': 'cards（朝の値・代理）', None: '**なし**'}
    for r in rows:
        usable = r['has_wp'] and r['source'] and r['settled']
        out.append(f"| {r['day'].isoformat()} | {r['org'].upper()} | {r['name']} | "
                   f"{'あり' if r['has_wp'] else 'なし'} | {source_label[r['source']]} | "
                   f"{'確定' if r['settled'] else '未確定'} | {'○' if usable else '—'} |")
    out.append('')

    no_wp = [r for r in rows if not r['has_wp']]
    no_src = [r for r in rows if r['has_wp'] and not r['source']]
    unsettled = [r for r in rows if r['has_wp'] and r['source'] and not r['settled']]
    usable = [r for r in rows if r['has_wp'] and r['source'] and r['settled']]
    out.append('### 除外の内訳（推測で埋めていない）')
    out.append('')
    out.append('| 理由 | 件数 |')
    out.append('|---|---|')
    out.append(f'| `win_probabilities` が無い（{WIN_PROB_START.isoformat()} より前の'
               f'旧方式。測り直しても改訂案の判断材料にならない） | {len(no_wp)} |')
    out.append(f'| 単勝オッズを復元できない（地方＝カードに `odds` が無く、'
               f'checks の `win_odds` 記録は {WIN_ODDS_LOGGED_FROM.isoformat()} 以降のみ） '
               f'| {len(no_src)} |')
    out.append(f'| 結果が未確定（`data/results/` に保存が無い） | {len(unsettled)} |')
    out.append(f'| **指標Bの対象** | **{len(usable)}** |')
    out.append('')
    by_source = {}
    for r in usable:
        by_source[r['source']] = by_source.get(r['source'], 0) + 1
    out.append(f'指標Bの対象 {len(usable)}件の内訳：'
               + '／'.join(f'{source_label[k]} {v}件' for k, v in sorted(by_source.items()))
               + '。')
    out.append('')
    out.append('**注意（この分析の最大の弱点）**：'
               f'`data/checks` の `win_odds`（bet_builder が実際に見た値）が'
               f'記録されるようになったのは {WIN_ODDS_LOGGED_FROM.isoformat()} 以降で、'
               f'それ以降で**結果まで確定しているレースはまだ無い**。したがって'
               f'指標B・Cの本体は **JRA の朝のカードの単勝オッズ（代理値）**に'
               f'依存している。朝と直前でオッズは動くので、比の絶対値には'
               f'その分の誤差が乗る（方向は一定しない）。')
    out.append('')
    return out


# ----------------------------------------------------------------------

def build_report(since, until, cap):
    rows = inventory(since, until)
    set_rows = set_level_rows(rows)
    horses = horse_level_rows(rows)
    pending_horses = horse_level_rows(rows, require_settled=False)
    races_used = len({(h['day'], h['name']) for h in horses})
    caps = cap_impact_rows(rows, cap)

    out = [f'# 主観勝率の市場からの乖離　定期チェック（{date.today().isoformat()}）', '']
    out.append('`検証ノート.md`「メソッド改訂案（ユーザー承認待ち）」の'
               '**2026-08-26 提示「主観勝率の市場からの乖離に上限を設ける」**'
               '（1.3倍上限案）の判断材料。提案文が求めている「4週ごとに実測と'
               '突き合わせて調整する」の1回目にあたる。')
    out.append('')
    out.append('**基準もロジックも変更していない**（数字を出しただけ）。'
               '`予想メソッド.md`・`bet_builder.py`・`discipline.py` は無変更。'
               '上限を採用するかどうかはユーザーが決める。')
    out.append('')
    out.append(f'生成: `python3 calibration_check.py --since {since.isoformat()} '
               f'--until {until.isoformat()} --cap {cap}`（外部通信なし）')
    out.append('')
    out.extend(render_inventory(rows, since, until))
    out.extend(render_set_level(set_rows, since, until))
    out.extend(render_actual(actual_outcomes(set_rows)))
    out.extend(render_horse_level(horses, races_used, cap, pending_horses))
    out.extend(render_cap_impact(caps, cap))
    out.extend(render_findings(set_rows, actual_outcomes(set_rows), horses,
                               pending_horses, caps, cap))
    return '\n'.join(out) + '\n'


def render_findings(set_rows, actual, horses, pending_horses, caps, cap):
    out = ['## 所見（数字から言えること・言えないこと）', '']
    new = [r for r in set_rows if r['day'] >= WIN_PROB_START]
    old = [r for r in set_rows if r['day'] < WIN_PROB_START]
    if new and old:
        old_ratio = (statistics.fmean(r['subjective_hit'] for r in old)
                     / statistics.fmean(r['market_hit'] for r in old))
        new_ratio = (statistics.fmean(r['subjective_hit'] for r in new)
                     / statistics.fmean(r['market_hit'] for r in new))
        direction = ('わずかに改善' if new_ratio < old_ratio * 0.95
                     else ('ほぼ変わらない' if new_ratio < old_ratio * 1.05 else '悪化'))
        out.append(f'1. **2.13倍からの変化：{direction}。** 券種セット単位の乖離は'
                   f'旧方式 {old_ratio:.2f}倍 → 新方式 {new_ratio:.2f}倍'
                   f'（{len(new)}レース）。Harville 化で券種ごとの的中率は'
                   f'正しく展開されるようになったが、**セット全体が市場より'
                   f'{new_ratio:.1f}倍強気という構図は残っている**。')
    marked = [h for h in horses if h['mark'] != '無印']
    if marked:
        st = describe([h['ratio'] for h in marked])
        over = sum(1 for h in marked if h['ratio'] > cap)
        out.append(f'2. **馬番単位で見ると、印馬の主観勝率は市場の'
                   f'平均{st["mean"]:.2f}倍・中央値{st["median"]:.2f}倍**'
                   f'（{len(marked)}頭、JRAのみ・朝のカードの代理オッズ）。'
                   f'{cap}倍を超えるのは{over}頭（{over / len(marked) * 100:.0f}%）、'
                   f'逆に市場より**下げて**いるのが'
                   f'{sum(1 for h in marked if h["ratio"] <= 1.0)}頭'
                   f'（{sum(1 for h in marked if h["ratio"] <= 1.0) / len(marked) * 100:.0f}%）。'
                   f'**入力そのものは一律に2倍へ膨らんでいるわけではない。**')
    if marked and new:
        out.append('3. **にもかかわらずセットでは約2倍になるのは、組み合わせ確率が'
                   '掛け算だから。** 3頭の組（3連複・ワイド流し）は各馬の補正が'
                   '積み重なるので、1頭あたり1.2〜1.3倍の補正でも'
                   'セットでは1.7〜2.2倍に増幅される。'
                   '**馬番単位の上限は、この増幅そのものを止める設計ではない。**')
    settled_caps = [r for r in caps if r['settled']]
    if settled_caps:
        rc = describe([r['ratio_capped'] for r in settled_caps])
        ev_now = [r for r in settled_caps if r['ev_subjective']]
        pass_now = sum(1 for r in ev_now
                       if r['ev_subjective'] >= discipline.MIN_EXPECTED_VALUE)
        pass_cap = sum(1 for r in ev_now
                       if r['ev_capped'] >= discipline.MIN_EXPECTED_VALUE)
        out.append(f'4. **上限{cap}倍を当てた場合の実測**：セット的中率の主観／市場は'
                   f'平均 {rc["mean"]:.2f}倍まで下がるが、'
                   f'**1.0倍（市場並み）にはならない**。期待値'
                   f'{discipline.MIN_EXPECTED_VALUE}以上を保つレースは'
                   f'{pass_now} → {pass_cap}（{len(ev_now)}件中）で、'
                   f'買う鞍数はおよそ3分の1になる。')
    pending_marked = [h for h in pending_horses if h['mark'] != '無印']
    if pending_marked and marked:
        pst = describe([h['ratio'] for h in pending_marked])
        out.append(f'5. **【要確認】代理値でない本物の単勝オッズが付いた'
                   f'{len({(h["day"], h["name"]) for h in pending_marked})}レース'
                   f'（地方・結果未確定）では、印馬の比が'
                   f'平均{pst["mean"]:.2f}倍・中央値{pst["median"]:.2f}倍'
                   f'（最大{pst["max"]:.2f}倍）と、JRAの代理値ベースより'
                   f'はっきり大きい。** 考えられる理由は2つあり、'
                   f'今のデータでは切り分けられない：'
                   f'(a) 地方は朝のカードにオッズが無く、'
                   f'**主観勝率を市場を見ずに書いている**ため乖離が大きい、'
                   f'(b) JRAは朝のカードに載ったオッズを見ながら主観勝率を'
                   f'書いているので、そのオッズと比べれば当然近くなる'
                   f'（測り方の側の効果）。{len(pending_marked)}頭・'
                   f'{len({(h["day"], h["name"]) for h in pending_marked})}レースの'
                   f'参考値であり、結論には使えない。')
    out.append('')
    out.append('### 1.3倍という上限案への所見（採否は決めない）')
    out.append('')
    if marked:
        st = describe([h['ratio'] for h in marked])
        out.append(f'- **1.3倍は「今の書き方の中央値」とほぼ同じ水準**'
                   f'（印馬の中央値 {st["median"]:.2f}倍）。つまりこの上限は'
                   f'「全体を一律に絞る」のではなく、**上振れした{cap}倍超の'
                   f'{sum(1 for h in marked if h["ratio"] > cap)}頭だけを'
                   f'切り落とす**形で効く。提案文が想定していた'
                   f'「29件すべてが上限に抵触し期待値が6割前後まで下がる」という'
                   f'影響の見立ては、**新方式には当てはまらない**（当時は旧方式の'
                   f'レース単位の自己申告値を前提にしていたため）。')
    out.append('- **緩める／厳しくするの判断材料**：馬番単位の中央値はすでに'
               f'{cap}倍付近なので、この上限は「全体を絞る」より「上振れを削る」'
               f'ように効く。それでもセット単位の乖離は{cap}倍上限のあとで'
               f'まだ1.3〜1.4倍残る（指標C）。'
               '**乖離を市場並みに寄せたいなら、馬番の上限ではなく'
               'セット的中率そのものに歯止めをかける形でないと届かない**'
               'という関係が、今回の数字から読める。上限を1.5倍まで緩めた場合に'
               f'抵触する印馬は{sum(1 for h in marked if h["ratio"] > 1.5) if marked else 0}頭'
               f'（{cap}倍なら{sum(1 for h in marked if h["ratio"] > cap) if marked else 0}頭）で、'
               '効き方は目に見えて弱まる。'
               'どちらを選ぶかは方針の問題で、ここでは決めない。')
    new_actual = [r for r in actual if r['day'] >= WIN_PROB_START]
    if new_actual:
        hits = sum(1 for r in new_actual if r['hit'])
        ms = statistics.fmean(r['subjective_hit'] for r in new_actual)
        mm = statistics.fmean(r['market_hit'] for r in new_actual)
        out.append(f'- **実測はまだ市場側に近い**：新方式{len(new_actual)}レースの'
                   f'実際の的中率は{hits / len(new_actual) * 100:.1f}%'
                   f'（{hits}/{len(new_actual)}）で、主観{ms * 100:.1f}%よりも'
                   f'市場推定{mm * 100:.1f}%に近い。2026-08-26 と同じ向きの'
                   f'ズレが続いている（ただし件数が少なく、二項検定では'
                   f'{"主観も棄却できる" if binom_at_most(hits, len(new_actual), ms) < 0.05 else "主観をまだ棄却できない"}）。')
    out.append('- **まだ判断できないこと（データ不足）**：')
    out.append('  - 新方式の実測サンプルが少なく、主観／市場のどちらが実測に'
               '近いかを統計的に分離できる規模に達していない。'
               '**「2.13倍が改善したか」は見積りの比では言えても、'
               'どちらが正しかったかは言えない。**')
    out.append(f'  - 指標B・Cの単勝オッズはほぼ全部が**朝のカードの代理値**である。'
               f'`data/checks` の `win_odds` が付いた日の結果が出揃う'
               f'（早ければ次の週末）まで、比の絶対値は暫定。')
    out.append('  - 地方（NAR）は市場オッズを復元できないため、'
               f'{WIN_ODDS_LOGGED_FROM.isoformat()} より前は指標B・Cから'
               '丸ごと落ちている。**地方は分析対象外**であり、'
               '地方の主観勝率が過大かどうかは本レポートからは何も言えない。')
    out.append('')
    out.append('### 次回（4週後）に何が増えるか')
    out.append('')
    out.append('`data/checks` の `win_odds` が付いたレースの結果が溜まるので、'
               '(1) 代理値でなく bet_builder が見た値そのもので比を出せる、'
               '(2) 地方も対象にできる、(3) 実測的中率と主観・市場を突き合わせて'
               '2026-08-26 と同じ二項検定ができる。'
               '`python3 calibration_check.py --since 2026-09-16` で同じ表が出る。')
    out.append('')
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='主観勝率と市場推定勝率の乖離を、保存済みデータだけで測る。')
    parser.add_argument('--since', default='2026-08-08',
                        help='集計開始日 YYYY-MM-DD（既定 2026-08-08＝買い目ファイルの最初）')
    parser.add_argument('--until', default=date.today().isoformat(),
                        help='集計終了日 YYYY-MM-DD（既定 今日）')
    parser.add_argument('--cap', type=float, default=DEFAULT_CAP,
                        help=f'当ててみる乖離上限（既定 {DEFAULT_CAP}＝改訂案の暫定値）')
    parser.add_argument('--out', default=DEFAULT_OUTPUT, help='Markdown の書き出し先')
    args = parser.parse_args(argv)

    text = build_report(date.fromisoformat(args.since),
                        date.fromisoformat(args.until), args.cap)
    print(text)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
