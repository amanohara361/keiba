#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逃げ候補一覧の作り方のバックテスト（2026-11-24）。

週次検証（2026-11-23週）の深掘り13レースのうち12件で、逃げ馬かペースの想定が外れていた。
朝の手順の「近5走のうち2走以上で先頭〜2番手」という数え方が、実際の逃げ馬を
どれだけ一覧から落としていたかを、カード（data/cards）の近走と実測（data_jra）の
初角通過順で数える。

比べるのは「実際に初角を先頭で通った馬が一覧に入っていたか」。一覧に入れても、
朝の予想がその中から本線を当てられるかは別の問題で、ここでは測らない。

使い方: python backtest_pace_candidates.py [--md 出力先]
"""
import argparse
import glob
import json
import os
import sys

import card

ROOT = os.path.dirname(os.path.abspath(__file__))
HALF = '2026-10-12'


def load_races():
    races = []
    for path in sorted(glob.glob(os.path.join(ROOT, 'data', 'cards', '2026-*.json'))):
        with open(path, encoding='utf-8') as f:
            day_card = json.load(f)
        ymd = day_card['date'].replace('-', '')
        for race in day_card['races']:
            if 'entries' not in race:
                continue
            jra = os.path.join(ROOT, 'data_jra', f"{ymd}_{race['venue']}.json")
            if not os.path.exists(jra):
                continue
            with open(jra, encoding='utf-8') as f:
                result = next((r for r in json.load(f) if r['race_id'] == race['race_id']), None)
            if not result:
                continue
            actual = {h['馬番']: h['_passes'][0] for h in result['horses'] if h.get('_passes')}
            if any(v == 1 for v in actual.values()):
                races.append((day_card['date'], race, actual))
    return races


def old_rule(entry):
    """これまでの朝の手順：近5走のうち2走以上で初角1〜2番手。"""
    hits = [card._first_corner(r.get('passing')) for r in (entry.get('recent') or [])[:5]]
    return sum(1 for p in hits if p and p <= 2) >= 2


def ext_flag(race, entry):
    return any('距離延長' in n for n in card.pace_notes(race, entry))


def blinker_flag(race, entry):
    return 'ブリンカー着用' in card.pace_notes(race, entry)


def measure(races, pick):
    n = lead_hit = top2 = top2_hit = size = 0
    for _, race, actual in races:
        entries = [e for e in race['entries'] if e.get('umaban') in actual]
        chosen = pick(race, entries)
        n += 1
        size += len(chosen)
        lead_hit += any(actual[e['umaban']] == 1 and e['umaban'] in chosen for e in entries)
        front = [e['umaban'] for e in entries if actual[e['umaban']] <= 2]
        top2 += len(front)
        top2_hit += sum(1 for u in front if u in chosen)
    return n, lead_hit, top2, top2_hit, size


def new_top(k):
    def pick(race, entries):
        found = card.pace_candidates(dict(race, entries=entries), top=k)
        return {r['umaban'] for r in found['top']}
    return pick


def flag_precision(races, flag):
    flagged = front = 0
    for _, race, actual in races:
        for e in race['entries']:
            if e.get('umaban') in actual and flag(race, e):
                flagged += 1
                front += actual[e['umaban']] <= 2
    return front, flagged


def report(races):
    old = lambda race, entries: {e['umaban'] for e in entries if old_rule(e)}
    rows = [('これまでの手順（近5走で2回以上1〜2番手）', old)]
    rows += [(f'新：直近3走の重み付き初角位置の上位{k}頭', new_top(k)) for k in (4, 5, 6)]
    rows.append(('これまでの手順＋1200以下からの距離延長',
                 lambda race, entries: old(race, entries) | {e['umaban'] for e in entries if ext_flag(race, e)}))
    rows.append(('これまでの手順＋ブリンカー着用',
                 lambda race, entries: old(race, entries) | {e['umaban'] for e in entries if blinker_flag(race, e)}))

    first, last = races[0][0], races[-1][0]
    out = [f'# 逃げ候補一覧の作り方のバックテスト（{first}〜{last}、中央{len(races)}レース）', '',
           '`python backtest_pace_candidates.py` の出力。カードの近走と data_jra の初角通過順で数えた。'
           '「逃げ馬」は実際に初角を先頭で通った馬（同着の先頭を含む）。根拠と決定は docs/決定ログ.md 2026-11-24。', '',
           '| 一覧の作り方 | 逃げ馬を拾えた | 初角2番手以内を拾えた | 一覧の平均頭数 | 前半 | 後半 |',
           '|---|---|---|---|---|---|']
    early = [r for r in races if r[0] < HALF]
    late = [r for r in races if r[0] >= HALF]
    for label, pick in rows:
        n, hit, t2, t2hit, size = measure(races, pick)
        e = measure(early, pick)
        l = measure(late, pick)
        out.append(f'| {label} | {hit}/{n}（{hit / n:.0%}） | {t2hit}/{t2}（{t2hit / t2:.0%}） | {size / n:.1f}頭 '
                   f'| {e[1] / e[0]:.0%} | {l[1] / l[0]:.0%} |')
    out += ['', f'前半は {HALF} より前（{len(early)}R）、後半はそれ以降（{len(late)}R）。重み 3:2:1 は合わせ込んでいない。', '',
            '## 注記にした材料の当たり具合', '',
            '拾った馬が実際に初角2番手以内だった割合。全馬の平均と変わらなければ、順位の材料にはしない。', '',
            '| 材料 | 初角2番手以内 |', '|---|---|']
    for label, flag in [('1200以下からの距離延長', ext_flag), ('ブリンカー着用', blinker_flag),
                        ('これまでの手順の該当馬', lambda race, e: old_rule(e)),
                        ('全馬（基準）', lambda race, e: True)]:
        front, flagged = flag_precision(races, flag)
        out.append(f'| {label} | {front}/{flagged}（{front / max(flagged, 1):.0%}） |')
    out += ['', '一覧に入っても、朝の予想がその中から本線を当てられるかは別。採用後の2週で週次検証が数える。']
    return '\n'.join(out) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--md')
    args = parser.parse_args(argv)
    text = report(load_races())
    if args.md:
        with open(args.md, 'w', encoding='utf-8') as f:
            f.write(text)
    print(text)


if __name__ == '__main__':
    sys.exit(main())
