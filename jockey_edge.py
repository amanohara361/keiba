#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""騎手ごとに「オッズから見込まれる勝利数」と実際の勝利数の差を測る。

## なぜこれがあるか

2026-11-03、動画「2026年に成績が激変した騎手」を手元の data_jra で検算したところ、
主張どおりだったのは一部の騎手だけで、単勝回収率は穴馬の1勝で大きく跳ねていた
（例：7番人気以下で2勝しただけの回収率483%）。回収率では「一撃」と「本当に
市場より勝っている」が区別できないので、**オッズに織り込まれていない分だけ**を測る。

予想メソッド第4章は「騎手の最近の調子は単独では判断材料にしない」「鞍上強化は
オッズに織り込み済み」。この集計はそれを毎週同じ物差しで確かめるためのもので、
**数字を出すだけで、予想メソッドにも買い目にも一切触らない。**
差が長く続く騎手が出たら、人が検証ノートに根拠付きで提案する（`docs/決定ログ.md` 2026-11-03）。

## 測り方

- 対象：`data_jra/*.json` の全レース。単勝オッズと着順のそろった馬。
- 見込み勝率：同じレースの 1/オッズ を合計1に割り戻した値（控除率の分を消す）。
- 差 = 実際の勝利数 − 見込み勝利数の合計。
- z = 差 ÷ √Σp(1−p)。騎乗数の違う騎手を同じ目盛りで比べるため。|z|≥2 を目安に並べる。
- 単勝回収率は参考に、最も大きい1勝の払戻を抜いた値（一撃抜き）も出す。

## 騎手名のゆれ

data_jra には「岩田望」と「岩田望来」のように略名とフルネームが混じる（取得元の違い）。
減量の記号（◇▲△☆★）と「Ｍ．デム」の点を外し、ほかの騎手名が前に一致する名前は
いちばん長い一致先に寄せる。略名が別々の騎手の頭に一致するとき（「小林」に対して
「小林美」「小林凌」）は寄せない。前方一致で寄らないゆれは ALIASES に書く。
略名の騎手と、同じ頭の別人が1人だけいる場合は誤って寄る（完全には防げない）。

