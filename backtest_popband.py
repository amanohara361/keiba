#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""◎の人気帯で券種を切り替える案のバックテスト（2026-11-21）。

X のポスト（丞相@プロ馬券師、2026-11-21）の提案を、手元の◎で試す。
  - ◎が4〜6番人気 → 馬連3点（◎ → 1・2・3番人気）
  - ◎が7〜10番人気 → ワイド3点（◎ → 1・2・3番人気）
比べる相手は現行の固定形「◎単勝100円＋◎複勝100円」（予想メソッド第13章）。

判断材料の数字を出すだけで、買い目のロジックは変えない。
人気は確定オッズの順位（data/results の ninki）。実際に買うときは発走前の人気になるので、
境目の馬は帯が入れ替わりうる。

使い方: python backtest_popband.py [--detail]
"""
import glob
import json
import os
import sys

BANDS = [(1, 1), (2, 3), (4, 6), (7, 10), (11, 18)]
# (キー, 1レースあたりの投資額)
COLUMNS = [('tan', 100), ('fuku', 100), ('tanfuku', 200), ('umaren', 300), ('wide', 300)]
LABELS = {'tan': '◎単勝', 'fuku': '◎複勝', 'tanfuku': '◎単勝＋複勝',
          'umaren': '馬連3点', 'wide': 'ワイド3点'}


def _pay(payouts, bet_type, combo):
    for entry in payouts.get(bet_type, []):
        if sorted(entry['combination']) == sorted(combo):
            return entry['yen']
    return 0


def load_rows(bets_dir='data/bets', results_dir='data/results'):
    """中央で◎があり、確定払戻が揃っているレースを1行ずつ返す。"""
    rows = []
    for path in sorted(glob.glob(os.path.join(bets_dir, '*.json'))):
        with open(path, encoding='utf-8') as f:
            day = json.load(f)
        for race in day.get('races', []):
            if race.get('org') == 'nar':
                continue
            rpath = os.path.join(results_dir, f"{race['race_id']}.json")
            if not os.path.exists(rpath):
                continue
            honmei = [m['umaban'] for m in race.get('marks', []) if m.get('mark') == '◎']
            if not honmei:
                continue
            with open(rpath, encoding='utf-8') as f:
                res = json.load(f)
            h = honmei[0]
            order = [x for x in res.get('finishing_order', []) if x.get('ninki')]
            ninki = {x['umaban']: x['ninki'] for x in order}
            top = {x['ninki']: x['umaban'] for x in order}
            if h not in ninki or not all(k in top for k in (1, 2, 3)):
                continue
            p = res['payouts']
            # ◎自身が1〜3番人気のときは、その1点は自分自身との組になり買えない（払戻0のまま投資に数える）
            partners = [top[k] for k in (1, 2, 3)]
            tan = _pay(p, '単勝', [h])
            fuku = _pay(p, '複勝', [h])
            rows.append({
                'date': day['date'], 'race_id': race['race_id'], 'name': race.get('name'),
                'honmei': h, 'ninki': ninki[h],
                'fav_odds': next(x['odds'] for x in order if x['ninki'] == 1),
                'tan': tan, 'fuku': fuku, 'tanfuku': tan + fuku,
                'umaren': sum(_pay(p, '馬連', [h, o]) for o in partners if o != h),
                'wide': sum(_pay(p, 'ワイド', [h, o]) for o in partners if o != h),
            })
    return rows


def summarize(rows):
    out = {}
    n = len(rows)
    for key, cost in COLUMNS:
        values = sorted((r[key] for r in rows), reverse=True)
        total = sum(values)
        out[key] = {
            'n': n,
            'hits': sum(1 for v in values if v > 0),
            'roi': total / (cost * n) if n else None,
            'roi_ex_max': sum(values[1:]) / (cost * (n - 1)) if n > 1 else None,
        }
    return out


def _fmt(stat):
    if stat['roi'] is None:
        return '-'
    ex = f"{stat['roi_ex_max']:.0%}" if stat['roi_ex_max'] is not None else '-'
    return f"{stat['roi']:.0%}（{stat['hits']}的中・最大除く{ex}）"


def main(argv):
    rows = load_rows()
    if not rows:
        print('対象レースがありません')
        return 1
    print(f"中央・◎あり・確定払戻あり {len(rows)}レース（{rows[0]['date']}〜{rows[-1]['date']}）\n")
    header = '| ◎の人気 | レース | ' + ' | '.join(LABELS[k] for k, _ in COLUMNS) + ' |'
    print(header)
    print('|' + '---|' * (len(COLUMNS) + 2))
    groups = [('全体', rows)] + [
        (f'{lo}番人気' if lo == hi else f'{lo}〜{hi}番人気',
         [r for r in rows if lo <= r['ninki'] <= hi]) for lo, hi in BANDS]
    groups.append(('4〜6番人気（1番人気1.4倍以下を除く）',
                   [r for r in rows if 4 <= r['ninki'] <= 6 and r['fav_odds'] > 1.4]))
    for label, rs in groups:
        if not rs:
            print(f'| {label} | 0 |' + ' - |' * len(COLUMNS))
            continue
        s = summarize(rs)
        print(f'| {label} | {len(rs)} | ' + ' | '.join(_fmt(s[k]) for k, _ in COLUMNS) + ' |')
    if '--detail' in argv:
        print('\n◎4〜10番人気の馬連・ワイド的中')
        for r in rows:
            if 4 <= r['ninki'] <= 10 and (r['umaren'] or r['wide']):
                print(f"  {r['date']} {r['name']} ◎{r['ninki']}番人気 馬連{r['umaren']} "
                      f"ワイド{r['wide']} 単勝{r['tan']} 複勝{r['fuku']}")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
