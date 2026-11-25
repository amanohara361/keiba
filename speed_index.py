#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自前のタイム指数（2026-11-25）。

netkeiba のタイム指数は有料で、無料ページには馬番1〜3の3頭しか出ない。
考え方は同じものを data_jra（中央の全レース結果）から作る。

指数 = 80 + 1000 × (想定の対数タイム − 実際の対数タイム) + 2 × (斤量 − 56)
- 想定の対数タイム = コース基準（場・芝ダ・距離）＋ その日の馬場差（日・場・芝ダ）＋ 1勝クラスの補正
- コース基準・馬場差・クラス差は、各レースの1〜3着の平均対数タイムに
  「コース＋馬場差＋クラス」を当てはめて交互に推定する（交互最小二乗）。
- 1000 × 対数の差 ＝ 0.1% 速いと1点。1600m（約96秒）なら1秒≒10点、1馬身（約0.17秒）≒2点。
- 斤量は1kg≒0.2秒（1600m）≒2点。
- 1勝クラスの1〜3着の平均がふつうの馬場で80になる。上のクラスほど高く出る（能力の差なので消さない）。

使わないもの：障害。コースのレースが3つ未満なら基準が定まらないので指数を出さない。

**注意**：コース基準とクラス差は全期間（後の日付を含む）で推定している。バックテストで
「その時点の指数」として使うと、基準の分だけ先の情報が混ざる（各馬の着順・タイムは混ざらない）。
"""
import glob
import json
import math
import os
import re
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
REF_CLASS = '1勝'
MIN_COURSE_RACES = 3
WEIGHT_POINTS = 2.0
WEIGHT_BASE = 56.0


def parse_time(text):
    m = re.match(r'^\s*(?:(\d+):)?(\d+(?:\.\d+)?)\s*$', str(text or ''))
    if not m:
        return None
    return int(m.group(1) or 0) * 60 + float(m.group(2))


def race_class(name):
    name = name or ''
    for key in ('新馬', '未勝利', '1勝', '2勝', '3勝'):
        if key in name:
            if key in ('新馬', '未勝利'):
                return ('2歳' if '2歳' in name else '3歳') + key
            return key
    return 'その他'  # 特別戦の名前だけでは1〜3勝・OP・重賞を見分けられない


def load_results(pattern=None):
    races = []
    for path in sorted(glob.glob(pattern or os.path.join(ROOT, 'data_jra', '*.json'))):
        venue = os.path.basename(path)[9:-5]
        with open(path, encoding='utf-8') as f:
            for race in json.load(f):
                if race.get('馬場') not in ('芝', 'ダ') or not race.get('距離'):
                    continue
                runs = []
                for h in race['horses']:
                    t = parse_time(h.get('タイム'))
                    if not t or not str(h.get('着順')).isdigit():
                        continue
                    try:
                        kg = float(h.get('斤量'))
                    except (TypeError, ValueError):
                        kg = WEIGHT_BASE
                    runs.append({'name': h['馬名'], 'umaban': h['馬番'], 'finish': int(h['着順']),
                                 'time': t, 'kg': kg, 'odds': _float(h.get('単勝')),
                                 'pop': _int(h.get('人気'))})
                if len(runs) < 3:
                    continue
                races.append({'race_id': race['race_id'], 'date': os.path.basename(path)[:8],
                              'venue': venue, 'surface': race['馬場'], 'distance': race['距離'],
                              'class': race_class(race.get('レース名')), 'runs': runs})
    return races


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def course_key(race):
    return (race['venue'], race['surface'], race['distance'])


def day_key(race):
    return (race['date'], race['venue'], race['surface'])


def fit(races, iterations=30):
    """コース基準 C・馬場差 V・クラス差 K を推定する。"""
    obs = []
    for race in races:
        top = sorted(race['runs'], key=lambda r: r['finish'])[:3]
        y = sum(math.log(r['time']) for r in top) / len(top)
        obs.append((course_key(race), day_key(race), race['class'], y))
    C = defaultdict(float)
    V = defaultdict(float)
    K = defaultdict(float)
    for _ in range(iterations):
        for table, idx in ((C, 0), (V, 1), (K, 2)):
            acc = defaultdict(list)
            for o in obs:
                rest = o[3] - sum(t[o[i]] for t, i in ((C, 0), (V, 1), (K, 2)) if t is not table)
                acc[o[idx]].append(rest)
            for key, vals in acc.items():
                table[key] = sum(vals) / len(vals)
        ref = K[REF_CLASS]
        for key in list(K):
            K[key] -= ref
        for key in list(C):
            C[key] += ref
    counts = defaultdict(int)
    for o in obs:
        counts[o[0]] += 1
    return {'C': dict(C), 'V': dict(V), 'K': dict(K), 'course_races': dict(counts)}


def index_runs(races, model=None):
    """各走に 'index' を付けて返す（基準が定まらないコースは None）。"""
    model = model or fit(races)
    for race in races:
        ck = course_key(race)
        ok = model['course_races'].get(ck, 0) >= MIN_COURSE_RACES
        expected = model['C'].get(ck, 0) + model['V'].get(day_key(race), 0)
        for run in race['runs']:
            run['index'] = (round(80 + 1000 * (expected - math.log(run['time']))
                                  + WEIGHT_POINTS * (run['kg'] - WEIGHT_BASE), 1) if ok else None)
    return races


def history(races):
    """馬名 → [(日付, 芝ダ, 指数, race_id)]（日付順）。"""
    hist = defaultdict(list)
    for race in sorted(races, key=lambda r: r['date']):
        for run in race['runs']:
            if run.get('index') is not None:
                hist[run['name']].append((race['date'], race['surface'], run['index'], race['race_id']))
    return hist


if __name__ == '__main__':
    rs = index_runs(load_results())
    model = fit(rs)
    print('クラス差（1000×対数）:', {k: round(v * 1000, 1) for k, v in sorted(model['K'].items(), key=lambda x: x[1])})
    import statistics
    for cls in sorted({r['class'] for r in rs}):
        vals = [run['index'] for r in rs if r['class'] == cls for run in r['runs'] if run['finish'] == 1 and run['index'] is not None]
        if vals:
            print(cls, len(vals), '勝ち馬の指数 中央値', statistics.median(vals))
