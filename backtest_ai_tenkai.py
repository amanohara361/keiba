#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""netkeiba の AI展開予測（無料部分）は逃げ馬を当てられるか（2026-11-25）。

2026-11-24 に、逃げ候補一覧の土台を「直近3走の重み付き初角位置の上位5頭」
（card.pace_candidates）にした。AI展開予測を朝の材料に足す前に、同じ物差しで比べる。
- 逃げ馬 … 実際に初角を先頭で通った馬（data_jra の _passes[0] == 1。同着の先頭を含む）
- AI の順位 … 「スタート後」の隊列を前から数えたもの（取消・除外の馬は除く）
- 4角は参考：AI の「4コーナー」の前3頭に、実際の最終コーナー先頭がいたか

AI のページは data/ai_tenkai/backtest_pages.json（取得は ai_tenkai.fetch）。
過去レースのページの予測は発走前と同じ（2026-11-25 に地方36鞍で、発走前と確定後が完全一致。
docs/決定ログ.md 2026-11-25）。

使い方: python backtest_ai_tenkai.py [--md 出力先]
"""
import argparse
import json
import os
import sys

import ai_tenkai
import backtest_pace_candidates as bpc
import card

ROOT = os.path.dirname(os.path.abspath(__file__))
PAGES = os.path.join(ROOT, 'data', 'ai_tenkai', 'backtest_pages.json')


def load(pages_path=PAGES):
    with open(pages_path, encoding='utf-8') as f:
        pages = json.load(f)
    rows = []
    for date, race, actual in bpc.load_races():
        pred = pages.get(race['race_id'])
        if not pred:
            continue
        entries = [e for e in race['entries'] if e.get('umaban') in actual]
        ours = [r['umaban'] for r in card.pace_candidates(dict(race, entries=entries), top=99)['top']]
        row = {'date': date, 'race_id': race['race_id'], 'actual': actual, 'ours': ours,
               'last': _last_corner(race, date)}
        for mode in ('plain', 'jockey'):
            row[mode] = [u for u in ai_tenkai.order(pred, mode, 'start') if u in actual]
            row[mode + '_c4'] = [u for u in ai_tenkai.order(pred, mode, 'c4') if u in actual]
        if row['plain']:
            rows.append(row)
    return rows


def _last_corner(race, date):
    path = os.path.join(ROOT, 'data_jra', f"{date.replace('-', '')}_{race['venue']}.json")
    with open(path, encoding='utf-8') as f:
        result = next(r for r in json.load(f) if r['race_id'] == race['race_id'])
    return {h['馬番']: h['_passes'][-1] for h in result['horses'] if h.get('_passes')}


def leaders(row):
    return {u for u, p in row['actual'].items() if p == 1}


def front2(row):
    return {u for u, p in row['actual'].items() if p <= 2}


def cover(rows, pick):
    """(逃げ馬を拾えた数, 初角2番手以内を拾えた数, その母数, 一覧の頭数合計)"""
    hit = f2 = f2n = size = 0
    for row in rows:
        chosen = set(pick(row))
        size += len(chosen)
        hit += bool(leaders(row) & chosen)
        f2 += len(front2(row) & chosen)
        f2n += len(front2(row))
    return hit, f2, f2n, size


def pct(a, b):
    return f'{a}/{b}（{a / b:.0%}）' if b else '-'


def report(rows):
    n = len(rows)
    first, last = rows[0]['date'], rows[-1]['date']
    out = [f'# AI展開予測（netkeiba・無料部分）のバックテスト（{first}〜{last}、中央{n}レース）', '',
           '`python backtest_ai_tenkai.py` の出力。「逃げ馬」は実際に初角を先頭で通った馬。'
           '「私たち」は card.pace_candidates（直近3走の重み付き初角位置）。'
           'AI は出馬表ページの「スタート後」の隊列（plain＝基本、jockey＝騎手傾向を考慮）。', '',
           '過去レースのページの予測は発走前と同じ（2026-11-25 に地方36鞍で、発走前と確定後が完全一致）。', '',
           '## 逃げ馬を一覧に拾えたか', '',
           '| 一覧の作り方 | 逃げ馬を拾えた | 初角2番手以内を拾えた | 平均頭数 |', '|---|---|---|---|']
    picks = []
    for k in (1, 2, 3, 5):
        picks.append((f'私たち 上位{k}頭', lambda r, k=k: r['ours'][:k]))
        picks.append((f'AI（基本） 上位{k}頭', lambda r, k=k: r['plain'][:k]))
        picks.append((f'AI（騎手傾向） 上位{k}頭', lambda r, k=k: r['jockey'][:k]))
    picks.append(('私たち 上位5頭 ＋ AI（基本）上位2頭', lambda r: set(r['ours'][:5]) | set(r['plain'][:2])))
    picks.append(('私たち 上位5頭 ＋ AI（騎手傾向）上位2頭', lambda r: set(r['ours'][:5]) | set(r['jockey'][:2])))
    picks.append(('私たち 上位4頭 ＋ AI（基本）上位1頭', lambda r: set(r['ours'][:4]) | set(r['plain'][:1])))
    for label, pick in picks:
        hit, f2, f2n, size = cover(rows, pick)
        out.append(f'| {label} | {pct(hit, n)} | {pct(f2, f2n)} | {size / n:.1f}頭 |')

    # 1番手の食い違い
    agree = [r for r in rows if r['ours'][:1] == r['plain'][:1]]
    differ = [r for r in rows if r['ours'][:1] != r['plain'][:1]]
    out += ['', '## 「誰がハナか」で私たちと AI（基本）が食い違ったとき', '',
            '| 場面 | レース数 | 私たちの1番手が逃げた | AI の1番手が逃げた |', '|---|---|---|---|']
    for label, part in (('一致', agree), ('食い違い', differ)):
        ours_hit = sum(bool(leaders(r) & set(r['ours'][:1])) for r in part)
        ai_hit = sum(bool(leaders(r) & set(r['plain'][:1])) for r in part)
        out.append(f'| {label} | {len(part)} | {pct(ours_hit, len(part))} | {pct(ai_hit, len(part))} |')

    # 前半・後半（合わせ込みの確認）
    out += ['', '## 期間を分けて（AI（基本）上位1頭・上位3頭が逃げ馬を拾えた割合）', '',
            '| 期間 | レース数 | 私たち 上位1頭 | AI 上位1頭 | 私たち 上位3頭 | AI 上位3頭 |', '|---|---|---|---|---|---|']
    for label, part in ((f'{bpc.HALF} より前', [r for r in rows if r['date'] < bpc.HALF]),
                        (f'{bpc.HALF} 以降', [r for r in rows if r['date'] >= bpc.HALF])):
        m = len(part)
        if not m:
            continue
        cells = [pct(cover(part, lambda r, k=k, key=key: r[key][:k])[0], m)
                 for k, key in ((1, 'ours'), (1, 'plain'), (3, 'ours'), (3, 'plain'))]
        out.append(f'| {label} | {m} | ' + ' | '.join(cells) + ' |')

    # 4角
    out += ['', '## 参考：4コーナーの先頭', '',
            'AI の「4コーナー」の前から3頭に、実際の最終コーナー先頭の馬がいた割合。', '',
            '| 予測 | 拾えた |', '|---|---|']
    for label, key in (('AI（基本）4角 上位3頭', 'plain_c4'), ('AI（騎手傾向）4角 上位3頭', 'jockey_c4'),
                       ('AI（基本）スタート後 上位3頭', 'plain'), ('私たち 上位3頭', 'ours')):
        hit = sum(bool({u for u, p in r['last'].items() if p == 1} & set(r[key][:3])) for r in rows)
        out.append(f'| {label} | {pct(hit, n)} |')
    return '\n'.join(out) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--md')
    args = parser.parse_args(argv)
    text = report(load())
    if args.md:
        with open(args.md, 'w', encoding='utf-8') as f:
            f.write(text)
    print(text)


if __name__ == '__main__':
    sys.exit(main())
