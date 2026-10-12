#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""馬場状態（良／稍重／重・不良）別に、相対馬体重と「人気より走れたか」の関係を測る。

## なぜこれがあるか

2026-10-12 のユーザーの仮説「欧州の粘る芝では、同じパワーなら軽い馬が有利では
（ゴルフのラフで重いボールほど沈むのと同じ）」を、国内の道悪で代理検証するため。
初回集計（芝396鞍）では、オッズ補正後の残差が「重い馬ほど人気より走らない」向きで、
その傾きが 良→稍重→重・不良 の順に急になった。ただし重・不良は33鞍しかなく、
信頼区間が0をまたいだ。**データが貯まるまで規則にはしない**（`docs/決定ログ.md`）。

**数字を出すだけで、予想メソッドにも買い目にも一切触らない。**

## 測り方

- 対象：`data_jra/*.json` の芝、馬体重と単勝オッズのそろった馬が5頭以上のレース。
- 相対体重：その馬の体重 − 同レースの平均体重（距離・性齢の体格差を消す）。
- 残差：単勝オッズ帯ごとの平均着順率 − その馬の着順率（着順率 = (着順-1)/(頭数-1)）。
  ＋なら人気より上に来た。「人気−着順」は人気馬ほど負に出る癖があるので使わない。
- 傾き：残差を相対体重で回帰した10kgあたりの傾き。95%区間はレース単位の
  ブートストラップ（同じレースの馬同士は独立でないため）。

使い方: python weight_going.py [data_jra のディレクトリ]
"""

import glob
import json
import os
import random
import re
import statistics as st
import sys
from collections import defaultdict

GOINGS = ('良', '稍重', '重・不良')
# 規則化を検討してよい重・不良の鞍数（2026-10-12 決定ログ）
READY_RACES = 60


def going_class(state):
    return {'良': '良', '稍重': '稍重'}.get(state, '重・不良')


def odds_band(odds):
    """単勝オッズを平方根で刻む。低オッズ側ほど細かく分かれる。"""
    return min(int(odds ** 0.5 * 2), 20)


def load_rows(paths):
    """芝の各馬を (race_id, 馬場区分, 相対体重, オッズ帯, 着順率) にする。"""
    rows = []
    for path in paths:
        with open(path, encoding='utf-8') as f:
            races = json.load(f)
        for r in races:
            if r.get('馬場') != '芝':
                continue
            hs = []
            for h in r.get('horses', []):
                m = re.match(r'(\d+)', str(h.get('馬体重') or ''))
                try:
                    hs.append((int(m.group(1)), float(h['単勝']), int(h['着順'])))
                except (AttributeError, KeyError, TypeError, ValueError):
                    continue  # 取消・除外・計不など
            if len(hs) < 5:
                continue
            mean_w = st.mean(w for w, _, _ in hs)
            n = len(hs)
            g = going_class(r.get('馬場状態'))
            for w, o, fin in hs:
                rows.append((r['race_id'], g, w - mean_w, odds_band(o), (fin - 1) / (n - 1)))
    return rows


def residuals(rows):
    base = defaultdict(list)
    for _, _, _, band, pct in rows:
        base[band].append(pct)
    mean_by_band = {k: st.mean(v) for k, v in base.items()}
    return [(rid, g, rw, mean_by_band[band] - pct) for rid, g, rw, band, pct in rows]


def slope(rs):
    """残差を相対体重で回帰した傾き（10kgあたり）。

    ブートストラップで数千回呼ぶので、statistics.mean を使わず和だけで計算する。
    """
    n = sx = sy = sxx = sxy = 0.0
    for r in rs:
        x, y = r[2], r[3]
        n += 1
        sx += x
        sy += y
        sxx += x * x
        sxy += x * y
    var = sxx - sx * sx / n if n else 0.0
    return 10 * (sxy - sx * sy / n) / var if var > 1e-12 else 0.0


def bootstrap_ci(by_race, n_iter=2000, seed=1):
    rng = random.Random(seed)
    ids = list(by_race)
    bs = sorted(slope([r for i in rng.choices(ids, k=len(ids)) for r in by_race[i]])
                for _ in range(n_iter))
    return bs[int(n_iter * 0.025)], bs[int(n_iter * 0.975)]


def band_label(rw):
    return '軽' if rw <= -20 else ('重' if rw >= 20 else '中')


def summarize(rs):
    out = {}
    for g in GOINGS:
        by_race = defaultdict(list)
        for r in rs:
            if r[1] == g:
                by_race[r[0]].append(r)
        if not by_race:
            continue
        flat = [r for v in by_race.values() for r in v]
        bands = {}
        for lab in ('軽', '中', '重'):
            x = [r[3] for r in flat if band_label(r[2]) == lab]
            bands[lab] = (len(x), st.mean(x) if x else None)
        out[g] = dict(races=len(by_race), bands=bands, slope=slope(flat),
                      ci=bootstrap_ci(by_race))
    return out


def analyze(data_dir='data_jra'):
    """集計結果（馬場区分→要約）と対象ファイル一覧を返す。データが無ければ ({}, [])。"""
    paths = sorted(glob.glob(os.path.join(data_dir, '*.json')))
    if not paths:
        return {}, []
    return summarize(residuals(load_rows(paths))), paths


def heavy_races(summary):
    return summary.get('重・不良', {}).get('races', 0)


def progress_line(summary):
    """重・不良の鞍数と目安までの残り。週次レビューのメールにもこの1行を載せる。"""
    heavy = heavy_races(summary)
    if heavy >= READY_RACES:
        return f'重・不良が{heavy}鞍（目安{READY_RACES}鞍に到達）。規則化の検討に進めます。'
    return f'重・不良は{heavy}鞍（目安{READY_RACES}鞍まであと{READY_RACES - heavy}鞍）。'


def render_lines(summary, paths):
    lines = [f'対象: {os.path.basename(paths[0])} 〜 {os.path.basename(paths[-1])}（芝）',
             '残差は＋なら人気より上位。軽=平均−20kg以下 / 重=平均＋20kg以上',
             f"{'馬場':6s}{'鞍数':>5s}  {'軽':>14s}{'中':>14s}{'重':>14s}   傾き/10kg [95%区間]"]
    for g, s in summary.items():
        cells = ''.join(f'{m:+.3f}({n:4d})'.rjust(14) if m is not None else '-'.rjust(14)
                        for n, m in s['bands'].values())
        lo, hi = s['ci']
        lines.append(f"{g:6s}{s['races']:5d}  {cells}   {s['slope']:+.4f} [{lo:+.4f},{hi:+.4f}]")
    lines += ['', progress_line(summary)]
    return lines


def main(argv):
    d = argv[1] if len(argv) > 1 else 'data_jra'
    summary, paths = analyze(d)
    if not paths:
        print(f'{d} にデータがありません')
        return 1
    print('\n'.join(render_lines(summary, paths)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
