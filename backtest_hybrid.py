#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ハイブリッド馬券術（予想メソッド 第7章）のバックテスト（2026-09-27）。

## 問い

第7章は「券種を1つに絞らず、単勝・馬連・3連複などに分散して網を張る」と
書いているが、2026-08-27以降に購入した61レースのうち54レースは1券種だけで、
2券種の7レースも `bet_builder._axis_hedge` が足した保険ワイドだけだった。
理由は2つ。

  (1) `bet_builder.build_bets` は券種ごとに候補を作り、**1つだけ**選ぶ
      （`max(ok, key=(hit_rate, ev))`）。
  (2) 第13章の合成オッズ3.0倍（合成 = 1/Σ(1/オッズ)）と期待値
      （合成 × 的中率）1.2 の規律は、券種を混ぜると合成オッズが下がるので
      ハイブリッドを構造的に落とす（単勝4＋ワイド6＋3連複30 → 2.22倍）。

**実際のレースでハイブリッドにしていたら成績は良かったのか**を、保存済みの
データだけで測る。

## 何をするか（数字を出すだけ）

**基準もロジックも変えない。** `予想メソッド.md`・`bet_builder.py`・
`discipline.py` には触らない。bet_builder の関数を呼んで数字を出すだけ。

- 素材は `data/checks/<日付>.json` の `priced_odds`（bet_builder がその回に
  値付けした全組み合わせのオッズ）と `win_odds`（出走全頭の単勝オッズ）。
  どちらも 2026-09-16（PR #57）以降の記録にしかない。レースごとに、
  **両方が空でない最後の検算**を採る（発走後の検算は両方とも空になる）。
- 印・相手・主観勝率は `data/bets/<日付>.json`、着順と払戻は
  `data/results/<race_id>.json`（保存済みのものだけ。ネットワークに出ない）。
- ハイブリッドの組は `priced_odds` にある◎絡みの組だけから作る
  （bet_builder は◎軸の組しか値付けしないので、それが使える全集合）。

定義の詳細はレポートの「2 定義」に書き出す（`render_definitions`）。

使い方:
    python3 backtest_hybrid.py
    python3 backtest_hybrid.py --since 2026-09-16 --until 2026-10-31
    python3 backtest_hybrid.py --out data/review/hybrid_backtest_2026-10-31.md
