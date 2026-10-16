#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主観勝率が「確定オッズ」より勝ち馬を当てているかと、◎のオッズが発走までにどれだけ動くかを測る。

## なぜこれがあるか

2026-10-16 の調査（`docs/調査/競馬AIデータラボの記事とメソッドの突き合わせ.md`）で、
主観勝率は検算時のオッズよりは勝ち馬を当てているが、確定オッズには勝てていない、
という傾向が出た（60レース）。払戻は確定オッズで決まるので、利益の元になるのは
「確定オッズより正確な部分」だけである。それを毎週1つの数字で見られるようにする
（同調査の改善案1）。あわせて、◎の単勝オッズが検算から確定までにどれだけ下がるかを
検算の時刻別に出す（改善案3の測定部分）。

**数字を出すだけで、予想メソッドにも買い目にも一切触らない。**

## 測り方

- 対象：`win_probabilities`（馬番ごとの主観勝率）があり、検算記録に単勝オッズ
  （`win_odds`。2026-09-16 から記録）があり、確定結果に各馬の確定オッズがあるレース。
- 3つの確率を、勝ち馬に付けていた確率の対数（対数尤度。0に近いほど良い）で比べる。
  - 市場（検算時）：最後の検算の単勝オッズから出した勝率
  - 主観：その市場勝率に `win_probabilities` を当てたもの（`bet_builder.apply_subjective`。
    直前検算が買い目を組むときと同じ）
  - 市場（確定）：確定オッズから出した勝率
- 「主観 − 確定」の1レースあたりの平均と、その標準誤差を出す。プラスで安定すれば、
  主観が確定オッズより正確な部分を持っている。
- オッズのずれ：検算のたびに、◎の「確定オッズ ÷ その検算で見たオッズ」を、
  発走の何分前の検算だったかで分けて出す。1未満なら発走までに売れた（オッズが下がった）。

