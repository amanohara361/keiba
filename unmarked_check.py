#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""無印にした理由（`unmarked`）を、カードの近走と突き合わせる（2026-11-11 ユーザー承認）。

朝の予想が「前走の負け」を理由に馬を無印にするとき、着差の数字・馬場・距離を
見ないまま切る形が続いた。検証ノートの注意点に2度昇格させても止まらず、
2026-11-09週の深掘り10レースのうち8件がこの形だった（AR共和国杯の6＝稍重の1.0秒差、
錦秋Sの8＝差し決着を先行しての負け、道営スプリントの13＝「逃げて崩れ」の一言など）。

ここでは判断はしない。朝の予想が書いた `unmarked[].loss_date` の走りをカードから引き、
着差・馬場・芝ダ・距離を今回と並べて、次に当たれば知らせるだけ。

  - 着差が1.5秒未満（予想メソッド第2章(a)の基準に届かない）
  - 芝ダ・距離（400m以上）・馬場（良と道悪）のどれかが今回と違う（第9章）
  - 負けを理由に書いているのに `loss_date` が無い

`make_bets.py --show`（朝の push 前の自己点検）が呼ぶ。知らせるだけで、push は止めない。
地方のカードの着差は「前の馬との差」（馬身）なので、勝ち馬との差は2着のときしか出せない。

外部ライブラリは使わない。
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CARDS_DIR = os.path.join(HERE, 'data', 'cards')

MARGIN_LIMIT = 1.5      # 第2章(a)：勝ち馬から1.5秒以上
DISTANCE_GAP = 400      # 距離が違うとみなす差（北野特別：1600m の負けで2000m を消した）

# reason に負けを書いているかの目安。「着差」「前走1着」なども拾うが、
# 知らせるだけなので取りこぼしより拾いすぎを選ぶ。
LOSS_WORDS = re.compile(r'大敗|崩れ|凡走|負け|敗|\d+着|着差|秒差|\d\.\d差')


def _digits(date_text):
    return re.sub(r'\D', '', str(date_text or ''))


def _surface(text):
    if not text:
        return None
    return '芝' if '芝' in text else ('ダ' if 'ダ' in text else text)


def _going_group(text):
    if not text:
        return None
    return '良' if text == '良' else '道悪'


