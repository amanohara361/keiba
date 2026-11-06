#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Harvilleモデルの3連複確率が実際の決着と合っているかを測る（2026-11-06）。

## なぜこれがあるか

`bet_builder.py` は馬番ごとの勝率を Harville モデル（「1着の確率から2着・3着を
順に割り出す」式）に通して、3連複・ワイド・馬連の的中率を出している。
Harville は「強い馬が順に来る」「各馬は独立に走る」という前提なので、
(1) 人気馬が2・3着に残る確率を高く見すぎる、(2) ペースや馬場で前の馬・後ろの
馬がまとめて来る（展開の連動）を表せない、という2つの弱点が知られている。
2026-11-06、他人のAI予想（10万回のレースシミュレーション）の図を見たのを
きっかけに「まず今の式がどれだけずれているかを測ってから、展開を入れるか決める」
ことにした。その測定がこのスクリプト。

**数字を出すだけで、基準もロジックも変えない。** `bet_builder.py`・
`予想メソッド.md` には触らない。

## 測っているもの

  第1部  市場の単勝オッズ → Harville で出した3連複確率と、実際の決着の突き合わせ。
         `data_jra/`（JRA全レースの確定単勝オッズ・着順・通過順）を使う。
         比較対象として Henery 型の補正（2着・3着を決める段で勝率を
         λ乗して人気馬を割り引く。λ2=0.81, λ3=0.65 は Lo & Bacon-Shone の
         文献値）も並べる。
  第2部  展開の連動。1角の通過順で「前（前1/3）」「後（後ろ1/3）」に分け、
         上位3頭に何頭入ったかの分布を Harville の予測と比べる。通過順は
         **レースの後でしか分からない**ので、ここは「連動があるか」を見るだけで、
         予想にそのまま使える数字ではない。脚質ごとの強さの差（平均のずれ）を
         一律の倍率で吸収したあとも分布の偏りが残るかで、連動の大きさを測る。
  第3部  実際に買った買い目（2026-08-27 以降、win_probabilities のあるレース）で、
         今の方式（主観勝率×Harville）と Henery 補正の見積りを実測と並べる。

ネットワークには出ない（`data_jra/` と `data/results/` の保存済みデータだけを読む）。

使い方:
    python3 harville_check.py
    python3 harville_check.py --out data/review/harville_calibration_2026-11-06.md
