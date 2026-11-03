#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""券種の解禁条件：その券種に要る見立てが、人気（市場）に勝てているか（2026-11-03〜）。

組み合わせ馬券の期待値は、各馬の見立ての優位を掛け合わせたものになる。◎の見立てに
優位があっても、相手の見立てが人気なり以下なら、組むほど悪くなる（従来の bet_builder で
3連複・馬連・partners を含む買い目が0%だった理由。決定ログ 2026-11-03「券種の使い分け」）。

そこで券種ごとに「当てるのに要る見立て」を決め、印ごとの実績を人気なりの見込みと比べる。

  | 券種 | 要る見立て |
  |---|---|
  | 単勝 | ◎の勝ち |
  | 複勝 | ◎の3着内 |
  | ワイド | ◎と○の3着内 |
  | 馬連 | ◎と○の連対 |
  | 3連複 | ◎・○・▲の3着内 |
  | 穴を相手に足す | partners の3着内 |

見込みの出し方
- 勝ち：確定単勝オッズの 1/オッズ をレース内で合計1に割り戻した値（市場の勝率）。
  オッズが欠けたレースは、連対・3着内と同じ人気なりの率を使う（地方の確定結果は 2026-11-03 から
  月次のオッズファイルで確定単勝オッズを足しているので、普段は起きない。取れなかったときの予備）。
- 連対・3着内：同じ組織（中央・地方）の対象レースで、同じ人気の馬が実際に連対・3着内に
  入った割合（人気なりの率）。市場の連対・3着内の確率は単勝オッズからは直接出ないため。
  Harville の式で出すと人気馬に寄った偏りが入る（調査メモ 2026-11-03 案F）ので使わない。