使い方: python edge_check.py
"""

import json
import math
import os
import statistics
from datetime import date, datetime, timedelta, timezone

import bet_builder
import bets

JST = timezone(timedelta(hours=9))

# 検算記録に単勝オッズが残るようになった日（calibration_check.WIN_ODDS_LOGGED_FROM と同じ）
SINCE = date(2026, 9, 16)

# 主観と市場の混ぜ方（改善案2）を検討してよいレース数の目安（2026-10-16 決定ログ）
READY_RACES = 200

# ◎のオッズのずれを期待値に織り込むか（改善案3の本体）を検討してよい頭数の目安
# （最後の検算の◎。2026-10-16 決定ログ）
READY_DRIFT = 100

# 発走の何分前の検算か。(下限, 上限, ラベル)
BUCKETS = ((120, None, '発走120分以上前'),
           (60, 120, '発走60〜120分前'),
           (0, 60, '発走60分前以内'))


# ----------------------------------------------------------------------
# 入力（保存済みファイルだけを読む。外部通信はしない）
# ----------------------------------------------------------------------

def load_checks(day):
    """その日の検算記録（追記型のリスト）。無ければ空。"""
    path = os.path.join(bets.CHECKS_DIR, f'{day.isoformat()}.json')
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def race_checks(history, race_id):
    """1レース分の、単勝オッズが入った検算を古い順に [(検算時刻, {馬番: オッズ})] で返す。

    発走後の検算はオッズを取りに行かないので win_odds が空になる。それは落とす。
    """
    out = []
    for entry in history:
        try:
            at = datetime.fromisoformat(entry['checked_at'])
        except (KeyError, TypeError, ValueError):
            continue
        for race in entry.get('races', []):
            if str(race.get('race_id')) != str(race_id):
                continue
            win_odds = race.get('win_odds') or {}
            if win_odds:
                out.append((at, {int(k): float(v) for k, v in win_odds.items()}))
    return sorted(out, key=lambda x: x[0])


def final_odds_and_winner(result):
    """確定結果から ({馬番: 確定オッズ}, 1着の馬番)。取れなければ (None, None)。"""
    order = (result or {}).get('finishing_order') or []
    final = {int(h['umaban']): float(h['odds']) for h in order
             if h.get('umaban') is not None and h.get('odds')}
    winners = [int(h['umaban']) for h in order if h.get('rank') == 1]
    if not final or not winners:
        return None, None
    return final, winners[0]


def collect(days, cached_result):
    """対象日のレースを、集計に使える形の辞書にそろえる。

    cached_result は review.cached_result（保存済みの確定結果を読むだけで取りに行かない）。
    review から渡してもらうのは、review がこのモジュールを読み込むので循環を避けるため。
    """
    records = []
    for day in days:
        if day < SINCE:
            continue
        sheet = bets.load_sheet(day)
        if not sheet:
            continue
        history = load_checks(day)
        for race in sheet.races:
            if not race.win_probabilities:
                continue
            checks = race_checks(history, race.race_id)
            final, winner = final_odds_and_winner(cached_result(race.race_id))
            if not checks or final is None:
                continue
            records.append({
                'day': day,
                'race_id': str(race.race_id),
                'org': race.org,
                'name': race.name,
                'start_time': race.start_time,
                'win_probabilities': {int(k): v for k, v in race.win_probabilities.items()},
                'marks': {int(k): race.mark_of(int(k)) for k in final},
                'checks': checks,
                'final': final,
                'winner': winner,
            })
    return records


# ----------------------------------------------------------------------
# 集計
# ----------------------------------------------------------------------

def _normalize(raw):
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()} if total > 0 else {}


def race_logliks(record):
    """1レースの (市場・検算時, 主観, 市場・確定) の対数尤度。使えなければ None。

    取消などで検算時と確定時の出走馬がずれるので、両方にいる馬だけで確率を作り直す。
    """
    seen = record['checks'][-1][1]
    keys = [k for k in record['final'] if k in seen and seen[k] > 0 and record['final'][k] > 0]
    winner = record['winner']
    if winner not in keys or len(keys) < 2:
        return None
    m_seen = _normalize({k: 1.0 / seen[k] for k in keys})
    m_final = _normalize({k: 1.0 / record['final'][k] for k in keys})
    overrides = {k: v for k, v in record['win_probabilities'].items() if k in m_seen}
    p_subj = _normalize(bet_builder.apply_subjective(m_seen, overrides))
    if not (m_seen.get(winner) and p_subj.get(winner) and m_final.get(winner)):
        return None
    return (math.log(m_seen[winner]), math.log(p_subj[winner]), math.log(m_final[winner]))


def loglik_summary(records):
    """レース数・各対数尤度の合計・「主観 − 確定」の平均と標準誤差。"""
    rows = [x for x in (race_logliks(r) for r in records) if x is not None]
    n = len(rows)
    out = {'races': n, 'seen': sum(r[0] for r in rows), 'subjective': sum(r[1] for r in rows),
           'final': sum(r[2] for r in rows), 'diff_mean': None, 'diff_se': None}
    diffs = [r[1] - r[2] for r in rows]
    if n >= 1:
        out['diff_mean'] = statistics.mean(diffs)
    if n >= 2:
        out['diff_se'] = statistics.stdev(diffs) / math.sqrt(n)
    return out


def _post_time(record):
    try:
        hh, mm = (int(x) for x in str(record['start_time']).split(':')[:2])
    except (TypeError, ValueError):
        return None
    return datetime(record['day'].year, record['day'].month, record['day'].day,
                    hh, mm, tzinfo=JST)


def bucket_of(minutes_before):
    for low, high, label in BUCKETS:
        if minutes_before >= low and (high is None or minutes_before < high):
            return label
    return None


def drift_summary(records, mark='◎'):
    """印 mark の馬の「確定オッズ ÷ 検算で見たオッズ」を、検算の時刻別と最後の検算で集める。"""
    ratios = {label: [] for _, _, label in BUCKETS}
    ratios['最後の検算'] = []
    for r in records:
        post = _post_time(r)
        horses = [k for k, m in r['marks'].items() if m == mark]
        for i, (at, seen) in enumerate(r['checks']):
            for k in horses:
                if not seen.get(k) or not r['final'].get(k):
                    continue
                ratio = r['final'][k] / seen[k]
                if post is not None:
                    minutes = (post - at.astimezone(JST)).total_seconds() / 60
                    label = bucket_of(minutes)
                    if label:
                        ratios[label].append(ratio)
                if i == len(r['checks']) - 1:
                    ratios['最後の検算'].append(ratio)
    return ratios


def describe(values):
    if not values:
        return None
    return {'n': len(values), 'median': statistics.median(values),
            'mean': statistics.mean(values),
            'down': sum(v < 1 for v in values) / len(values)}


def analyze(days, cached_result, week_days=()):
    records = collect(days, cached_result)
    week = set(week_days)
    return {
        'total': loglik_summary(records),
        'week': loglik_summary([r for r in records if r['day'] in week]),
        'drift': {label: describe(v) for label, v in drift_summary(records).items()},
    }


# ----------------------------------------------------------------------
# 出力
# ----------------------------------------------------------------------

def _diff_text(s):
    if s['diff_mean'] is None:
        return '—'
    se = f"（標準誤差 {s['diff_se']:.3f}）" if s['diff_se'] is not None else ''
    return f"{s['diff_mean']:+.3f}{se}"


def render_lines(summary):
    """週次レビューの詳細ファイルに載せる Markdown の行。判定はしない。"""
    t, w = summary['total'], summary['week']
    lines = [
        f'{SINCE.isoformat()} 以降の、検算の実オッズと確定結果がそろったレースのみ。'
        '数字が0に近いほど、勝ち馬に高い確率を付けていた。',
        '',
        '| 確率の出し方 | 通算（対数尤度の合計） | 今週 |',
        '|---|---|---|',
        f"| 市場（検算時の単勝オッズ） | {t['seen']:.2f} | {w['seen']:.2f} |",
        f"| 主観（`win_probabilities` を当てたもの） | {t['subjective']:.2f} | {w['subjective']:.2f} |",
        f"| 市場（確定オッズ） | {t['final']:.2f} | {w['final']:.2f} |",
        f"| レース数 | {t['races']} | {w['races']} |",
        '',
        f"- **主観 − 確定（1レースあたり）：通算 {_diff_text(t)} ／ 今週 {_diff_text(w)}**",
        '  - プラスなら、主観が確定オッズより勝ち馬に高い確率を付けていた。'
        '平均が標準誤差の2倍を超えて続けば、偶然ではない差と見てよい。',
        f"  - 主観と市場の混ぜ方（改善案2）の検討は {READY_RACES} レースが目安"
        f"（いま {t['races']}）。",
        '',
        '### ◎の単勝オッズのずれ（確定オッズ ÷ 検算で見たオッズ・通算）',
        '',
        '1未満なら発走までに売れた。◎を軸にした買い目の期待値は、そのぶん検算時より低く着地する。',
        '',
        '| 検算の時刻 | 頭数 | 中央値 | 平均 | 下がった割合 |',
        '|---|---|---|---|---|',
    ]
    for label in [b[2] for b in BUCKETS] + ['最後の検算']:
        d = summary['drift'].get(label)
        if d:
            lines.append(f"| {label} | {d['n']} | {d['median']:.3f} | {d['mean']:.3f} | {d['down']:.0%} |")
        else:
            lines.append(f'| {label} | 0 | — | — | — |')
    lines.append('')
    return lines


def mail_line(summary):
    """メールに載せる1行。"""
    t = summary['total']
    last = summary['drift'].get('最後の検算')
    drift = f" ／ ◎のオッズ 最後の検算→確定 中央値{last['median']:.3f}" if last else ''
    return (f"主観 vs 確定オッズ（観察中）：{t['races']}/{READY_RACES}R、"
            f"1Rあたり {_diff_text(t)}{drift}")


if __name__ == '__main__':
    import review
    days = review.all_bet_days()
    print('\n'.join(render_lines(analyze(days, review.cached_result))))
