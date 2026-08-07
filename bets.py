#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""買い目データの契約（スキーマ）と読み書き。

このファイルが定める JSON 形式が、予想する側と検算する側の唯一の接点である。

    予想する側              →  data/bets/YYYY-MM-DD.json  →  検算する側
    （いまは Cowork の朝タスク）                              （check.py・GitHub Actions）

**完全自動化を見据えた設計**：検算側はこの JSON しか見ない。将来 Claude API が
印を打つようになっても、同じ形を吐きさえすれば check.py 側は一切変更しなくてよい。
`source` にどこが作ったかを記録しておき、あとで精度を比較できるようにしている。

外部ライブラリは使わない（jra_bias.py と同じ方針。Python 3.x だけで動く）。
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def now_jst():
    return datetime.now(JST)

ROOT = os.path.dirname(os.path.abspath(__file__))
BETS_DIR = os.path.join(ROOT, 'data', 'bets')
CHECKS_DIR = os.path.join(ROOT, 'data', 'checks')

# 予想メソッド 第13章で扱う券種。jra_bias.py の TYPE_NAMES と対応させる。
BET_TYPES = {
    '単勝': '1',
    '複勝': '2',
    '馬連': '4',
    'ワイド': '5',
    '馬単': '6',
    '3連複': '7',
}

# 印の序列。第13章F案の「上位印／下位印」の判定に使う。
MARK_ORDER = ['◎', '○', '▲', '△']
SENIOR_MARKS = ['◎', '○']   # これが相手から外れたら矛盾を疑う
JUNIOR_MARKS = ['▲', '△']

CONFIDENCE_LEVELS = ['A', 'B', 'C']


class BetsError(ValueError):
    """買い目ファイルの形式が契約に合っていない。"""


class Bet:
    """1点の買い目。"""

    def __init__(self, bet_type, horses, stake=100):
        if bet_type not in BET_TYPES:
            raise BetsError(f'知らない券種です: {bet_type}（{"・".join(BET_TYPES)} のいずれか）')
        self.type = bet_type
        self.horses = [int(h) for h in horses]
        self.stake = int(stake)
        if not self.horses:
            raise BetsError(f'{bet_type} に馬番がありません')

    @property
    def combination(self):
        return '-'.join(str(h) for h in self.horses)

    def __repr__(self):
        return f'{self.type} {self.combination}'

    def to_dict(self):
        return {'type': self.type, 'horses': self.horses, 'stake': self.stake}


class RaceBets:
    """1レース分の印・勝負度・買い目。"""

    def __init__(self, race_id, name, start_time, marks, bets,
                 confidence='B', subjective_hit_rate=None, venue=None, race_no=None,
                 note=''):
        self.race_id = str(race_id)
        self.name = name
        self.start_time = start_time          # "15:25"
        self.marks = marks                    # [{'mark': '◎', 'umaban': 7}, ...]
        self.bets = bets                      # [Bet, ...]
        self.confidence = confidence          # 勝負度 A/B/C
        self.subjective_hit_rate = subjective_hit_rate   # 期待値の計算に使う（0.0〜1.0）
        self.venue = venue
        self.race_no = race_no
        self.note = note

        if confidence not in CONFIDENCE_LEVELS:
            raise BetsError(f'勝負度は A/B/C のいずれかです: {confidence}')
        if not re.fullmatch(r'\d{12}', self.race_id):
            raise BetsError(f'race_id は12桁の数字です: {race_id}')

    # --- 印の参照 ---

    def horses_for(self, mark):
        """その印がついた馬番のリスト（△は複数ありうる）。"""
        return [m['umaban'] for m in self.marks if m['mark'] == mark]

    def mark_of(self, umaban):
        """馬番から印を引く。印が無ければ None（＝無印）。"""
        for m in self.marks:
            if m['umaban'] == umaban:
                return m['mark']
        return None

    @property
    def marked_horses(self):
        return {m['umaban'] for m in self.marks}

    @property
    def horses_in_bets(self):
        """買い目に1度でも登場する馬番。"""
        return {h for bet in self.bets for h in bet.horses}

    def start_datetime(self, day):
        """発走時刻を JST の datetime にする。時刻が無ければ None。"""
        if not self.start_time:
            return None
        try:
            hour, minute = (int(x) for x in self.start_time.split(':'))
        except ValueError:
            return None
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=JST)

    def to_dict(self):
        return {
            'race_id': self.race_id,
            'name': self.name,
            'venue': self.venue,
            'race_no': self.race_no,
            'start_time': self.start_time,
            'confidence': self.confidence,
            'subjective_hit_rate': self.subjective_hit_rate,
            'marks': self.marks,
            'bets': [b.to_dict() for b in self.bets],
            'note': self.note,
        }