**判定はしない。** 目安に届いたかだけを出す。届いたら、後の期間で数え直してから承認にかける
（決定ログ 2026-10-16 のルール）。目安：全条件で z≥2（偶然では出にくい差）かつ、期間を前半・後半に
分けてどちらも見込み超え（1回の上振れでないこと）。
"""

import math

# 印の区分。partners（中穴候補）は「穴」とする。
GROUPS = ['◎', '○', '▲', '△', '穴']
METRICS = [('win', '勝ち', 1), ('quin', '連対', 2), ('place', '3着内', 3)]

# (券種, [(印, 指標)])
GATES = [
    ('単勝', [('◎', 'win')]),
    ('複勝', [('◎', 'place')]),
    ('ワイド', [('◎', 'place'), ('○', 'place')]),
    ('馬連', [('◎', 'quin'), ('○', 'quin')]),
    ('3連複', [('◎', 'place'), ('○', 'place'), ('▲', 'place')]),
    ('穴を相手に足す', [('穴', 'place')]),
]

# すでに買っている券種（決定ログ 2026-11-03：中央は◎単勝＋◎複勝の固定形）。
# 目安に届いてもメールでは知らせない（毎週同じ通知が出続けるのを避ける。同日のカウンタの決定と同じ考え方）。
# 表では「使用中」と出し、届かなくなったら固定形を見直す材料にする。
IN_USE = {('jra', '単勝'), ('jra', '複勝')}

Z_READY = 2.0
MIN_HORSES = 30   # これ未満の頭数では目安を判定しない


def horse_rows(race, result):
    """1レースの全馬を (印の区分, 人気, 着順, 単勝オッズ) で返す。比べられないレースは None。

    人気・着順のどれかが欠けた馬がいるレースは、人気なりの見込みが崩れるので丸ごと外す。
    単勝オッズは欠けてもよい（そのレースの勝ちの見込みは人気なりの率で出す）。
    """
    order = (result or {}).get('finishing_order') or []
    if not race.marks or not order:
        return None
    if any(not isinstance(h.get('ninki'), int) or not isinstance(h.get('rank'), int)
           for h in order):
        return None
    partners = {p.get('umaban') for p in race.partners}
    rows = []
    for h in order:
        group = race.mark_of(h['umaban']) or ('穴' if h['umaban'] in partners else '')
        rows.append({'group': group, 'ninki': h['ninki'], 'rank': h['rank'],
                     'odds': float(h['odds']) if h.get('odds') else None})
    return rows


def _popularity_rates(races):
    """組織ごと・人気ごとの勝率・連対率・3着内率。races は [(org, rows)]。"""
    counts = {}
    for org, rows in races:
        for r in rows:
            c = counts.setdefault((org, r['ninki']), {'n': 0, 'win': 0, 'quin': 0, 'place': 0})
            c['n'] += 1
            c['win'] += r['rank'] == 1
            c['quin'] += r['rank'] <= 2
            c['place'] += r['rank'] <= 3
    return {k: {m: c[m] / c['n'] for m in ('win', 'quin', 'place')}
            for k, c in counts.items()}


def _tally(races, rates):
    """印ごと・指標ごとに、実数・見込み・分散を足し上げる。"""
    out = {g: {m: {'n': 0, 'actual': 0, 'expected': 0.0, 'var': 0.0} for m, *_ in METRICS}
           for g in GROUPS}
    for org, rows in races:
        priced = all(r['odds'] for r in rows)
        inv = sum(1.0 / r['odds'] for r in rows) if priced else None
        for r in rows:
            if r['group'] not in out:
                continue
            for metric, _, top in METRICS:
                if metric == 'win' and priced:
                    p = (1.0 / r['odds']) / inv
                else:
                    p = rates.get((org, r['ninki']), {}).get(metric, 0.0)
                c = out[r['group']][metric]
                c['n'] += 1
                c['actual'] += r['rank'] <= top
                c['expected'] += p
                c['var'] += p * (1 - p)
    return out


def _stat(c):
    ratio = c['actual'] / c['expected'] if c['expected'] else None
    z = (c['actual'] - c['expected']) / math.sqrt(c['var']) if c['var'] else None
    return {**c, 'ratio': ratio, 'z': z}


def analyze(dated_races, week_start=None):
    """dated_races は [(日付, org, rows)]。組織ごとに通算・前半・後半・今週を集計する。

    人気なりの率は、その組織の通算の全馬から出す（今週や前半だけで出すと頭数が足りない）。
    """
    out = {}
    for org in sorted({o for _, o, _ in dated_races}):
        mine = sorted([(d, rows) for d, o, rows in dated_races if o == org], key=lambda x: x[0])
        if not mine:
            continue
        rates = _popularity_rates([(org, rows) for _, rows in mine])
        half = len(mine) // 2
        periods = {
            'total': mine,
            'first': mine[:half],
            'second': mine[half:],
            'week': [x for x in mine if week_start and x[0] >= week_start],
        }
        res = {'races': len(mine),
               'split': mine[half][0] if half < len(mine) else None}
        for key, rows in periods.items():
            tally = _tally([(org, r) for _, r in rows], rates)
            res[key] = {g: {m: _stat(c) for m, c in ms.items()} for g, ms in tally.items()}
        res['gates'] = [dict(gate_status(res, name, conds), in_use=(org, name) in IN_USE)
                        for name, conds in GATES]
        out[org] = res
    return out


def gate_status(res, name, conds):
    """券種1つの目安。全条件で z≥Z_READY かつ前半・後半とも見込み超えなら ready。"""
    missing = []
    for group, metric in conds:
        t = res['total'][group][metric]
        f = res['first'][group][metric]
        s = res['second'][group][metric]
        label = f'{group}の{dict((m, l) for m, l, _ in METRICS)[metric]}'
        if t['n'] < MIN_HORSES:
            missing.append(f'{label}（{t["n"]}頭で判定不可）')
        elif t['z'] is None or t['z'] < Z_READY:
            missing.append(f'{label}（z={t["z"]:+.1f}）' if t['z'] is not None else label)
        elif not ((f['ratio'] or 0) > 1 and (s['ratio'] or 0) > 1):
            missing.append(f'{label}（前半・後半の向きが揃わない）')
    return {'name': name, 'conds': conds, 'ready': not missing, 'missing': missing}


ORG_LABEL = {'jra': '中央', 'nar': '地方'}


def _cell(c):
    if not c['n']:
        return '—'
    ratio = f"{c['ratio']:.2f}" if c['ratio'] is not None else '—'
    z = f"{c['z']:+.1f}" if c['z'] is not None else '—'
    return f"{c['actual']}/{c['expected']:.1f}（{ratio}倍、z{z}）"


def render_lines(analysis):
    """週次レビュー .md の節。"""
    if not analysis:
        return []
    lines = [
        '## 券種の解禁条件：要る見立てが人気に勝てているか（2026-11-03〜）',
        '',
        '組み合わせ馬券は各馬の見立ての優位を掛け合わせる。相手の見立てが人気なり以下なら、組むほど悪くなる。'
        '券種ごとに要る見立てを決め、印の実績を見込みと比べる（決定ログ 2026-11-03「券種の使い分け」）。'
        '見込みは、勝ちが確定単勝オッズから出した市場の勝率（オッズが取れなかったレースは人気なりの率）、'
        '連対・3着内が同じ組織・同じ人気の馬の実績率。'
        '「実数/見込み（倍率、z）」。z は偶然の振れ幅の何倍ずれたか（2以上で偶然では出にくい）。',
        '',
        f'目安：全条件で z≥{Z_READY:.0f}、かつ前半・後半とも見込み超え（{MIN_HORSES}頭未満は判定しない）。'
        '届いたら、後の期間で数え直してから承認にかける。判定はしない。',
        '',
    ]
    for org, res in analysis.items():
        label = ORG_LABEL.get(org, org)
        split = f"、前半・後半の境 {res['split'].isoformat()}" if res.get('split') else ''
        lines += [f"### {label}（{res['races']}レース{split}）", '',
                  '| 券種 | 目安 | 届いていない条件 |', '|---|---|---|']
        for g in res['gates']:
            mark = '**届いた**' if g['ready'] else '未達'
            name = g['name'] + ('（使用中）' if g.get('in_use') else '')
            lines.append(f"| {name} | {mark} | {'、'.join(g['missing']) or '—'} |")
        lines += ['', '| 印 | 指標 | 今週 | 通算 | 前半 | 後半 |', '|---|---|---|---|---|---|']
        for group in GROUPS:
            for metric, mlabel, _ in METRICS:
                cells = [_cell(res[k][group][metric]) for k in ('week', 'total', 'first', 'second')]
                lines.append(f'| {group} | {mlabel} | ' + ' | '.join(cells) + ' |')
        lines.append('')
    return lines


def mail_line(analysis):
    """まだ使っていない券種が目安に届いたときだけ1行。無ければ None（毎週の通知にしない）。"""
    ready = [f"{ORG_LABEL.get(org, org)}の{g['name']}"
             for org, res in (analysis or {}).items() for g in res['gates']
             if g['ready'] and not g.get('in_use')]
    if not ready:
        return None
    return ('券種の解禁条件の目安に届いた：' + '、'.join(ready)
            + '（後の期間で数え直してから承認にかける。詳細は週次レビューの .md）')