"""

import argparse
import json
import os
import sys
from datetime import date
from types import SimpleNamespace

import bet_builder
import bets
import discipline
import results as results_module
import review

# priced_odds / win_odds が data/checks に記録されるようになった日（PR #57）。
PRICED_ODDS_FROM = date(2026, 9, 16)

# SET_HIT_RATE_CAP（1.5倍）が本番に入った日（PR #61、2026-09-18 08:16 JST）。
# これより前の検算は上限なしで組まれている（再現チェックの理由分類に使う）。
CAP_ADOPTED = date(2026, 9, 18)

DEFAULT_OUTPUT = os.path.join('data', 'review', 'hybrid_backtest_2026-09-27.md')

STAKE = 100

# 部品（1点）の足切り：上限(CAP)後の的中率で見た1点の期待値がこれ未満の組は
# ハイブリッドの部品にしない。既定は0（足切りなし）＝期待値の判定はセット単位の
# 採否ルール（R1〜R3）に任せる。1.0 にすると「自分のモデルでも損と見ている組は
# 網に入れない」版になる（レポートの感度分析で両方を出す）。
LEG_MIN_EV = 0.0
SENSITIVITY_LEG_MIN_EV = 1.0

# R2/R3 の採否に使う「正確な期待値」の下限。第13章の期待値1.2と同じ値。
EXACT_MIN_EV = discipline.MIN_EXPECTED_VALUE

# R3：◎の単勝オッズがこれ以下のときだけハイブリッドにする。
# 市場推定勝率でおおむね16%以上（控除後0.8/5.0）＝上位人気の◎に相当する。
# 第8章の推奨レンジ（4.0〜9.9倍）の下側で、「◎が強いときだけ網を張る」
# という第7章の前提に合わせた。**結果を見る前に決めた値で、調整していない。**
R3_AXIS_ODDS_MAX = 5.0

SHAPES = {
    'H1': ('単勝', 'ワイド', '3連複'),
    'H2': ('単勝', 'ワイド'),
    'H3': ('ワイド', '3連複'),
}
SHAPE_LABELS = {
    'H1': '◎単勝＋◎ワイド1点＋◎3連複1点',
    'H2': '◎単勝＋◎ワイド1点',
    'H3': '◎ワイド1点＋◎3連複1点',
}
RULES = ('R0', 'R1', 'R2', 'R3')
RULE_LABELS = {
    'R0': '無条件（組めたら必ず買う・参考）',
    'R1': '現行規律（合成3.0倍・合成×合算的中率1.2）',
    'R2': '正確な期待値1.2以上＋トリガミ無し',
    'R3': f'R2＋◎単勝{R3_AXIS_ODDS_MAX:.1f}倍以下',
}


# ----------------------------------------------------------------------
# 入力（保存済みファイルだけを読む）
# ----------------------------------------------------------------------

def last_priced_records(day):
    """その日の検算記録から、priced_odds と win_odds が両方ある最後の記録を
    {race_id: (checked_at, 記録)} で返す。"""
    path = os.path.join(bets.CHECKS_DIR, f'{day.isoformat()}.json')
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        history = json.load(f)
    out = {}
    for entry in history:
        for race in entry.get('races', []):
            if race.get('priced_odds') and race.get('win_odds'):
                out[str(race.get('race_id'))] = (entry.get('checked_at'), race)
    return out


def check_days(since, until):
    days = []
    if not os.path.isdir(bets.CHECKS_DIR):
        return days
    for name in sorted(os.listdir(bets.CHECKS_DIR)):
        stem, ext = os.path.splitext(name)
        if ext != '.json':
            continue
        try:
            day = date.fromisoformat(stem)
        except ValueError:
            continue
        if since <= day <= until:
            days.append(day)
    return days


def priced_lookup(priced_odds):
    """calibration_check.priced_lookup と同じ（当時と同じ候補集合になる）。"""
    def lookup(bet_type, horses):
        key = '-'.join(str(h) for h in sorted(horses))
        return (priced_odds.get(bet_type) or {}).get(key)
    return lookup


def load_races(since, until):
    """対象レースを [{'day','race','record','checked_at'}] で返す。
    買い目ファイルに無い race_id は skipped に入れる。"""
    rows, skipped = [], []
    for day in check_days(max(since, PRICED_ODDS_FROM), until):
        records = last_priced_records(day)
        if not records:
            continue
        sheet = bets.load_sheet(day)
        by_id = {r.race_id: r for r in sheet.races} if sheet else {}
        for race_id, (checked_at, record) in records.items():
            race = by_id.get(race_id)
            if race is None:
                skipped.append((day, race_id, '買い目ファイルに無い'))
                continue
            rows.append({'day': day, 'race': race, 'record': record,
                         'checked_at': checked_at})
    rows.sort(key=lambda r: (r['day'], r['race'].start_time or '', r['race'].race_id))
    return rows, skipped


# ----------------------------------------------------------------------
# 確率（bet_builder の関数をそのまま使う）
# ----------------------------------------------------------------------

def leg_rate(p, bet_type, horses):
    """1点の的中率（Harville）。"""
    if bet_type == '単勝':
        return p[horses[0]]
    if bet_type == '馬連':
        return bet_builder.p_quinella(p, tuple(horses))
    if bet_type == 'ワイド':
        return bet_builder.p_wide(p, tuple(horses))
    if bet_type == '3連複':
        return bet_builder.p_trio(p, tuple(horses))
    raise ValueError(f'対応していない券種です: {bet_type}')


def union_rate(p, legs):
    """どれか1点でも的中する確率。券種が混ざっても重なりを二重に数えない
    （bet_builder._union_hit_rate・calibration_check.set_hit_rate と同じ方法）。"""
    total = 0.0
    for top3, prob in bet_builder._harville_top3(p):
        if any(bet_builder._combo_hits(leg['type'], leg['horses'], top3) for leg in legs):
            total += prob
    return total


class Context:
    """1レース分の勝率分布・lookup。"""

    def __init__(self, race, record):
        self.race = race
        self.record = record
        self.win_odds = {int(k): float(v) for k, v in (record.get('win_odds') or {}).items()}
        self.market = bet_builder.market_win_probabilities(self.win_odds)
        axis_horses = race.horses_for('◎')
        self.axis = axis_horses[0] if axis_horses else None
        self.overrides = {k: v for k, v in race.win_probabilities.items() if k in self.market}
        self.p = bet_builder.apply_subjective(self.market, self.overrides)
        self.priced = record.get('priced_odds') or {}
        self.lookup = priced_lookup(self.priced)

    @property
    def axis_odds(self):
        return self.win_odds.get(self.axis)

    def usable(self):
        """ハイブリッドを組めない理由（組めるなら None）。"""
        if self.axis is None:
            return '◎が無い'
        if not self.market or self.axis not in self.market:
            return '◎の単勝オッズが無い'
        if not self.overrides:
            return 'win_probabilities が無い'
        if self.axis_odds and self.axis_odds > discipline.WIN_ODDS_MAX:
            return f'◎単勝{self.axis_odds:.1f}倍＞{discipline.WIN_ODDS_MAX}倍（軸の上限）'
        return None

    def make_leg(self, bet_type, horses, odds):
        horses = sorted(int(h) for h in horses) if bet_type != '単勝' else [int(horses[0])]
        subj = leg_rate(self.p, bet_type, horses)
        mkt = leg_rate(self.market, bet_type, horses)
        cap = bet_builder._capped_rate(subj, mkt)
        return {'type': bet_type, 'horses': horses, 'odds': float(odds),
                'p_subj': subj, 'p_mkt': mkt, 'p': cap, 'ev': cap * float(odds)}


# ----------------------------------------------------------------------
# ポートフォリオ（買い目セット）
# ----------------------------------------------------------------------

def exact_expected_return(legs):
    """1点同額のときの正確な期待回収率 = Σ p_i × odds_i ÷ 点数。

    期待値の線形性により、点同士が同時に的中しうる（重なる）場合でも厳密に
    成り立つ。合成オッズ×合算的中率（第13章の近似）とは別物。
    """
    if not legs:
        return None
    return sum(leg['p'] * leg['odds'] for leg in legs) / len(legs)


def no_trigami(legs):
    """どの1点が当たっても総投資額を下回らない（全点のオッズ ≥ 点数）。"""
    return bool(legs) and all(leg['odds'] >= len(legs) for leg in legs)


def summarize(ctx, legs, name):
    """legs から評価値をまとめた辞書を作る。"""
    subj = union_rate(ctx.p, legs)
    mkt = union_rate(ctx.market, legs)
    union = bet_builder._capped_rate(subj, mkt)
    composite = discipline.composite_odds([leg['odds'] for leg in legs])
    return {
        'name': name, 'legs': legs,
        'composite': composite,
        'union': union, 'union_subj': subj, 'union_mkt': mkt,
        'ev_rule': (composite * union) if composite else None,
        'ev_exact': exact_expected_return(legs),
        'no_trigami': no_trigami(legs),
    }


def label(portfolio):
    if not portfolio:
        return '—'
    return ' / '.join(f"{leg['type']}{'-'.join(str(h) for h in leg['horses'])}"
                      f"({leg['odds']:g})" for leg in portfolio['legs'])


def _keeps_order(race, horses):
    return bet_builder._keeps_mark_order(race, SimpleNamespace(combos=[horses]))


def axis_legs(ctx, bet_type):
    """priced_odds にある◎絡みの bet_type の組を全部 leg にする。"""
    out = []
    for key, odds in (ctx.priced.get(bet_type) or {}).items():
        horses = [int(h) for h in key.split('-')]
        if ctx.axis not in horses or not odds or odds <= 0:
            continue
        if any(h not in ctx.p for h in horses):
            continue
        out.append(ctx.make_leg(bet_type, horses, odds))
    return out


def best_leg(ctx, bet_type, leg_min_ev=LEG_MIN_EV):
    """その券種で「部品として使える」組のうち、上限後の的中率が最大のもの。

    使える＝(a) 上限後の1点期待値が leg_min_ev 以上（既定0＝条件なし）、(b) 印の序列を崩さない
    （bet_builder._keeps_mark_order と同じ基準。1点ずつ守れば、組み合わせた
    セット全体でも守られる）。同率なら1点期待値の高い方。
    """
    ok = [leg for leg in axis_legs(ctx, bet_type)
          if leg['ev'] >= leg_min_ev and _keeps_order(ctx.race, leg['horses'])]
    if not ok:
        return None
    return max(ok, key=lambda leg: (leg['p'], leg['ev']))


def build_hybrids(ctx, leg_min_ev=LEG_MIN_EV):
    """{形: ポートフォリオ or None} と {形: 組めなかった理由}。"""
    portfolios, reasons = {}, {}
    why = ctx.usable()
    best = {} if why else {t: best_leg(ctx, t, leg_min_ev) for t in ('単勝', 'ワイド', '3連複')}
    for shape, types in SHAPES.items():
        if why:
            portfolios[shape], reasons[shape] = None, why
            continue
        missing = [t for t in types if best.get(t) is None]
        if missing:
            portfolios[shape] = None
            reasons[shape] = '部品なし：' + '・'.join(missing)
            continue
        portfolios[shape] = summarize(ctx, [best[t] for t in types], shape)
    return portfolios, reasons


def baseline(ctx, floor=True):
    """現行ルールの単一チケット（保険ワイド込み）を build_bets で組み直す。

    floor=False は的中率の足切り（MIN_HIT_RATE、2026-09-27導入）を外した版。
    足切りは本バックテスト対象のレースより後に入ったので、両方を出す。
    """
    race = ctx.race
    saved_rate = race.subjective_hit_rate
    saved_min = bet_builder.MIN_HIT_RATE
    try:
        if not floor:
            bet_builder.MIN_HIT_RATE = 0.0
        confidence, built, note = bet_builder.build_bets(race, ctx.lookup, ctx.win_odds)
    finally:
        bet_builder.MIN_HIT_RATE = saved_min
        race.subjective_hit_rate = saved_rate
    if not built:
        return None, note
    legs = [ctx.make_leg(b.type, b.horses, ctx.lookup(b.type, b.horses)) for b in built]
    return summarize(ctx, legs, 'B'), note


def rule_buys(rule, portfolio, ctx, floor=True):
    """ハイブリッドを買うか。"""
    if portfolio is None:
        return False
    if rule == 'R0':
        return True
    if floor and portfolio['union'] < bet_builder.MIN_HIT_RATE:
        return False
    if rule == 'R1':
        return (portfolio['composite'] is not None
                and portfolio['composite'] >= discipline.MIN_COMPOSITE_ODDS
                and portfolio['ev_rule'] >= discipline.MIN_EXPECTED_VALUE)
    ok = portfolio['ev_exact'] >= EXACT_MIN_EV and portfolio['no_trigami']
    if rule == 'R2':
        return ok
    if rule == 'R3':
        return ok and ctx.axis_odds is not None and ctx.axis_odds <= R3_AXIS_ODDS_MAX
    raise ValueError(rule)


def recorded_legs(record):
    """その検算で bet_builder が組んだ買い目（discipline の BLOCK 前）。"""
    out = []
    for item in record.get('bet_odds') or []:
        parts = str(item.get('bet', '')).split()
        if len(parts) < 2:
            continue
        horses = [int(h) for h in parts[1].split('-') if h.isdigit()]
        out.append((parts[0], tuple(sorted(horses)) if parts[0] != '単勝' else tuple(horses)))
    return sorted(out)


def legs_key(portfolio):
    if not portfolio:
        return []
    return sorted((leg['type'], tuple(leg['horses'])) for leg in portfolio['legs'])


# ----------------------------------------------------------------------
# 精算
# ----------------------------------------------------------------------

def settle(portfolio, result, stake=STAKE):
    """(投資, 払戻, 的中) を返す。買わない（None）なら (0, 0, False)。"""
    if not portfolio:
        return 0, 0, False
    staked = returned = 0
    for leg in portfolio['legs']:
        unit = results_module.payout_for(result['payouts'], leg['type'], leg['horses'])
        staked += stake
        returned += unit * stake // 100
    return staked, returned, returned > 0


def top3(result):
    return [h['umaban'] for h in sorted(result['finishing_order'], key=lambda h: h['rank'])[:3]]


# ----------------------------------------------------------------------
# 集計
# ----------------------------------------------------------------------

def evaluate(since, until, leg_min_ev=LEG_MIN_EV):
    rows, skipped = load_races(since, until)
    out = []
    for row in rows:
        race, record = row['race'], row['record']
        ctx = Context(race, record)
        base, base_note = baseline(ctx, floor=True)
        base0, _ = baseline(ctx, floor=False)
        hybrids, reasons = build_hybrids(ctx, leg_min_ev)
        result = review.cached_result(race.race_id)
        decisions = {(s, r, f): rule_buys(r, hybrids[s], ctx, floor=f)
                     for s in SHAPES for r in RULES for f in (True, False)}
        out.append({
            **row, 'ctx': ctx, 'base': base, 'base_note': base_note, 'base0': base0,
            'hybrids': hybrids, 'reasons': reasons, 'decisions': decisions,
            'recorded': recorded_legs(record), 'blocked': bool(record.get('blocked')),
            'result': result,
            'actual': [(b.type, b.horses) for b in race.bets],
        })
    return out, skipped


def strategy_portfolio(row, shape, rule, floor, fallback=True):
    """その方針でそのレースに買うポートフォリオ（買わないなら None）。"""
    fallback_base = row['base'] if floor else row['base0']
    if shape is None:
        return fallback_base
    if row['decisions'][(shape, rule, floor)]:
        return row['hybrids'][shape]
    return fallback_base if fallback else None


def tally(rows, pick):
    t = {'races': 0, 'bought': 0, 'hits': 0, 'staked': 0, 'returned': 0, 'legs': 0,
         'ev_sum': 0.0}
    for row in rows:
        if not row['result']:
            continue
        t['races'] += 1
        portfolio = pick(row)
        if not portfolio:
            continue
        staked, returned, hit = settle(portfolio, row['result'])
        t['bought'] += 1
        t['legs'] += len(portfolio['legs'])
        t['staked'] += staked
        t['returned'] += returned
        t['hits'] += int(hit)
        t['ev_sum'] += portfolio['ev_exact'] * staked
    return t


def reproduction_reason(row):
    """当時の組（recorded）と組み直し（B0）がずれた理由の分類。"""
    if legs_key(row['base0']) == row['recorded']:
        return None
    if legs_key(row['base']) == row['recorded']:
        return '足切り5%（09-27導入）で説明できる'
    race = row['race']
    axis_legs_rec = [horses for t, horses in row['recorded'] if row['ctx'].axis in horses]
    if axis_legs_rec and not all(_keeps_order(race, list(h)) for h in axis_legs_rec):
        return '当時の組が印の序列を崩していた（09-27から選ばない）' + (
            '・当時BLOCK' if row['blocked'] else '')
    if row['day'] < CAP_ADOPTED:
        return 'CAP 1.5 導入（09-18）前の記録（当時は上限なしで組んでいた）'
    return '上記以外（印の修正など。要確認）'


def strategies():
    """(キー, 表示名, shape, rule, floor, fallback)。

    fallback=True はハイブリッドを買わないレースで基準に戻す完結した方針。
    R0（無条件）は参考として「採用レースのみ」にだけ出す。
    """
    out = [('B', '基準：現行の単一チケット（足切り5%あり）', None, None, True, True),
           ('B0', '基準：現行の単一チケット（足切りなし）', None, None, False, True)]
    for floor in (True, False):
        tag = '' if floor else '・足切りなし'
        for shape in SHAPES:
            for rule in RULES[1:]:
                out.append((f'{shape}-{rule}{"" if floor else "-nf"}',
                            f'{shape}×{rule}（非採用時は基準{tag}）',
                            shape, rule, floor, True))
    for shape in SHAPES:
        for rule in RULES:
            out.append((f'{shape}-{rule}-only', f'{shape}×{rule}：採用レースのみ',
                        shape, rule, True, False))
    return out


def all_tallies(rows):
    out = {}
    for key, name, shape, rule, floor, fallback in strategies():
        out[key] = (name, tally(rows, lambda r, s=shape, ru=rule, f=floor, fb=fallback:
                                strategy_portfolio(r, s, ru, f, fb)))
    return out


# ----------------------------------------------------------------------
# 出力
# ----------------------------------------------------------------------

def _yen(n):
    return f'{n:,}円'


def _pct(num, den):
    return f'{num / den * 100:.1f}%' if den else '—'


def _roi(t):
    return _pct(t['returned'], t['staked'])


def _model_roi(t):
    return f"{t['ev_sum'] / t['staked'] * 100:.0f}%" if t['staked'] else '—'


def _short(portfolio):
    if not portfolio:
        return '見送り'
    return ' / '.join(f"{leg['type']}{'-'.join(str(h) for h in leg['horses'])}"
                      for leg in portfolio['legs'])


def _median(values):
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def render_coverage(rows, skipped, since, until):
    settled = [r for r in rows if r['result']]
    out = ['## 1　データの範囲', '']
    out.append(f'- 対象期間：{since.isoformat()}〜{until.isoformat()}'
               f'（`priced_odds` の記録は {PRICED_ODDS_FROM.isoformat()} から）')
    out.append(f'- `priced_odds` と `win_odds` の両方が残っているレース：**{len(rows)}件**'
               f'（中央{sum(r["race"].org == "jra" for r in rows)}・'
               f'地方{sum(r["race"].org == "nar" for r in rows)}）')
    pending = [r for r in rows if not r['result']]
    out.append(f'- うち結果確定（`data/results/` に保存済み）：**{len(settled)}件**。'
               f'未確定{len(pending)}件は精算から除外：'
               + ('、'.join(f'{r["day"].isoformat()} {r["race"].name}' for r in pending) or 'なし'))
    if skipped:
        out.append(f'- 買い目ファイルに無く除外：{len(skipped)}件')
    bought_actual = sum(1 for r in settled if r['actual'])
    out.append(f'- 確定済みのうち、`data/bets` の最終 bets に買い目が残っているレース：'
               f'{bought_actual}件（参考。本バックテストは今のルールで組み直した基準と比べる）')
    out.append('- 使った記録は各レースで「priced_odds と win_odds が両方ある最後の検算」。'
               '発走後の検算はどちらも空になるので、実質的に最後の発走前検算。')
    out.append('- 評価に使うオッズは発走前の記録値、精算は確定払戻。両者のずれは'
               'どの方針にも同じようにかかる。')
    out.append('')
    return out


def render_definitions(leg_min_ev):
    cap = bet_builder.SET_HIT_RATE_CAP
    out = ['## 2　定義', '']
    out.append('### 確率モデル（本番と同じ）')
    out.append('')
    out.append('- 市場勝率＝`market_win_probabilities(win_odds)`、主観勝率＝'
               '`apply_subjective(市場, win_probabilities)`、組の的中率は Harville。')
    out.append(f'- 1点ごとの的中率 p は本番どおり `min(主観, 市場のみ × {cap})`'
               f'（SET_HIT_RATE_CAP）。セット全体の合算的中率にも同じ上限を'
               f'セット単位で当てる（`_build_candidates` と同じ）。')
    out.append('- 合算的中率（どれか1点でも当たる確率）は、全馬の1〜3着の並びを総当たりして'
               '重なりを二重に数えない（`_union_hit_rate` と同じ方法）。')
    out.append('- **正確な期待回収率** ＝ Σ(p_i × オッズ_i) ÷ 点数（1点100円同額）。'
               '期待値の線形性により、点同士が同時に当たりうる場合でも厳密。')
    out.append('- **規律上の期待値** ＝ 合成オッズ × 合算的中率（第13章）。'
               '券種が混ざると正確な値からずれる。')
    out.append('')
    out.append('### 基準（現行の単一チケット）')
    out.append('')
    out.append('- `bet_builder.build_bets` を `priced_odds` の lookup で呼び直したもの'
               f'（CAP {cap}・印の序列・◎単勝{discipline.WIN_ODDS_MAX}倍上限・保険ワイド込み）。')
    out.append(f'- 足切り（MIN_HIT_RATE {bet_builder.MIN_HIT_RATE:.0%}）は 2026-09-27 導入で'
               '対象レースのほとんどより後なので、あり（B）となし（B0）の両方を出す。')
    out.append('')
    out.append('### ハイブリッドの組み方')
    out.append('')
    out.append('部品は `priced_odds` にある**◎を含む**組だけ（bet_builder は◎軸しか値付けしない）。'
               '券種ごとに、次を満たす組のうち上限後の的中率 p が最大の1点を「最良」とする'
               '（同率なら1点期待値が高い方。bet_builder の「規律を満たすうち的中率最大」と同じ考え方）。')
    out.append('')
    out.append('- 印の序列を崩さない（`_keeps_mark_order` と同じ。○を外して▲△を入れない）。'
               '1点ずつ守れば、組み合わせたセット全体でも守られる。')
    if leg_min_ev > 0:
        out.append(f'- 1点期待値 p × オッズ ≥ {leg_min_ev}')
    else:
        out.append('- 1点ごとの期待値の足切りは置かない（期待値はセット単位の採否ルールで判定する）。'
                   f'1点期待値 ≥ {SENSITIVITY_LEG_MIN_EV} を部品の条件にした版は8章の感度分析に出す。')
    out.append(f'- ◎の単勝が{discipline.WIN_ODDS_MAX}倍超のレースは組まない（本番の軸上限と同じ）。')
    out.append('')
    for shape, text in SHAPE_LABELS.items():
        out.append(f'- **{shape}**：{text}（部品が1つでも欠けたら組まない）')
    out.append('')
    out.append('### 採否ルール')
    out.append('')
    out.append(f'- **R1**：{RULE_LABELS["R1"]}。')
    out.append(f'- **R2**：正確な期待回収率 ≥ {EXACT_MIN_EV}、かつ全点のオッズ ≥ 点数'
               '（どの1点が当たっても総投資を割らない＝トリガミ無し）。')
    out.append(f'- **R3**：R2 に加えて◎の単勝オッズ ≤ {R3_AXIS_ODDS_MAX}倍。'
               f'市場推定勝率でおおむね16%以上（0.8÷{R3_AXIS_ODDS_MAX}）の上位人気の◎に限る。'
               '第7章の「網を張る」は◎が強いことが前提なので、その条件を明示した。'
               '**値は結果を見る前に決め、調整していない。**')
    out.append('- **R0**（参考）：組めたら必ず買う。網そのものの的中率・回収率を見るための対照。')
    out.append('- 足切りありの方針では、ハイブリッドの合算的中率にも5%の足切りを当てる（R0を除く）。')
    out.append('- ハイブリッドを買わないレースは基準の単一チケットに戻す（＝方針として完結させる）。'
               '「採用レースのみ」はハイブリッドを買ったレースだけの成績。')
    out.append('- 精算は `results.payout_for`（保存済みの確定払戻）で1点100円。')
    out.append('')
    return out


def render_reproduction(rows):
    out = ['## 3　基準の再現チェック', '']
    out.append('組み直した基準（足切りなしB0）と、その検算で実際に bet_builder が組んだ買い目'
               '（`bet_odds`、discipline の BLOCK 前）を比べる。')
    out.append('')
    diff = [r for r in rows if reproduction_reason(r)]
    out.append(f'- 一致：{len(rows) - len(diff)}/{len(rows)}件')
    counts = {}
    for r in diff:
        counts[reproduction_reason(r)] = counts.get(reproduction_reason(r), 0) + 1
    for reason, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        out.append(f'- 不一致：{reason} … {n}件')
    if diff:
        out.append('')
        out.append('| 日付 | レース | 当時の組 | 組み直し(B0) | 組み直し(B) | 理由 |')
        out.append('|---|---|---|---|---|---|')
        for r in diff:
            rec = ' / '.join(f"{t}{'-'.join(str(h) for h in hs)}"
                             for t, hs in r['recorded']) or '見送り'
            out.append(f"| {r['day'].isoformat()} | {r['race'].name} | {rec} | "
                       f"{_short(r['base0'])} | {_short(r['base'])} | {reproduction_reason(r)} |")
    out.append('')
    out.append('本バックテストの基準は「今のルールならどうだったか」（B）であり、当時の購入実績ではない。')
    out.append('')
    return out


def render_comparison(rows, tallies):
    out = ['## 4　方針の比較（結果確定レースのみ）', '']
    out.append('「モデル期待」は買った組の正確な期待回収率を投資額で加重平均したもの'
               '（モデルが正しければ回収率はこの付近に来るはずの値）。')
    out.append('')
    header = '| 方針 | 購入 | 点数 | 的中 | 的中率 | 投資 | 払戻 | 回収率 | モデル期待 |'
    sep = '|---|---:|---:|---:|---:|---:|---:|---:|---:|'
    groups = [
        ('### 4-1　足切りあり（現行本番に相当）', lambda s: s[4] and s[5]),
        ('### 4-2　足切りなし（対象レース当時に近い）', lambda s: (not s[4]) and s[5]),
        ('### 4-3　ハイブリッドを買ったレースだけ（足切りあり。R0＝無条件は参考）',
         lambda s: not s[5]),
    ]
    for title, pred in groups:
        out.append(title)
        out.append('')
        out.append(header)
        out.append(sep)
        for s in strategies():
            if not pred(s):
                continue
            name, t = tallies[s[0]]
            out.append(f"| {name} | {t['bought']}/{t['races']} | {t['legs']} | {t['hits']} | "
                       f"{_pct(t['hits'], t['bought'])} | {_yen(t['staked'])} | "
                       f"{_yen(t['returned'])} | {_roi(t)} | {_model_roi(t)} |")
        out.append('')
    out.append('### 4-4　中央／地方別（足切りあり・非採用時は基準）')
    out.append('')
    out.append('| 方針 | 中央 購入/的中 | 中央 投資→払戻 | 中央 回収率 | 地方 購入/的中 | '
               '地方 投資→払戻 | 地方 回収率 |')
    out.append('|---|---:|---:|---:|---:|---:|---:|')
    for key, _name, shape, rule, floor, fallback in strategies():
        if not floor or not fallback:
            continue
        cells = []
        for org in ('jra', 'nar'):
            t = tally([r for r in rows if r['race'].org == org],
                      lambda r, s=shape, ru=rule: strategy_portfolio(r, s, ru, True, True))
            cells += [f"{t['bought']}/{t['hits']}",
                      f"{t['staked']:,}→{t['returned']:,}", _roi(t)]
        out.append(f"| {key} | " + ' | '.join(cells) + ' |')
    out.append('')
    return out


def render_adoption(rows):
    """各形・各ルールで何件組めて何件採用されたか（未確定も含む全件）。"""
    out = ['## 5　ハイブリッドが組めた件数と採否（未確定を含む全件）', '']
    out.append('| 形 | 組めた | R1採用 | R2採用 | R3採用 | 合成3.0倍以上 | 正確な期待1.2以上 | '
               'トリガミ無し | 組めなかった理由 |')
    out.append('|---|---:|---:|---:|---:|---:|---:|---:|---|')
    for shape in SHAPES:
        built = [r['hybrids'][shape] for r in rows if r['hybrids'][shape]]
        reasons = {}
        for r in rows:
            if not r['hybrids'][shape]:
                reasons[r['reasons'][shape]] = reasons.get(r['reasons'][shape], 0) + 1
        top = '、'.join(f'{k}（{v}）' for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))
        counts = [sum(r['decisions'][(shape, rule, True)] for r in rows) for rule in RULES[1:]]
        out.append(f'| {shape} | {len(built)}/{len(rows)} | {counts[0]} | {counts[1]} | '
                   f'{counts[2]} | {sum(p["composite"] >= discipline.MIN_COMPOSITE_ODDS for p in built)} | '
                   f'{sum(p["ev_exact"] >= EXACT_MIN_EV for p in built)} | '
                   f'{sum(p["no_trigami"] for p in built)} | {top or "—"} |')
    out.append('')
    for shape in SHAPES:
        built = [r['hybrids'][shape] for r in rows if r['hybrids'][shape]]
        if not built:
            continue
        out.append(f'- {shape}：合成オッズの中央値 {_median([p["composite"] for p in built]):.2f}倍、'
                   f'正確な期待回収率の中央値 {_median([p["ev_exact"] for p in built]):.2f}、'
                   f'合算的中率（上限後）の中央値 {_median([p["union"] for p in built]) * 100:.1f}%。')
    out.append('')
    return out


def _settle_text(portfolio, result, adopted=True):
    if not portfolio:
        return '—'
    if not result:
        return '未確定'
    staked, returned, _hit = settle(portfolio, result)
    text = f'{returned}/{staked}'
    return text if adopted else f'({text})'


def render_per_race(rows):
    out = ['## 6　レース別', '']
    out.append('B＝基準（足切りあり）。H1欄は組・合成オッズ/合算的中率/正確な期待回収率と、'
               'R1/R2/R3 の採否（○＝採用・足切りあり）。払戻欄は「払戻/投資」（100円/点）で、'
               'そのルールで不採用なら括弧書き（組めていれば払戻は参考として出す）。')
    out.append('')
    out.append('| 日付 | 組織 | レース | ◎(単勝) | 1-2-3着 | B の組 | B | H1 の組 | '
               'H1 合成/的中/期待 | R1/R2/R3 | H1(R2) | H2(R2) | H3(R2) |')
    out.append('|---|---|---|---|---|---|---:|---|---|---|---:|---:|---:|')
    for r in rows:
        ctx = r['ctx']
        res = r['result']
        finish = '-'.join(str(h) for h in top3(res)) if res else '未確定'
        h1 = r['hybrids']['H1']
        if h1:
            stats = f"{h1['composite']:.2f}/{h1['union'] * 100:.0f}%/{h1['ev_exact']:.2f}"
            marks = ''.join('○' if r['decisions'][('H1', rule, True)] else '×'
                            for rule in RULES[1:])
            h1_text = label(h1)
        else:
            stats, marks, h1_text = '—', '—', f"（{r['reasons']['H1']}）"
        axis_odds = f"{ctx.axis}({ctx.axis_odds:g})" if ctx.axis_odds else str(ctx.axis)
        hyb = [_settle_text(r['hybrids'][s], res, r['decisions'][(s, 'R2', True)])
               for s in SHAPES]
        out.append(f"| {r['day'].strftime('%m-%d')} | {'中' if r['race'].org == 'jra' else '地'} | "
                   f"{r['race'].name} | {axis_odds} | {finish} | {label(r['base'])} | "
                   f"{_settle_text(r['base'], res)} | {h1_text} | {stats} | {marks} | "
                   + ' | '.join(hyb) + ' |')
    out.append('')
    return out


def render_sensitivity(since, until):
    rows, _ = evaluate(since, until, leg_min_ev=SENSITIVITY_LEG_MIN_EV)
    tallies = all_tallies(rows)
    out = [f'## 8　感度分析：部品に「1点期待値 ≥ {SENSITIVITY_LEG_MIN_EV}」を課した場合', '']
    out.append('自分のモデルでも損と見ている組を網に入れない版。部品が欠けて組めないレースが増える。')
    out.append('')
    out.append('| 形 | 組めた | R1/R2/R3 採用 | R2（非採用時は基準）回収率・的中 | '
               'R2 採用レースのみ 払戻/投資 |')
    out.append('|---|---:|---|---|---|')
    for shape in SHAPES:
        built = sum(1 for r in rows if r['hybrids'][shape])
        counts = '/'.join(str(sum(r['decisions'][(shape, rule, True)] for r in rows))
                          for rule in RULES[1:])
        full = tallies[f'{shape}-R2'][1]
        only = tallies[f'{shape}-R2-only'][1]
        out.append(f"| {shape} | {built}/{len(rows)} | {counts} | {_roi(full)}・{full['hits']} | "
                   f"{only['returned']:,}/{only['staked']:,}（{only['bought']}件・的中{only['hits']}） |")
    out.append('')
    return out


def render_findings(rows, tallies):
    settled = [r for r in rows if r['result']]
    t = {k: v[1] for k, v in tallies.items()}
    b, b0 = t['B'], t['B0']
    out = ['## 7　所見', '']
    out.append(f"- 結果確定 {len(settled)}件で、基準（足切りあり）は {b['bought']}件購入・"
               f"{b['hits']}件的中・回収率 {_roi(b)}（足切りなしは {b0['bought']}件・"
               f"{b0['hits']}件的中・{_roi(b0)}）。**どの方針も的中は数件で、"
               "1件の配当の有無で回収率が数十ポイント以上動く。**")
    built = {s: sum(1 for r in rows if r['hybrids'][s]) for s in SHAPES}
    r1 = {s: sum(r['decisions'][(s, 'R1', True)] for r in rows) for s in SHAPES}
    comp_ok = {s: sum(1 for r in rows if r['hybrids'][s]
                      and r['hybrids'][s]['composite'] >= discipline.MIN_COMPOSITE_ODDS)
               for s in SHAPES}
    out.append(f"- **R1（現行規律）はハイブリッドをほぼ全部落とす。** 組めた件数に対する採用は "
               + '、'.join(f"{s} {r1[s]}/{built[s]}" for s in SHAPES)
               + '。合成3.0倍を満たすのは '
               + '、'.join(f"{s} {comp_ok[s]}/{built[s]}" for s in SHAPES)
               + 'で、単勝・ワイドのような低オッズ券を混ぜた時点で合成が割れる。'
               '第7章が実行されないのは実装の偶然ではなく規律の構造であることが、件数に'
               'よらない事実として確認できる。')
    r0 = {s: t[f'{s}-R0-only'] for s in SHAPES}
    out.append('- 網そのもの（R0＝組めたら必ず買う）の成績：'
               + '、'.join(f"{s} 的中{r0[s]['hits']}/{r0[s]['bought']}（{_pct(r0[s]['hits'], r0[s]['bought'])}）"
                           f"・回収率{_roi(r0[s])}・モデル期待{_model_roi(r0[s])}" for s in SHAPES)
               + f"。購入レースあたりの的中率は基準（{_pct(b['hits'], b['bought'])}）より高く、"
               '第7章の言う「網」の効果は出ている。ただしモデル自身の期待回収率（5章の中央値）が'
               '1を下回る組が大半で、網を張ること自体は期待値を生まない。')
    r2 = {s: t[f'{s}-R2-only'] for s in SHAPES}
    r3 = {s: t[f'{s}-R3-only'] for s in SHAPES}
    out.append('- R2（正確な期待値1.2＋トリガミ無し）の採用レースのみ：'
               + '、'.join(f"{s} {r2[s]['bought']}件・的中{r2[s]['hits']}・"
                           f"{r2[s]['returned']:,}/{r2[s]['staked']:,}円" for s in SHAPES)
               + f"。R3（◎単勝{R3_AXIS_ODDS_MAX:.1f}倍以下）は "
               + '、'.join(f"{s} {r3[s]['bought']}件・的中{r3[s]['hits']}" for s in SHAPES)
               + '。')
    split, total_in, total_out = [], 0, 0
    for s in SHAPES:
        inside = [r for r in settled if r['decisions'][(s, 'R3', True)]]
        outside = [r for r in settled
                   if r['decisions'][(s, 'R2', True)] and not r['decisions'][(s, 'R3', True)]]
        hits_in = sum(settle(r['hybrids'][s], r['result'])[2] for r in inside)
        hits_out = sum(settle(r['hybrids'][s], r['result'])[2] for r in outside)
        total_in += hits_in
        total_out += hits_out
        split.append(f"{s} R3内 {len(inside)}件・的中{hits_in}／R3外 {len(outside)}件・的中{hits_out}")
    if total_out and not total_in:
        verdict = ('**現時点のハイブリッドの的中はすべて◎が人気薄側（R3の外）から出ており、'
                   '「◎が強いときだけ網を張る」という R3 の前提を支持するデータはまだ無い**'
                   '（逆を示すにも件数が足りない）。')
    else:
        verdict = 'どちらの側も件数が少なく、R3 の条件の良し悪しはまだ判断できない。'
    out.append(f"- R2採用を◎の単勝{R3_AXIS_ODDS_MAX:.1f}倍で分けると（結果確定分）："
               + '、'.join(split) + '。' + verdict)
    lines = [f"{s}×{rule} {_roi(t[f'{s}-{rule}'])}（的中{t[f'{s}-{rule}']['hits']}）"
             for s in SHAPES for rule in RULES[1:]]
    out.append(f"- 基準へのフォールバック込みの回収率（足切りあり）：基準 {_roi(b)}"
               f"（的中{b['hits']}）に対し、" + '、'.join(lines) + '。')
    out.append("- 基準との差は、ハイブリッドが採用された数レースの結果だけで決まっている"
               "（6章のレース別表で確認できる）。")
    out.append('')
    out.append('### このデータで言えること・言えないこと')
    out.append('')
    out.append(f"- **言えない**：どの方針が優れているか。n={len(settled)}レース・的中は各方針とも"
               "数件で、回収率の差は1〜2件の的中の有無でほぼ全部説明できる。ハイブリッドが"
               "実際に採用されたレースは各ルールで数件しかなく、採用レースの回収率は"
               "統計的には何も示さない。**この結果だけで基準値（合成3.0倍・期待値1.2・"
               "足切り5%）や買い方を変える根拠にはならない。**")
    out.append("- **言える**：(1) R1 がハイブリッドを落とすのは構造的な事実。第7章を実行するなら、"
               "合成オッズではなく正確な期待回収率で判定する別の規律（R2/R3のような）が要る。"
               "(2) 網を張ると的中頻度は上がるが、今のモデル（CAP 1.5後）ではほとんどの網が"
               "期待値1を割っており、「当たる回数」と「儲かるか」は別。"
               "(3) ハイブリッドの部品は `priced_odds` にある◎軸の組に限られ、単勝は◎1頭分しか"
               "値付けされていない。○の単勝や複勝を部品にする形は今のデータでは評価できない。")
    out.append("- **今後見続ける価値があるのは R2**（とその部分集合の R3）。R2 は正確な期待値で"
               "判定するので券種を混ぜても判定が歪まず、トリガミも避け、R1と違って実際に"
               "ハイブリッドを採用する。R3 は第7章の前提（◎が強いときだけ網を張る）を明示した"
               "版だが、◎が人気のときは部品のオッズが低く期待値1.2に届きにくいため、採用が"
               "ほとんど出ない。R2 と R3 の採用レースを別々に積み上げ、R2採用のうち R3 を"
               "満たさないレース（◎が人気薄）の成績が悪いかを見るのが次の確認点になる。"
               "R1 は比較の対照として残す。")
    out.append("- 判断に使える件数の目安：採用レースが各ルール30件程度（的中が2桁）"
               "たまるまでは、本レポートは定期的に再生成して推移を見るだけにとどめる。")
    out.append('')
    return out


def build_report(since, until):
    rows, skipped = evaluate(since, until)
    tallies = all_tallies(rows)
    out = ['# ハイブリッド馬券術（第7章）のバックテスト', '',
           f'生成：`python3 backtest_hybrid.py --since {since.isoformat()} '
           f'--until {until.isoformat()}`（保存済みデータのみ・外部通信なし）。'
           '**数字を出すだけで、基準・ロジック・予想メソッドは変えていない。**', '']
    out += render_coverage(rows, skipped, since, until)
    out += render_definitions(LEG_MIN_EV)
    out += render_reproduction(rows)
    out += render_comparison(rows, tallies)
    out += render_adoption(rows)
    out += render_per_race(rows)
    out += render_findings(rows, tallies)
    out += render_sensitivity(since, until)
    return '\n'.join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='ハイブリッド馬券術（第7章）を保存済みデータでバックテストする。')
    parser.add_argument('--since', default=PRICED_ODDS_FROM.isoformat(),
                        help=f'集計開始日 YYYY-MM-DD（既定 {PRICED_ODDS_FROM.isoformat()}'
                             '＝priced_odds の最初）')
    parser.add_argument('--until', default=date.today().isoformat(),
                        help='集計終了日 YYYY-MM-DD（既定 今日）')
    parser.add_argument('--out', default=DEFAULT_OUTPUT,
                        help=f'Markdown の書き出し先（既定 {DEFAULT_OUTPUT}）')
    args = parser.parse_args(argv)
    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until)
    text = build_report(since, until)
    print(text)
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
