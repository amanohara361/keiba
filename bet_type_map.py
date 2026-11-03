#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""券種×人気帯の「何も考えずに買った場合の回収率」（2026-11-03〜）。

予想を一切使わず、ある人気帯の馬（組）を全部100円ずつ買ったら何%戻るかを、
data_jra/ の全レースの確定払戻から出す。市場がどの券種・どの人気帯を
買いすぎているか（＝配当が割安か）の地図で、予想の上手下手とは関係しない。

使い道（決定ログ 2026-11-03「券種の使い分け」段階3）：
◎が同じ見立てでも、どの券種で買うと控除と市場の偏りで削られにくいかを選ぶ材料にする。
例えば単勝では人気薄ほど回収率が下がる（本命・大穴バイアス。2026-11-03 に921レースで
1〜2番人気83〜84%、10番人気以下67%）。同じ偏りが複勝・ワイド・3連複でどう出るかを見る。

**判定はしない、数字だけ。** 高配当の数回で回収率が大きく動くので、最大払戻1回を除いた値も並べる。

注意：
- data_jra/ の horses は完走馬だけ（中止・除外・取消は入らない）。中止馬を含む組も実際には
  売られているので、買い目の数（＝賭け金）がわずかに少なく出る。
- 人気は確定時点のもの。発走前に分かっていた人気とは違うことがある。
- 払戻の無いレース（2026-11-03 以前で取り直していない分）は数えない。
"""

import glob
import itertools
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data_jra')

# 単勝・複勝の人気帯
SINGLE_BANDS = [('1番人気', 1, 1), ('2番人気', 2, 2), ('3番人気', 3, 3),
                ('4〜5番人気', 4, 5), ('6〜9番人気', 6, 9), ('10番人気以下', 10, 99)]
# 組の馬券の人気帯（A=1〜3番人気、B=4〜6番人気、C=7番人気以下）
GROUP_BANDS = [('A', 1, 3), ('B', 4, 6), ('C', 7, 99)]
BAND_NOTE = 'A=1〜3番人気、B=4〜6番人気、C=7番人気以下'

PAIR_TYPES = ['ワイド', '馬連', '馬単']
TRIO_TYPES = ['3連複', '3連単']
ORDERED = {'馬単', '3連単'}      # 着順まで当てる券種（組の数え方が並び順込み）
ORDERED_KEY = {'馬単'}           # 人気帯も着順どおりに分ける券種（3連単は27通りになり読めないので並びを問わない）


def _band(ninki, bands):
    return next(name for name, low, high in bands if low <= ninki <= high)


def load_races(data_dir=None):
    """払戻があり、全馬の人気がそろったレースだけを返す。各馬は {馬番: 人気}。"""
    out = []
    for path in sorted(glob.glob(os.path.join(data_dir or DATA, '*.json'))):
        with open(path, encoding='utf-8') as f:
            for rc in json.load(f):
                pay = rc.get('払戻')
                if not pay:
                    continue
                try:
                    pop = {int(h['馬番']): int(h['人気']) for h in rc['horses']}
                except (KeyError, TypeError, ValueError):
                    continue
                if len(pop) < 3:
                    continue
                out.append({'race_id': rc['race_id'], 'date': rc.get('日付', ''),
                            'popularity': pop, 'payouts': pay})
    return out


def _new():
    return {'bets': 0, 'hits': 0, 'returned': 0, 'max': 0}


def _add_hit(cell, yen):
    cell['hits'] += 1
    cell['returned'] += yen
    cell['max'] = max(cell['max'], yen)


def _key(nums, pop, ordered):
    bands = [_band(pop[n], GROUP_BANDS) for n in nums]
    return '-'.join(bands if ordered else sorted(bands))


def tally(races):
    """{券種: {人気帯の組: {bets, hits, returned, max}}}。"""
    out = {t: {} for t in ['単勝', '複勝'] + PAIR_TYPES + TRIO_TYPES}
    for rc in races:
        pop, pay = rc['popularity'], rc['payouts']
        horses = sorted(pop)
        for t in ('単勝', '複勝'):
            if t not in pay:
                continue
            for n in horses:
                out[t].setdefault(_band(pop[n], SINGLE_BANDS), _new())['bets'] += 1
            for e in pay[t]:
                n = e['combination'][0]
                if n in pop:
                    _add_hit(out[t].setdefault(_band(pop[n], SINGLE_BANDS), _new()), e['yen'])
        for t, size in [(t, 2) for t in PAIR_TYPES] + [(t, 3) for t in TRIO_TYPES]:
            if t not in pay:
                continue
            ordered = t in ORDERED
            combos = (itertools.permutations(horses, size) if ordered
                      else itertools.combinations(horses, size))
            for c in combos:
                out[t].setdefault(_key(c, pop, t in ORDERED_KEY), _new())['bets'] += 1
            for e in pay[t]:
                c = e['combination']
                if len(c) == size and all(n in pop for n in c):
                    _add_hit(out[t].setdefault(_key(c, pop, t in ORDERED_KEY), _new()), e['yen'])
    return out


def _roi(cell, drop_max=False):
    staked = cell['bets'] * 100
    if not staked:
        return '—'
    returned = cell['returned'] - (cell['max'] if drop_max else 0)
    return f'{returned / staked * 100:.0f}%'


def _order(t, keys):
    if t in ('単勝', '複勝'):
        names = [b[0] for b in SINGLE_BANDS]
        return [k for k in names if k in keys]
    return sorted(keys)


def render_lines(table, races):
    if not races:
        return []
    dates = sorted(r['date'] for r in races if r['date'])
    span = f'{dates[0]}〜{dates[-1]}' if dates else ''
    lines = [
        '## 券種×人気帯：何も考えずに買った場合の回収率（中央・2026-11-03〜）',
        '',
        f'`bet_type_map.py` の出力。data_jra の {len(races)}レース（{span}）。予想は使わず、その人気帯の馬・組を全部100円ずつ買った場合。'
        '市場がどこを買いすぎているか（配当が割安か）の地図で、控除後の水準は単複80%・ワイド馬連77.5%・馬単3連複75%・3連単72.5%。'
        f'組の馬券の人気帯は {BAND_NOTE}（馬単は1着-2着の順、ほかは並びを問わない）。'
        '判定はしない。決定ログ 2026-11-03「券種の使い分け」。',
        '',
        '| 券種 | 人気帯 | 点数 | 的中 | 回収率 | 最大払戻1回を除く |',
        '|---|---|---|---|---|---|',
    ]
    for t, cells in table.items():
        for k in _order(t, cells):
            c = cells[k]
            lines.append(f"| {t} | {k} | {c['bets']:,} | {c['hits']} | {_roi(c)} | {_roi(c, True)} |")
    lines.append('')
    return lines


def report(data_dir=None):
    races = load_races(data_dir)
    return render_lines(tally(races), races)


def main():
    lines = report()
    print('\n'.join(lines) if lines else 'data_jra に払戻のあるレースがまだありません'
          '（payouts_backfill.yml を回すか、bias.yml の蓄積を待つ）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
