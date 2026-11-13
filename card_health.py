#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""カードの「欄の空き具合」を見て、ページの書き方の変化を早く知らせる。

2026-11-14、netkeiba が出馬表の書き方を変えていた（コースの括弧が消え、天候が
アイコンに移った）。読み取り側はエラーにならず空欄を返すだけだったので、
カードの turn・rail・weather は 10-31 から2週間、全レース null のまま誰も
気づかなかった。印付けの側は「カードに無いので要確認」と書いて先へ進んでいた。

**ふだん必ず埋まる欄**が、候補レースのほとんどで空なら「書き方が変わった疑い」
として知らせる。1頭・1レースの欠け（新馬・地方帰り・取得の失敗）は普通に
起きるので知らせない。半分以上が空のときだけ知らせる。

どの欄を「ふだん必ず埋まる」とするかは、2026-10-18〜11-14 の8枚のカードで
実測して決めた（docs/決定ログ.md 2026-11-14）。判断はしない。知らせるだけ。
"""

import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
CARDS_DIR = os.path.join(ROOT, 'data', 'cards')

# この割合より埋まっていなければ知らせる。
MIN_FILLED = 0.5


def _filled(value):
    return value not in (None, '', [], {})


def _cond(key):
    return lambda race: (race.get('conditions') or {}).get(key)


def _is_turf(race):
    surface = race.get('surface') or (race.get('conditions') or {}).get('surface')
    return surface in ('芝', 'turf')


# (キー, 表示名, 値の取り出し方, 対象に含めるか)
# 馬場は入れない。前夜は発表前のことがあり（10-31 は 2/6）、空でも書き方の変化とは
# 限らない。馬場は直前検算の側（CHECK_FIELDS）で見る。
RACE_FIELDS = [
    ('weather', '天候', _cond('weather'), None),
    ('turn', '回り', _cond('turn'), None),
    ('rail', 'コース区分（芝）', _cond('rail'), _is_turf),
    ('race_terms', '競走条件', _cond('race_terms'), None),
    ('odds', '前夜のオッズ', lambda r: r.get('odds'), None),
]


def _recent_any(key):
    return lambda e: any(_filled(run.get(key)) for run in (e.get('recent') or []))


ENTRY_FIELDS = [
    ('jockey', '騎手', lambda e: e.get('jockey'), None),
    ('kinryo', '斤量', lambda e: e.get('kinryo'), None),
    ('sex', '性別', lambda e: e.get('sex'), None),
    ('affiliation', '所属', lambda e: e.get('affiliation'), None),
    ('blinker', 'ブリンカー', lambda e: e.get('blinker'), None),
    ('oikiri', '追い切り', lambda e: e.get('oikiri'), None),
    ('recent', '近走', lambda e: e.get('recent'), None),
    ('recent.passing', '近走の通過順', _recent_any('passing'), None),
    ('recent.last3f', '近走の上がり', _recent_any('last3f'), None),
    ('pedigree', '血統', lambda e: e.get('pedigree'), None),
]


def _measure(items, fields):
    problems = []
    for key, label, get, applies in fields:
        pool = [item for item in items if applies is None or applies(item)]
        if not pool:
            continue
        filled = sum(1 for item in pool if _filled(get(item)))
        if filled / len(pool) < MIN_FILLED:
            problems.append({'key': key, 'label': label, 'filled': filled, 'total': len(pool)})
    return problems


def check(card):
    """カードを見て、ほとんど空の欄の一覧を返す。問題が無ければ空のリスト。"""
    races = [r for r in (card or {}).get('races') or [] if r.get('candidate')]
    if not races:
        return []
    problems = _measure(races, RACE_FIELDS)
    entries = [e for r in races for e in (r.get('entries') or [])]
    if entries:
        problems += _measure(entries, ENTRY_FIELDS)
    return problems


# 直前検算が出馬表から取り直す馬場・天候・回り（conditions.fetch）。中央だけ。
# 地方は nar.py が別の形で返す（回りを持たない）ので対象にしない。
# キーそのものが無い conditions（取得経路がその項目を持たない）は数えない。
# 「キーはあるのに空」＝読もうとして読めなかった、だけを数える。
CHECK_FIELDS = [
    ('going', '馬場（検算時）', lambda c: c.get('going'), lambda c: 'going' in c),
    ('weather', '天候（検算時）', lambda c: c.get('weather'), lambda c: 'weather' in c),
    ('turn', '回り（検算時）', lambda c: c.get('turn'), lambda c: 'turn' in c),
]


def check_conditions(conditions_list):
    """直前検算で取った中央の conditions の一覧を見る。取れなかった（None）レースは数えない。"""
    pool = [c for c in conditions_list if c]
    if not pool:
        return []
    return _measure(pool, CHECK_FIELDS)


def load(day):
    path = os.path.join(CARDS_DIR, f'{day.isoformat()}.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def format_problems(problems):
    """メール・ログ用の文面。問題が無ければ空のリスト。"""
    if not problems:
        return []
    lines = ['⚠ 読めていない欄があります（ページの書き方が変わった疑い）']
    for p in problems:
        state = '全部空' if p['filled'] == 0 else f"{p['filled']}/{p['total']}だけ"
        lines.append(f"  - {p['label']}（{p['key']}）：{state}（対象{p['total']}）")
    lines.append('  1つ2つの欠けではなく、ほとんどが空です。読み取りの正規表現が'
                 '今のページに合っているかを確かめてください（docs/決定ログ.md 2026-11-14）。')
    return lines


def subject_labels(problems):
    return '・'.join(p['label'] for p in problems[:4]) + ('ほか' if len(problems) > 4 else '')