"""

import argparse
import glob
import itertools
import json
import math
import os
import statistics
from collections import defaultdict
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))

# Lo & Bacon-Shone の文献値。このデータで当てはめた値ではない。
HENERY = (0.81, 0.65)
HARVILLE = (1.0, 1.0)
BINS = (0.0, 0.005, 0.01, 0.02, 0.04, 0.08, 0.16, 1.01)


def normalize(weights):
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def market_probabilities(win_odds):
    return normalize({k: 1.0 / v for k, v in win_odds.items()})


def top3_orders(p, lambdas=HARVILLE):
    """(1着, 2着, 3着) の並びと確率を列挙する。lambdas=(1, 1) で Harville。"""
    l2, l3 = lambdas
    q2 = p if l2 == 1 else normalize({k: v ** l2 for k, v in p.items()})
    q3 = p if l3 == 1 else normalize({k: v ** l3 for k, v in p.items()})
    for a, b, c in itertools.permutations(p, 3):
        r2, r3 = 1.0 - q2[a], 1.0 - q3[a] - q3[b]
        if r2 > 0 and r3 > 0:
            yield (a, b, c), p[a] * q2[b] / r2 * q3[c] / r3


def trio_probabilities(p, lambdas=HARVILLE):
    out = defaultdict(float)
    for order, prob in top3_orders(p, lambdas):
        out[frozenset(order)] += prob
    return out


def running_style(passes, field_size):
    """1角の通過順から 前／中／後 を返す（通過順が無ければ None）。"""
    if not passes:
        return None
    x = passes[0] / field_size
    return '前' if x <= 1 / 3 else ('後' if x > 2 / 3 else '中')


def load_jra_races():
    """data_jra/ から (確定単勝オッズ, 上位3頭, 脚質) を読む。同着のレースは除く。"""
    races = []
    for path in sorted(glob.glob(os.path.join(ROOT, 'data_jra', '*.json'))):
        with open(path, encoding='utf-8') as f:
            for race in json.load(f):
                horses = race['horses']
                if len(horses) < 5:
                    continue
                top3 = [h['馬番'] for h in horses if int(h['着順']) <= 3]
                if len(top3) != 3:
                    continue
                races.append({
                    'race_id': race['race_id'],
                    'odds': {h['馬番']: float(h['単勝']) for h in horses},
                    'top3': frozenset(top3),
                    'style': {h['馬番']: running_style(h.get('_passes'), len(horses))
                              for h in horses},
                })
    return races


# ----------------------------------------------------------------------
# 第1部　較正
# ----------------------------------------------------------------------

def calibration(races, lambdas):
    """確率帯ごとの (組数, 予測の合計, 実際の的中数) と、対数尤度、確率1位の3連複の成績。"""
    bins = [[0, 0.0, 0] for _ in BINS[:-1]]
    loglik = 0.0
    top = [0.0, 0]
    for race in races:
        trios = trio_probabilities(market_probabilities(race['odds']), lambdas)
        loglik += math.log(trios[race['top3']])
        best = max(trios, key=trios.get)
        top[0] += trios[best]
        top[1] += best == race['top3']
        for trio, prob in trios.items():
            for i in range(len(BINS) - 1):
                if BINS[i] <= prob < BINS[i + 1]:
                    bins[i][0] += 1
                    bins[i][1] += prob
                    bins[i][2] += trio == race['top3']
                    break
    return bins, loglik, top


def popularity_inplace(races, lambdas):
    """人気順位別の3着内率（予測と実測）。10番人気以下はまとめる。"""
    out = defaultdict(lambda: [0, 0.0, 0])
    for race in races:
        inplace = defaultdict(float)
        for trio, prob in trio_probabilities(market_probabilities(race['odds']), lambdas).items():
            for h in trio:
                inplace[h] += prob
        for rank, h in enumerate(sorted(race['odds'], key=race['odds'].get), 1):
            row = out[min(rank, 10)]
            row[0] += 1
            row[1] += inplace[h]
            row[2] += h in race['top3']
    return out


def fit_lambdas(races, grid=(0.5, 0.6, 0.7, 0.8, 0.9, 1.0)):
    """λ2・λ3 を格子で総当たりし、対数尤度が最大の組を返す（参考値）。"""
    best = None
    for l2 in grid:
        for l3 in grid:
            ll = sum(math.log(trio_probabilities(market_probabilities(r['odds']), (l2, l3))[r['top3']])
                     for r in races)
            if best is None or ll > best[2]:
                best = (l2, l3, ll)
    return best


# ----------------------------------------------------------------------
# 第2部　展開の連動
# ----------------------------------------------------------------------

def style_counts(races, target, lambdas, boost=1.0):
    """上位3頭に target の脚質が何頭いるかの分布（予測, 実測）と予測の平均。

    boost は target の馬の勝率に掛ける倍率。脚質ごとの強さの差（平均のずれ）を
    吸収したあとの分布の形を見るのに使う。
    """
    pred = [0.0] * 4
    actual = [0] * 4
    mean = 0.0
    for race in races:
        st = race['style']
        p = normalize({h: v * (boost if st[h] == target else 1.0)
                       for h, v in market_probabilities(race['odds']).items()})
        for trio, prob in trio_probabilities(p, lambdas).items():
            n = sum(st[h] == target for h in trio)
            pred[n] += prob
            mean += n * prob
        actual[sum(st[h] == target for h in race['top3'])] += 1
    return pred, actual, mean


def matched_boost(races, target, lambdas):
    """予測の平均頭数が実測と一致する倍率を二分法で求める。"""
    actual_mean = None
    lo, hi = 0.2, 5.0
    for _ in range(25):
        k = math.sqrt(lo * hi)
        pred, actual, mean = style_counts(races, target, lambdas, k)
        if actual_mean is None:
            actual_mean = sum(i * n for i, n in enumerate(actual))
        if mean < actual_mean:
            lo = k
        else:
            hi = k
    return k, pred, actual


def chi_square(pred, actual):
    return sum((a - p) ** 2 / p for p, a in zip(pred, actual) if p > 0)


# ----------------------------------------------------------------------
# 第3部　実際に買った買い目
# ----------------------------------------------------------------------

def set_rate(p, legs, lambdas):
    import bet_builder
    return sum(prob for order, prob in top3_orders(p, lambdas)
               if any(bet_builder._combo_hits(t, c, order) for t, c in legs))


def own_bets(since=date(2026, 8, 27), until=None):
    import bet_builder
    import calibration_check as cc
    import results as results_module
    import review
    rows = []
    for row in cc.inventory(since, until or date.today()):
        race = row['race']
        if not (row['has_wp'] and row['legs'] and row['win_odds'] and race.bets):
            continue
        result = review.cached_result(race.race_id)
        if not result:
            continue
        market = bet_builder.market_win_probabilities(row['win_odds'])
        if not market:
            continue
        overrides = {int(k): v for k, v in race.win_probabilities.items()}
        p = bet_builder.apply_subjective(market, overrides)
        legs = [(t, h) for t, h in row['legs'] if all(x in p for x in h)]
        if not legs:
            continue
        rows.append({
            'subjective_harville': set_rate(p, legs, HARVILLE),
            'subjective_henery': set_rate(p, legs, HENERY),
            'market_harville': set_rate(market, legs, HARVILLE),
            'market_henery': set_rate(market, legs, HENERY),
            'market_bet_odds': cc.market_hit_rate(row['legs'], row['odds']),
            'hit': bool(results_module.settle(race, result)['hit']),
        })
    return rows


# ----------------------------------------------------------------------
# 出力
# ----------------------------------------------------------------------

def render(races):
    out = ['# Harvilleモデルの較正と展開の連動（harville_check.py）', '']
    out.append('**基準もロジックも変更していない**（数字を出しただけ）。'
               '`bet_builder.py`・`予想メソッド.md` は無変更。')
    out.append('')
    files = sorted(glob.glob(os.path.join(ROOT, 'data_jra', '*.json')))
    span = f'{os.path.basename(files[0])[:8]}〜{os.path.basename(files[-1])[:8]}' if files else '-'
    out.append(f'対象：`data_jra/` の JRA {len(races)}レース（{span}、同着を除く）。'
               '勝率は確定単勝オッズから控除率を除いて正規化したもの。')
    out.append('')

    out.append('## 第1部　3連複確率の較正')
    out.append('')
    results = {name: calibration(races, lam) for name, lam in
               (('Harville', HARVILLE), ('Henery補正', HENERY))}
    out.append('| 確率帯 | Harville 組数 | 予測 | 実測 | 実測/予測 | Henery 組数 | 予測 | 実測 | 実測/予測 |')
    out.append('|---|---|---|---|---|---|---|---|---|')
    hv, hn = results['Harville'][0], results['Henery補正'][0]
    for i in range(len(BINS) - 1):
        a, b = hv[i], hn[i]
        out.append(f'| {BINS[i] * 100:.1f}〜{min(BINS[i + 1], 1) * 100:.1f}% '
                   f'| {a[0]} | {a[1]:.1f} | {a[2]} | {a[2] / a[1]:.2f} '
                   f'| {b[0]} | {b[1]:.1f} | {b[2]} | {b[2] / b[1]:.2f} |')
    out.append('')
    for name, (_bins, ll, top) in results.items():
        out.append(f'- {name}：確率1位の3連複は予測{top[0]:.1f}回に対し実測{top[1]}回'
                   f'（{top[1] / top[0]:.2f}倍）。対数尤度 {ll:.1f}')
    l2, l3, ll = fit_lambdas(races)
    out.append(f'- 参考：このデータで対数尤度が最大になる λ は (λ2, λ3) = ({l2}, {l3})、'
               f'対数尤度 {ll:.1f}（0.1刻みの格子）')
    out.append('')
    out.append('### 人気順位別の3着内率（Harville）')
    out.append('')
    out.append('| 人気 | 頭数 | 予測 | 実測 | 実測/予測 |')
    out.append('|---|---|---|---|---|')
    for rank, (n, pred, hit) in sorted(popularity_inplace(races, HARVILLE).items()):
        label = f'{rank}番人気' if rank < 10 else '10番人気以下'
        out.append(f'| {label} | {n} | {pred / n * 100:.1f}% | {hit / n * 100:.1f}% | {hit / pred:.2f} |')
    out.append('')

    out.append('## 第2部　展開の連動（上位3頭に同じ脚質が何頭入るか）')
    out.append('')
    styled = [r for r in races if None not in r['style'].values()]
    out.append(f'対象 {len(styled)}レース（通過順の欠けたレースを除く）。'
               '「平均をそろえた後」は、その脚質の勝率に一律の倍率を掛けて平均頭数を'
               '実測に合わせたもの。それでも0頭・3頭が予測より多ければ、'
               '前の馬・後ろの馬がまとめて来る連動がある。')
    out.append('')
    for target, label in (('前', '前（1角で前1/3）'), ('後', '後（1角で後ろ1/3）')):
        raw_pred, actual, _ = style_counts(styled, target, HARVILLE)
        k, pred, _ = matched_boost(styled, target, HARVILLE)
        out.append(f'**{label}**　倍率 {k:.2f}、平均をそろえた後のカイ二乗 {chi_square(pred, actual):.1f}'
                   '（自由度2の5%点は5.99）')
        out.append('')
        out.append('| 頭数 | Harville そのまま | 平均をそろえた後 | 実測 |')
        out.append('|---|---|---|---|')
        for n in range(4):
            out.append(f'| {n}頭 | {raw_pred[n]:.1f} | {pred[n]:.1f} | {actual[n]} |')
        out.append('')

    out.append('## 第3部　実際に買った買い目（2026-08-27〜、結果確定分）')
    out.append('')
    rows = own_bets()
    if rows:
        hits = sum(r['hit'] for r in rows)
        out.append('| 見積りの出し方 | 的中率の平均 |')
        out.append('|---|---|')
        for key, label in (('subjective_harville', '主観勝率 × Harville（今の方式）'),
                           ('subjective_henery', '主観勝率 × Henery補正'),
                           ('market_harville', '市場の単勝 × Harville'),
                           ('market_henery', '市場の単勝 × Henery補正'),
                           ('market_bet_odds', '市場（買った券種の発走前オッズ）')):
            out.append(f'| {label} | {statistics.fmean(r[key] for r in rows) * 100:.1f}% |')
        out.append(f'| **実測** | **{hits / len(rows) * 100:.1f}%（{hits}/{len(rows)}）** |')
        out.append('')
    else:
        out.append('対象レースがありません。')
        out.append('')
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', help='Markdown の書き出し先（省略時は標準出力）')
    args = parser.parse_args(argv)
    text = '\n'.join(render(load_jra_races())) + '\n'
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(text)
    else:
        print(text)


if __name__ == '__main__':
    main()