class BetSheet:
    """1日分の買い目ファイル全体。"""

    def __init__(self, date, races, generated_at=None, source='cowork'):
        self.date = date                      # datetime.date
        self.races = races
        self.generated_at = generated_at
        self.source = source

    def to_dict(self):
        return {
            'date': self.date.isoformat(),
            'generated_at': self.generated_at,
            'source': self.source,
            'races': [r.to_dict() for r in self.races],
        }


# ----------------------------------------------------------------------
# 読み込み
# ----------------------------------------------------------------------

def _require(obj, key, context):
    if key not in obj:
        raise BetsError(f'{context} に "{key}" がありません')
    return obj[key]


def parse_sheet(payload):
    """辞書を BetSheet にする。契約違反はここで全部はじく。"""
    from datetime import date as _date

    raw_date = _require(payload, 'date', '買い目ファイル')
    try:
        day = _date.fromisoformat(raw_date)
    except ValueError:
        raise BetsError(f'date は YYYY-MM-DD 形式です: {raw_date}')

    races = []
    for i, raw in enumerate(_require(payload, 'races', '買い目ファイル'), 1):
        context = f'{i}件目のレース'
        marks = []
        for m in raw.get('marks', []):
            mark = _require(m, 'mark', f'{context}の印')
            if mark not in MARK_ORDER:
                raise BetsError(f'{context}: 知らない印です {mark}（{"".join(MARK_ORDER)} のいずれか）')
            marks.append({'mark': mark, 'umaban': int(_require(m, 'umaban', f'{context}の印'))})

        bets = [
            Bet(_require(b, 'type', f'{context}の買い目'),
                _require(b, 'horses', f'{context}の買い目'),
                b.get('stake', 100))
            for b in raw.get('bets', [])
        ]

        races.append(RaceBets(
            race_id=_require(raw, 'race_id', context),
            name=raw.get('name', ''),
            start_time=raw.get('start_time'),
            marks=marks,
            bets=bets,
            confidence=raw.get('confidence', 'B'),
            subjective_hit_rate=raw.get('subjective_hit_rate'),
            venue=raw.get('venue'),
            race_no=raw.get('race_no'),
            note=raw.get('note', ''),
        ))

    return BetSheet(
        date=day,
        races=races,
        generated_at=payload.get('generated_at'),
        source=payload.get('source', 'cowork'),
    )


def sheet_path(day):
    return os.path.join(BETS_DIR, f'{day.isoformat()}.json')


def load_sheet(day):
    """その日の買い目ファイルを読む。無ければ None（＝朝タスクが届いていない）。"""
    path = sheet_path(day)
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return parse_sheet(json.load(f))


def save_sheet(sheet):
    path = sheet_path(sheet.date)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(sheet.to_dict(), f, ensure_ascii=False, indent=2)
        f.write('\n')
    return path


def save_check_result(day, payload):
    """検算の記録を残す。あとで「規律が実際に効いたか」を検証するため。"""
    path = os.path.join(CHECKS_DIR, f'{day.isoformat()}.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    history = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            history = json.load(f)
    history.append(payload)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return path
