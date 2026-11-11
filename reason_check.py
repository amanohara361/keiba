#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""勝率を動かした理由の区分（`win_reasons`）の点検と、区分別の集計（2026-11-11 ユーザー承認）。

朝の予想は `win_probabilities` に馬番ごとの主観勝率を書くが、市場より上げた理由は
`note` の地の文にしか無く、「どの種類の理由が市場に勝っているか」を機械で測れなかった。
そこで理由を `win_reasons`（馬番・区分・理由）に分けて書く（`unmarked` と同じ作り）。

1. 点検：`missing_reasons(race)` … 勝率を書いたのに理由の行が無い馬番。
   `make_bets.py --show` が呼んで知らせるだけ（終了コードは変えない）。
2. 集計：`analyze` / `render_lines` … 確定結果のあるレースについて、`win_reasons` の
   **先頭の区分**ごとに、中央・地方別で頭数・勝ち・市場の見込み勝利数・回収率などを出す。
   週次レビューの観察節（review.py）。判定はしない。外部通信もしない
   （保存済みの確定結果 `review.cached_result` だけを読む）。

外部ライブラリは使わない。
"""

import math
from datetime import date

import bets

SINCE = date(2026, 11, 11)
MIN_HORSES = 30          # これ未満の区分は判定しない
NO_CATEGORY = '(区分なし)'
FIRST_DATA_DAY = '2026-11-14'

ORG_LABELS = (('jra', '中央'), ('nar', '地方'))


# ----------------------------------------------------------------------
# 点検
# ----------------------------------------------------------------------

def missing_reasons(race):
    """win_probabilities に書いたのに win_reasons に行が無い馬番（昇順）。"""
    covered = {w['umaban'] for w in race.win_reasons}
    return sorted(n for n in (race.win_probabilities or {}) if n not in covered)


def check_race(race):
    """[(馬番 or None, 知らせる文), ...]。知らせるだけで、問題の数には数えない。"""
    if not race.win_probabilities:
        return []
    if not race.win_reasons:
        return [(None, 'win_probabilities があるのに win_reasons が空です'
                       '（docs/朝タスク手順.md「勝率を動かした理由を書く」）')]
    return [(n, f'{n}番の勝率に理由の区分がありません') for n in missing_reasons(race)]


# ----------------------------------------------------------------------
# 集計
# ----------------------------------------------------------------------

def final_odds_and_ranks(result):
    """確定結果から ({馬番: 確定オッズ}, {馬番: 着順})。取れなければ (None, None)。

    競走中止の馬も確定オッズに含める（発走の時点では走る馬。edge_check.final_odds_and_winner と同じ）。
    """
    order = (result or {}).get('finishing_order') or []
    odds, ranks = {}, {}
    for h in order:
        if h.get('umaban') is None:
            continue
        n = int(h['umaban'])
        if h.get('odds'):
            odds[n] = float(h['odds'])
        if isinstance(h.get('rank'), int):
            ranks[n] = h['rank']
    for h in (result or {}).get('non_finishers') or []:
        if h.get('status') == '中止' and h.get('umaban') and h.get('odds'):
            odds.setdefault(int(h['umaban']), float(h['odds']))
    if not odds or 1 not in ranks.values():
        return None, None
    return odds, ranks


def collect(days, cached_result):
    """対象日の (日付, RaceBets, 確定結果) を返す。結果が無いレース・win_probabilities の無いレースは除く。"""
    out = []
    for day in days:
        if day < SINCE:
            continue
        sheet = bets.load_sheet(day)
        if not sheet:
            continue
        for race in sheet.races:
            if not race.win_probabilities:
                continue
            result = cached_result(race.race_id)
            if result:
                out.append((day, race, result))
    return out


def _blank():
    return {'n': 0, 'wins': 0, 'expected': 0.0, 'var': 0.0, 'payout': 0.0, 'place': 0}


def _add(cell, p_market, odds, rank):
    cell['n'] += 1
    cell['expected'] += p_market
    cell['var'] += p_market * (1 - p_market)
    if rank == 1:
        cell['wins'] += 1
        cell['payout'] += odds * 100
    if rank is not None and rank <= 3:
        cell['place'] += 1


def finish(cell):
    """集計セルに倍率・z・回収率を足して返す。"""
    n, exp = cell['n'], cell['expected']
    return {
        **cell,
        'ratio': cell['wins'] / exp if exp > 0 else None,
        'z': (cell['wins'] - exp) / math.sqrt(cell['var']) if cell['var'] > 0 else None,
        'roi': cell['payout'] / (n * 100) if n else None,
    }


def analyze(dated_races):
    """dated_races: [(日付, RaceBets, 確定結果)]。

    戻り値: {'by_org': {org: {'cats': {区分: セル}, 'up': セル, 'down': セル, 'all': セル}},
             'horses': 区分のある馬の頭数, 'races': 対象レース数}
    区分は win_reasons の先頭の行。行が無い馬は NO_CATEGORY。
    市場の勝率は確定単勝オッズの 1/オッズ を全頭で合計1に割り戻した値。
    """
    by_org = {}
    horses = races = 0
    for _day, race, result in dated_races:
        odds, ranks = final_odds_and_ranks(result)
        if odds is None or not race.win_probabilities:
            continue
        inv = {n: 1.0 / o for n, o in odds.items() if o > 0}
        total = sum(inv.values())
        if total <= 0:
            continue
        first = {}
        for w in race.win_reasons:
            first.setdefault(w['umaban'], w['category'])
        used = False
        for n, p in race.win_probabilities.items():
            if n not in inv:
                continue
            used = True
            pm = inv[n] / total
            rank = ranks.get(n)
            block = by_org.setdefault(race.org, {'cats': {}, 'up': _blank(),
                                                 'down': _blank(), 'all': _blank()})
            cat = first.get(n, NO_CATEGORY)
            _add(block['cats'].setdefault(cat, _blank()), pm, odds[n], rank)
            _add(block['all'], pm, odds[n], rank)
            if p > pm:
                _add(block['up'], pm, odds[n], rank)
            elif p < pm:
                _add(block['down'], pm, odds[n], rank)
            if n in first:
                horses += 1
        races += used
    return {'by_org': by_org, 'horses': horses, 'races': races}


# ----------------------------------------------------------------------
# 出力
# ----------------------------------------------------------------------

def _row(label, cell):
    c = finish(cell)
    ratio = f"{c['ratio']:.2f}" if c['ratio'] is not None else '—'
    z = f"{c['z']:+.2f}" if c['z'] is not None else '—'
    roi = f"{c['roi']:.0%}" if c['roi'] is not None else '—'
    note = '' if c['n'] >= MIN_HORSES else f'（{MIN_HORSES}頭未満・判定しない）'
    return (f"| {label} | {c['n']} | {c['wins']} | {c['expected']:.1f} | {ratio} | {z} | "
            f"{roi} | {c['place']} |{note}")


def render_lines(analysis):
    """週次レビューの詳細ファイルに載せる Markdown の行。判定はしない。"""
    lines = [
        '勝率に理由を付けた馬を、`win_reasons` の先頭の区分ごとに、確定結果と突き合わせる。'
        '見込み勝利数は確定単勝オッズを全頭で正規化した市場の勝率の合計。'
        f'**{MIN_HORSES}頭未満の区分は判定しない。規則にはしない。**',
        '',
    ]
    if not analysis or not analysis['by_org'] or not analysis['horses']:
        return lines + [f'まだ記録がありません（{FIRST_DATA_DAY} の朝の予想から）', '']
    head = ['| 区分 | 頭数 | 勝ち | 見込み勝利数 | 倍率 | z | 単勝回収率 | 3着内 |',
            '|---|---|---|---|---|---|---|---|']
    for org, label in ORG_LABELS:
        block = analysis['by_org'].get(org)
        if not block:
            continue
        lines += [f'### {label}', ''] + head
        order = [c for c in bets.WIN_REASON_CATEGORIES if c in block['cats']]
        order += [c for c in block['cats'] if c not in order]
        for cat in order:
            lines.append(_row(cat, block['cats'][cat]))
        lines.append(_row('**全体**', block['all']))
        lines.append(_row('市場より上げた馬', block['up']))
        lines.append(_row('市場より下げた馬', block['down']))
        lines.append('')
    return lines