使い方: python jockey_edge.py [data_jra のディレクトリ]
"""

import glob
import json
import math
import os
import re
import sys
from collections import defaultdict
from datetime import date, timedelta

# 表に載せる最低騎乗数。これ未満は z が安定しない。
MIN_RIDES = 50
# 「市場より勝っている／負けている」と並べる目安
Z_MARK = 2.0
# 直近の窓（日数）
RECENT_DAYS = 56

_MARKS = '◇▲△☆★'

# 前方一致では寄らない表記ゆれ（data_jra で実際に見つかったもの）
ALIASES = {
    '大江原圭': '大江圭',
    '鮫島克駿': '鮫島駿',
    '石神深道': '石神道',
    '角田大和': '角田和',
    # 略名が2人の頭に一致して自動では寄らないもの。JRA所属の側を略名に寄せる
    # （石川倭・吉村智洋は地方所属の別人。2026-11-03 時点の data_jra で確認）
    '原優介': '原',
    '石川裕紀': '石川',
    '吉村誠之': '吉村',
}


def _strip(name):
    n = str(name or '').strip().lstrip(_MARKS).strip().replace('．', '').replace('.', '')
    return ALIASES.get(n, n)


def _roots(names):
    """ほかのどれにも前方一致しない名前だけを残す。"""
    return {n for n in names if not any(m != n and n.startswith(m) for m in names)}


def canonical_map(names):
    """名前→寄せ先。ほかの名前が前方一致する名前は、いちばん長い一致先に寄せる。

    ただし寄せ先の略名が別々の騎手の頭に一致するとき（略名「小林」に対して
    「小林美」「小林凌」がある等）は、どれのことか決められないので寄せない。
    """
    names = {n for n in names if n}
    ambiguous = {m for m in names
                 if len(_roots({n for n in names if n != m and n.startswith(m)})) > 1}
    out = {}
    for n in names:
        cands = [m for m in names if m != n and len(m) >= 2 and n.startswith(m)
                 and m not in ambiguous]
        out[n] = max(cands, key=len) if cands else n
    # 「津村明秀」→「津村明」→「津村」のように多段になるので、行き止まりまでたどる
    for n in out:
        while out[out[n]] != out[n]:
            out[n] = out[out[n]]
    return out


def _file_date(path):
    m = re.match(r'(\d{8})_', os.path.basename(path))
    if not m:
        return None
    s = m.group(1)
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def load_rides(paths):
    """各馬を (日付, 騎手, 見込み勝率, 勝ったか, 単勝オッズ, 人気) にする。"""
    raw = []
    for path in paths:
        day = _file_date(path)
        with open(path, encoding='utf-8') as f:
            races = json.load(f)
        for r in races:
            hs = []
            for h in r.get('horses', []):
                try:
                    o = float(h['単勝'])
                    fin = int(h['着順'])
                except (KeyError, TypeError, ValueError):
                    continue  # 取消・除外・中止など
                if o <= 0:
                    continue
                try:
                    pop = int(h.get('人気'))
                except (TypeError, ValueError):
                    pop = None
                hs.append((_strip(h.get('騎手')), o, fin, pop))
            if len(hs) < 2:
                continue
            total = sum(1 / o for _, o, _, _ in hs)
            for j, o, fin, pop in hs:
                raw.append((day, j, (1 / o) / total, fin == 1, o, pop))
    cmap = canonical_map({r[1] for r in raw})
    return [(d, cmap.get(j, j), p, w, o, pop) for d, j, p, w, o, pop in raw]


def _stats(rides):
    n = len(rides)
    wins = sum(1 for r in rides if r[3])
    exp = sum(r[2] for r in rides)
    var = sum(r[2] * (1 - r[2]) for r in rides)
    pays = sorted((r[4] for r in rides if r[3]), reverse=True)
    roi = sum(pays) / n if n else 0.0
    roi_cut = sum(pays[1:]) / (n - 1) if n > 1 else 0.0
    return dict(n=n, wins=wins, exp=exp, diff=wins - exp,
                z=(wins - exp) / math.sqrt(var) if var > 0 else 0.0,
                roi=roi, roi_cut=roi_cut)


def summarize(rides, end=None, recent_days=RECENT_DAYS):
    """騎手→{'all': 通算, 'recent': 直近, 'fav': 1〜3番人気, 'other': 4番人気以下}。"""
    days = [r[0] for r in rides if r[0]]
    end = end or (max(days) if days else None)
    since = end - timedelta(days=recent_days - 1) if end else None
    by = defaultdict(list)
    for r in rides:
        by[r[1]].append(r)
    out = {}
    for j, rs in by.items():
        out[j] = dict(
            all=_stats(rs),
            recent=_stats([r for r in rs if since and r[0] and r[0] >= since]),
            fav=_stats([r for r in rs if r[5] is not None and r[5] <= 3]),
            other=_stats([r for r in rs if r[5] is None or r[5] > 3]),
        )
    return out


def analyze(data_dir='data_jra'):
    paths = sorted(glob.glob(os.path.join(data_dir, '*.json')))
    if not paths:
        return {}, []
    return summarize(load_rides(paths)), paths


def _row(j, s):
    a, rc, fv, ot = s['all'], s['recent'], s['fav'], s['other']
    return (f"{j[:6]:<6s}{a['n']:5d}{a['wins']:5d}{a['exp']:7.1f}{a['diff']:+7.1f}{a['z']:+6.1f}"
            f"{rc['diff']:+7.1f}{fv['diff']:+7.1f}{ot['diff']:+7.1f}"
            f"{a['roi']:7.0%}{a['roi_cut']:7.0%}")


def render_lines(summary, paths, min_rides=MIN_RIDES, top=10):
    lines = [f'対象: {os.path.basename(paths[0])} 〜 {os.path.basename(paths[-1])}'
             f'（全レース、{min_rides}騎乗以上）',
             '見込み＝オッズから割り戻した勝率の合計。差＝実勝−見込み。'
             f'直近＝最終日から{RECENT_DAYS}日。上位＝1〜3番人気、他＝4番人気以下',
             '回収抜き＝最大の1勝を抜いた単勝回収率（一撃の影響を見る）',
             f"{'騎手':<6s}{'騎乗':>5s}{'勝':>5s}{'見込':>7s}{'差':>7s}{'z':>6s}"
             f"{'直近差':>7s}{'上位差':>7s}{'他差':>7s}{'単回':>7s}{'回収抜き':>7s}"]
    rows = sorted(((j, s) for j, s in summary.items() if s['all']['n'] >= min_rides),
                  key=lambda x: -x[1]['all']['z'])
    if not rows:
        return lines + ['（対象の騎手がいません）']
    lines.append('-- 市場より勝っている側（z の高い順）')
    lines += [_row(j, s) for j, s in rows[:top]]
    lines.append('-- 市場より負けている側（z の低い順）')
    lines += [_row(j, s) for j, s in reversed(rows[-min(5, len(rows)):])]
    lines += ['', watch_line(summary, min_rides)]
    return lines


def watch_line(summary, min_rides=MIN_RIDES):
    """|z|≥2 の騎手の一覧。週次レビューのメールにもこの1行を載せる。"""
    hits = sorted(((j, s['all']) for j, s in summary.items()
                   if s['all']['n'] >= min_rides and abs(s['all']['z']) >= Z_MARK),
                  key=lambda x: -x[1]['z'])
    if not hits:
        return f'|z|≥{Z_MARK:g} の騎手はいません（観察のみ。規則にはしない）。'
    body = '、'.join(f"{j}({a['z']:+.1f})" for j, a in hits)
    return f'|z|≥{Z_MARK:g}：{body}（観察のみ。規則にはしない）'


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