def load_card_race(day, race_id, org='jra'):
    """その日のカードから race_id のレースを返す。無ければ None。"""
    sub = ('nar',) if org == 'nar' else ()
    path = os.path.join(CARDS_DIR, *sub, f'{day}.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        card = json.load(f)
    return next((r for r in card.get('races', []) if str(r.get('race_id')) == str(race_id)), None)


def today_conditions(card_race):
    """今回の芝ダ・距離・馬場。馬場は朝は未発表のことがある（None）。"""
    if card_race is None:
        return {}
    cond = card_race.get('conditions') or {}
    return {
        'surface': _surface(card_race.get('surface') or cond.get('surface')),
        'distance': card_race.get('distance') or cond.get('distance'),
        'going': cond.get('going') or card_race.get('going'),
    }


def runs_of(entry):
    """カードの近走を、中央・地方で同じ形にそろえる。"""
    out = []
    for r in entry.get('recent') or []:            # 中央
        try:
            margin = float(r.get('margin'))
        except (TypeError, ValueError):
            margin = None
        out.append({
            'date': _digits(r.get('date')), 'race': r.get('race', ''),
            'rank': r.get('rank'), 'margin': margin, 'margin_text': r.get('margin'),
            'going': r.get('going'), 'surface': _surface(r.get('surface')),
            'distance': r.get('distance'), 'passing': r.get('passing'),
        })
    for r in entry.get('recent_runs') or []:       # 地方
        out.append({
            'date': _digits(r.get('date')), 'race': r.get('race_name', ''),
            'rank': r.get('finish'), 'margin': None,
            # 地方の着差は前の馬との差。2着なら勝ち馬との差になる。
            'margin_text': (None if not r.get('margin') else
                            (f"勝ち馬と{r['margin']}" if r.get('finish') == 2
                             else f"前の馬と{r['margin']}")),
            'going': r.get('going'), 'surface': _surface(r.get('surface')),
            'distance': r.get('distance'),
            'passing': '-'.join(str(c) for c in r.get('corners') or []) or None,
        })
    return out


def describe(run):
    bits = [f"{run['date'][:4]}/{run['date'][4:6]}/{run['date'][6:]}", run['race'] or '']
    if run['rank'] is not None:
        bits.append(f"{run['rank']}着")
    if run['margin'] is not None:
        bits.append(f"{run['margin']:.1f}秒差")
    elif run['margin_text']:
        bits.append(run['margin_text'])
    bits.append(f"{run['going'] or '馬場?'} {run['surface'] or ''}{run['distance'] or ''}m")
    if run['passing']:
        bits.append(f"通過{run['passing']}")
    return ' '.join(b for b in bits if b)


def loss_flags(run, today):
    """その負けで切ってよいかを疑う点。空なら知らせることは無い。"""
    flags = []
    if run['margin'] is not None and run['margin'] < MARGIN_LIMIT:
        flags.append(f'着差{run["margin"]:.1f}秒は1.5秒未満（第2章(a)の基準に届かない）')
    if run['margin'] is None and run['rank'] == 2 and '大差' not in (run['margin_text'] or ''):
        flags.append('2着で、大敗ではない')
    if today.get('surface') and run['surface'] and run['surface'] != today['surface']:
        flags.append(f'芝ダが違う（{run["surface"]}→今回{today["surface"]}）')
    if today.get('distance') and run['distance'] and \
            abs(int(run['distance']) - int(today['distance'])) >= DISTANCE_GAP:
        flags.append(f'距離が違う（{run["distance"]}m→今回{today["distance"]}m）')
    if _going_group(today.get('going')) and _going_group(run['going']) and \
            _going_group(run['going']) != _going_group(today['going']):
        flags.append(f'馬場が違う（{run["going"]}→今回{today["going"]}）')
    return flags


def check_race(race, card_race):
    """[(馬番 or None, 知らせる文), ...] を返す。"""
    notes = []
    if not race.marks:
        return notes
    if not race.unmarked:
        notes.append((None, 'unmarked（無印にした理由）がありません。'
                            '個別に吟味して無印にした馬を書いてください（docs/朝タスク手順.md「無印にした理由を書く」）'))
        return notes

    kept = race.marked_horses | {p['umaban'] for p in race.partners}
    entries = {e.get('umaban'): e for e in (card_race or {}).get('entries') or []}
    today = today_conditions(card_race)

    for u in race.unmarked:
        num = u['umaban']
        if num in kept:
            notes.append((num, '印か partners にも入っています。unmarked から外してください'))
            continue
        loss_date = _digits(u.get('loss_date'))
        if not loss_date:
            if LOSS_WORDS.search(u.get('reason', '')):
                notes.append((num, '負けを理由にしているのに loss_date がありません。'
                                   'どの走りの負けかを日付で書いてください'))
            continue
        if card_race is None:
            notes.append((num, 'カードが見つからず、loss_date の走りを確かめられません'))
            continue
        runs = runs_of(entries.get(num) or {})
        run = next((r for r in runs if r['date'] == loss_date), None)
        if run is None:
            have = '・'.join(r['date'] for r in runs) or 'なし'
            notes.append((num, f'loss_date {u["loss_date"]} の走りがカードの近走にありません（近走：{have}）'))
            continue
        flags = loss_flags(run, today)
        text = describe(run)
        if not flags and run['margin'] is None and run['rank'] not in (None, 1):
            notes.append((num, f'{text} → 勝ち馬との差がカードから出せません'
                               '（地方は前の馬との差）。着差を確かめてから切ってください'))
        elif flags:
            notes.append((num, f'{text} → ' + '／'.join(flags)
                          + '。この負けだけでは切らない（検証ノート注意点 2026-10-27・11-03）。'
                            'partners に残すか、それでも切る理由を reason に足してください'))
        else:
            notes.append((num, f'{text}（基準どおりの負け）'))
    return notes
