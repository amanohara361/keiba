#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自前のタイム指数（speed_index.py）は、人気（単勝オッズ）を超える情報を持つか（2026-11-25）。

各レースの出走馬について、そのレースより前の同じ芝ダの指数から次の3通りを作る。
- 前走：同じ芝ダの直近1走
- 近3走最高：同じ芝ダの直近3走の最高
- 近2走平均：同じ芝ダの直近2走の平均
出走馬の7割以上に値があり、5頭以上のレースだけを数える（値の無い馬は順位に入れない）。

見るもの
1. 指数1位の馬の勝率・3着内率・単勝回収率（1番人気と同じレースで比べる）
2. 条件付きロジット：勝つ確率 ∝ exp(a × log(市場の確率) ＋ b × 指数のレース内z値)。
   b が 0 なら指数は人気に織り込み済み。b > 0 で有意なら人気の外に情報がある。
3. 人気帯ごとに、指数上位2頭とそれ以外の勝率・単勝回収率

オッズは data_jra の確定単勝（市場の最終評価）。朝の時点のオッズとは違う。

使い方: python backtest_speed_index.py [--md 出力先]
"""
import argparse
import math
import sys

import numpy as np

import speed_index as si

HALF = '20261012'
FEATURES = {
    '前走': lambda xs: xs[-1],
    '近3走最高': lambda xs: max(xs[-3:]),
    '近2走平均': lambda xs: sum(xs[-2:]) / len(xs[-2:]),
}
MIN_FIELD = 5
MIN_COVER = 0.7


def build(races=None):
    races = si.index_runs(races or si.load_results())
    hist = si.history(races)
    rows = []  # (race, [(run, {feature: value})])
    for race in races:
        if any(r['odds'] is None for r in race['runs']) or len(race['runs']) < MIN_FIELD:
            continue
        horses = []
        for run in race['runs']:
            past = [x[2] for x in hist.get(run['name'], [])
                    if x[0] < race['date'] and x[1] == race['surface']]
            horses.append((run, {k: f(past) for k, f in FEATURES.items()} if past else {}))
        rows.append((race, horses))
    return rows


def eligible(rows, feature):
    out = []
    for race, horses in rows:
        have = [h for h in horses if feature in h[1]]
        if len(have) >= MIN_COVER * len(horses):
            out.append((race, horses))
    return out


def top_by(horses, feature):
    have = [h for h in horses if feature in h[1]]
    return sorted(have, key=lambda h: (-h[1][feature], h[0]['umaban']))


def summary(races, pick):
    n = win = top3 = 0
    ret = 0.0
    pops = []
    for race, horses in races:
        run = pick(race, horses)
        if run is None:
            continue
        n += 1
        win += run['finish'] == 1
        top3 += run['finish'] <= 3
        ret += run['odds'] * 100 if run['finish'] == 1 else 0
        if run['pop']:
            pops.append(run['pop'])
    return n, win, top3, ret, (sum(pops) / len(pops) if pops else 0)


def clogit(races, feature, with_index=True):
    """a（市場）と b（指数z値）を最尤推定。戻り値 (params, se, loglik, レース数)。

    with_index=False は市場だけ、'only' は指数だけ（指数そのものが働いているかの確認）。
    """
    data = []
    for race, horses in races:
        p = np.array([1 / h[0]['odds'] for h in horses])
        p = p / p.sum()
        vals = [h[1].get(feature) for h in horses]
        known = [v for v in vals if v is not None]
        mu, sd = np.mean(known), (np.std(known) or 1.0)
        z = np.array([(v - mu) / sd if v is not None else 0.0 for v in vals])
        y = np.array([h[0]['finish'] == 1 for h in horses], dtype=float)
        if y.sum() != 1:
            continue  # 同着は外す
        if with_index == 'only':
            X = z[:, None]
        elif with_index:
            X = np.column_stack([np.log(p), z])
        else:
            X = np.log(p)[:, None]
        data.append((X, y))
    k = data[0][0].shape[1]
    beta = np.zeros(k)
    beta[0] = 0.0 if with_index == 'only' else 1.0
    for _ in range(50):
        grad = np.zeros(k)
        hess = np.zeros((k, k))
        ll = 0.0
        for X, y in data:
            u = X @ beta
            u -= u.max()
            w = np.exp(u)
            pr = w / w.sum()
            ll += float(np.log(pr[y == 1][0]))
            xbar = pr @ X
            grad += X[y == 1][0] - xbar
            hess -= (X * pr[:, None]).T @ X - np.outer(xbar, xbar)
        step = np.linalg.solve(hess, grad)
        beta -= step
        if np.abs(step).max() < 1e-8:
            break
    se = np.sqrt(np.diag(np.linalg.inv(-hess)))
    return beta, se, ll, len(data)


def pct(a, b):
    return f'{a}/{b}（{a / b:.1%}）' if b else '-'


def report(rows):
    out = ['# 自前タイム指数のバックテスト（中央、data_jra）', '',
           '`python backtest_speed_index.py` の出力。指数の作り方は speed_index.py。'
           '指数は「そのレースより前の、同じ芝ダの走」だけから作る。オッズは確定単勝。', '',
           '**注意**：コース基準・クラス差は全期間で推定している（先の日付の情報が基準にだけ混ざる）。'
           f'データは 2026-07-26 から。出走馬の{MIN_COVER:.0%}以上に値があるレースだけを数えている（新馬・2歳戦の多くは入らない）。', '']

    # 1. 指数1位
    out += ['## 1. 指数1位の馬', '',
            '| 指数 | レース数 | 指数1位の勝率 | 3着内率 | 単勝回収率 | 平均人気 | 同じレースの1番人気の勝率 | 1番人気の回収率 |',
            '|---|---|---|---|---|---|---|---|']
    for feat in FEATURES:
        races = eligible(rows, feat)
        n, w, t3, ret, pop = summary(races, lambda r, hs, f=feat: (top_by(hs, f) or [(None,)])[0][0])
        fn, fw, _, fret, _ = summary(races, lambda r, hs: min(hs, key=lambda h: h[0]['odds'])[0])
        out.append(f'| {feat} | {n} | {pct(w, n)} | {pct(t3, n)} | {ret / n:.0f}% | {pop:.1f} | {pct(fw, fn)} | {fret / fn:.0f}% |')

    # 2. 条件付きロジット
    out += ['', '## 2. 人気に織り込み済みか（条件付きロジット）', '',
            '勝つ確率 ∝ exp(a×log(市場の確率) ＋ b×指数のz値)。b の t値が2を超えれば、人気の外に情報がある。'
            '対数尤度の差は「指数を足して説明が良くなった分」。', '',
            '「指数だけ」は市場を入れずに当てはめた b（指数そのものが勝ち馬と結びついているかの確認）。', '',
            '| 指数 | 期間 | レース数 | 指数だけのb（t値） | a（市場） | b（指数） | bのt値 | 対数尤度の改善 |',
            '|---|---|---|---|---|---|---|---|']
    for feat in FEATURES:
        races = eligible(rows, feat)
        for label, part in (('全期間', races), ('10-12より前', [r for r in races if r[0]['date'] < HALF]),
                            ('10-12以降', [r for r in races if r[0]['date'] >= HALF])):
            if len(part) < 30:
                continue
            (a, b), (sa, sb), ll, m = clogit(part, feat)
            _, _, ll0, _ = clogit(part, feat, with_index=False)
            (bo,), (so,), _, _ = clogit(part, feat, with_index='only')
            out.append(f'| {feat} | {label} | {m} | {bo:+.2f}（{bo / so:+.1f}） | {a:.2f}（±{sa:.2f}） | {b:+.3f}（±{sb:.3f}） | {b / sb:+.1f} | {ll - ll0:+.1f} |')

    # 3. 人気帯
    feat = '近3走最高'
    races = eligible(rows, feat)
    out += ['', f'## 3. 人気帯ごと（{feat}の上位2頭とそれ以外）', '',
            '| 人気 | 指数上位2頭：頭数 | 勝率 | 単勝回収率 | それ以外：頭数 | 勝率 | 単勝回収率 |', '|---|---|---|---|---|---|---|']
    bands = ((1, 1), (2, 3), (4, 6), (7, 9), (10, 99))
    for lo, hi in bands:
        cells = []
        for want_top in (True, False):
            n = w = 0
            ret = 0.0
            for race, horses in races:
                tops = {id(h[0]) for h in top_by(horses, feat)[:2]}
                for run, _ in horses:
                    if not run['pop'] or not lo <= run['pop'] <= hi or (id(run) in tops) != want_top:
                        continue
                    n += 1
                    if run['finish'] == 1:
                        w += 1
                        ret += run['odds'] * 100
            cells += [str(n), pct(w, n), f'{ret / n:.0f}%' if n else '-']
        label = f'{lo}番人気' if lo == hi else (f'{lo}〜{hi}番人気' if hi < 99 else f'{lo}番人気以下')
        out.append(f'| {label} | ' + ' | '.join(cells) + ' |')
    out += ['', '単勝回収率は控除率（約20%）を含む。人気帯の中で上位2頭が「それ以外」より明らかに高ければ、人気の外に情報がある。']
    return '\n'.join(out) + '\n'


def main(argv=None):
    global MIN_COVER
    parser = argparse.ArgumentParser()
    parser.add_argument('--md')
    parser.add_argument('--cover', type=float, default=MIN_COVER, help='出走馬のうち指数がある割合の下限')
    args = parser.parse_args(argv)
    MIN_COVER = args.cover
    text = report(build())
    if args.md:
        with open(args.md, 'w', encoding='utf-8') as f:
            f.write(text)
    print(text)


if __name__ == '__main__':
    sys.exit(main())
