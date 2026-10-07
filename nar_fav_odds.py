#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""地方の1番人気を確定単勝オッズで分け、勝率・回収率を比べる。

## なぜこれがあるか

「地方の1番人気でも、オッズが2倍以上だと勝ちを取り逃す印象がある」という
ユーザーの疑問を確かめた（2026-10-07、`docs/調査/地方1番人気のオッズ別成績.md`）。
同じ集計を月が増えるたびにやり直せるよう、取得と集計を分けて残す。

**数字を出すだけで、基準もロジックも変えない。**

## 公式の確定オッズは約8か月分しか残らない

keiba.go.jp の月次オッズ（OddsDataDownload）は、2026-10-07 時点で
2026年2月分より前が空のZIP（661バイト）になっていた。結果（RaceDataDownload）は
2023年まで残っているが、オッズが消えると「1番人気のオッズ」は二度と復元できない。
だから1レース1行に縮めた集計用の表を `data/nar_fav/YYYYMM.csv` に保存して、
リポジトリに残す（月1回 `fetch` すれば足りる）。

使い方:
  python nar_fav_odds.py fetch 202609            # 1か月分を取得して data/nar_fav/ へ
  python nar_fav_odds.py fetch 202602 202609     # 範囲で取得
  python nar_fav_odds.py report                  # 保存済みの全月を集計
  python nar_fav_odds.py report 202606 202609    # 範囲を絞って集計
"""
import csv
import io
import math
import os
import sys
import time
import urllib.request
import zipfile
from collections import Counter

import nar

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, 'data', 'nar_fav')

FIELDS = ['venue', 'date', 'race_no', 'runners', 'umaban', 'odds', 'finish', 'place_pay']

# 地方の単勝の払戻率。オッズを「市場が見込む勝率」に直すときに使う
# （bet_builder と同じく一律で扱う。主催者ごとの差は確かめていない）。
WIN_PAYOUT = 0.8
THRESHOLD = 2.0
BANEI = '帯広ば'


# ----------------------------------------------------------------------
# 取得
# ----------------------------------------------------------------------

def _download(kind, year, month):
    url = nar.BASE.format(kind=kind) + f'?type=monthly&k_year={year}&k_month={month}'
    req = urllib.request.Request(url, headers={'User-Agent': nar.UA})
    with urllib.request.urlopen(req, timeout=nar.TIMEOUT) as res:
        return res.read()


def _csv_rows(zf, suffix):
    for name in zf.namelist():
        if name.lower().endswith(suffix):
            text = zf.read(name).decode('utf-8-sig', errors='replace')
            yield from csv.DictReader(io.StringIO(text))


def win_odds_rows(odds_zip):
    """オッズZIPから単勝の行だけを取り出す。3連単まで丸ごと辞書にすると
    1か月で百万行を超えるので、行頭の列だけ見て単勝以外は捨てる。"""
    with zipfile.ZipFile(io.BytesIO(odds_zip)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith('_odds.csv'):
                continue
            with zf.open(name) as fh:
                reader = csv.reader(io.TextIOWrapper(fh, encoding='utf-8-sig'))
                header = next(reader, None)
                if not header:
                    continue
                for r in reader:
                    if len(r) >= len(header) and r[3] == '単勝':
                        yield dict(zip(header, r))


def _int(v):
    v = (v or '').strip().replace(',', '')
    return int(v) if v.isdigit() else None


def build_rows(win_rows, horse_rows, payback_rows):
    """1レース1行（1番人気1頭）の表を作る。戻り値は (rows, 除外の内訳)。

    除外するのは、1番人気が同率で複数・取消等で着順が無い・完走5頭未満・
    オッズが無いレース。**推測で埋めない**（例：同率1番人気のどちらかを選ばない）。
    """
    odds = {}
    for r in win_rows:
        try:
            key = (r['競馬場'], r['競走年月日'], int(r['レース番号']), int(r['番号1']))
            odds[key] = (float(r['オッズ']), _int(r['人気']))
        except (KeyError, ValueError):
            continue
    races = {}
    for r in horse_rows:
        k = (r['競馬場'], r['競走年月日'], _int(r['レース番号']))
        races.setdefault(k, []).append(r)
    pay = {(r['競馬場'], r['競走年月日'], _int(r['レース番号'])): r for r in payback_rows}

    rows, skip = [], Counter()
    for k in sorted(races):
        runners = []
        for h in races[k]:
            o = odds.get(k + (_int(h['馬番']),))
            if o:
                runners.append((o[0], o[1], _int(h['馬番']), _int(h['着順'])))
        if not runners:
            skip['オッズ無し'] += 1
            continue
        if not any(x[3] for x in runners):
            skip['結果未確定'] += 1
            continue
        favs = [x for x in runners if x[1] == 1]
        if len(favs) != 1:
            skip['1番人気が同率で複数・不明'] += 1
            continue
        o, _, ban, fin = favs[0]
        if fin is None:
            skip['1番人気が取消・中止等'] += 1
            continue
        finished = sum(1 for x in runners if x[3])
        if finished < 5:
            skip['完走5頭未満'] += 1
            continue
        place_pay = 0
        p = pay.get(k) or {}
        for i in (1, 2, 3):
            if _int(p.get(f'複勝組番{i}')) == ban:
                place_pay = _int(p.get(f'複勝払戻金{i}（円）')) or 0
        rows.append({'venue': k[0], 'date': k[1], 'race_no': k[2], 'runners': finished,
                     'umaban': ban, 'odds': o, 'finish': fin, 'place_pay': place_pay})
    return rows, skip


def fetch_month(ym):
    year, month = int(ym[:4]), int(ym[4:])
    odds_zip = _download('Odds', year, month)
    time.sleep(2)
    race_zip = _download('Race', year, month)
    win = list(win_odds_rows(odds_zip))
    if not win:
        raise nar.NarError(f'{ym} の確定オッズが公式に残っていません（{len(odds_zip)}バイト）')
    with zipfile.ZipFile(io.BytesIO(race_zip)) as zf:
        rows, skip = build_rows(win, _csv_rows(zf, '_horselist.csv'),
                                _csv_rows(zf, '_payback.csv'))
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f'{ym}.csv')
    with open(path, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f'{ym}: {len(rows)}レース → {os.path.relpath(path, HERE)}  除外 {dict(skip)}')


# ----------------------------------------------------------------------
# 集計
# ----------------------------------------------------------------------

def load(ym_from=None, ym_to=None):
    rows = []
    if not os.path.isdir(OUT_DIR):
        return rows
    for f in sorted(os.listdir(OUT_DIR)):
        ym = f[:6]
        if not f.endswith('.csv') or (ym_from and ym < ym_from) or (ym_to and ym > ym_to):
            continue
        with open(os.path.join(OUT_DIR, f), encoding='utf-8') as fh:
            for r in csv.DictReader(fh):
                rows.append({**r, 'race_no': int(r['race_no']), 'runners': int(r['runners']),
                             'odds': float(r['odds']), 'finish': int(r['finish']),
                             'place_pay': int(r['place_pay'])})
    return rows


def p_two_sided(z):
    return math.erfc(abs(z) / math.sqrt(2))


def two_prop_z(x1, n1, x2, n2):
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (x1 / n1 - x2 / n2) / se if se else 0.0


def stats(rs):
    n = len(rs)
    return {
        'n': n,
        'win': sum(r['finish'] == 1 for r in rs),
        'top2': sum(r['finish'] <= 2 for r in rs),
        'top3': sum(r['finish'] <= 3 for r in rs),
        'market': sum(WIN_PAYOUT / r['odds'] for r in rs) / n,
        'win_roi': sum(r['odds'] for r in rs if r['finish'] == 1) / n,
        'place_roi': sum(r['place_pay'] for r in rs) / (n * 100),
    }


def chi2_sf(x, df):
    """カイ二乗分布の上側確率（Wilson-Hilferty近似。標準ライブラリだけで済ませる）。"""
    if df <= 0:
        return 1.0
    z = ((x / df) ** (1 / 3) - (1 - 2 / (9 * df))) / math.sqrt(2 / (9 * df))
    return 0.5 * math.erfc(z / math.sqrt(2))


def venue_heterogeneity(rs):
    """「実際の勝ち数 − 市場想定の勝ち数」が場によって違うかの検定。"""
    chi, k = 0.0, 0
    for v in sorted({r['venue'] for r in rs}):
        s = [r for r in rs if r['venue'] == v]
        e = sum(WIN_PAYOUT / r['odds'] for r in s)
        var = sum((WIN_PAYOUT / r['odds']) * (1 - WIN_PAYOUT / r['odds']) for r in s)
        chi += (sum(r['finish'] == 1 for r in s) - e) ** 2 / var
        k += 1
    return chi, k, chi2_sf(chi, k)


def _line(label, s):
    return (f'{label:<10} n={s["n"]:>5}  勝率{s["win"]/s["n"]:6.1%}  連対{s["top2"]/s["n"]:6.1%}  '
            f'3着内{s["top3"]/s["n"]:6.1%}  市場想定勝率{s["market"]:6.1%}  '
            f'単回収{s["win_roi"]:6.1%}  複回収{s["place_roi"]:6.1%}')


def report(rows):
    out = []
    flat = [r for r in rows if r['venue'] != BANEI]
    if not flat:
        return 'データがありません（先に fetch してください）'
    dates = sorted(r['date'] for r in flat)
    out.append(f'対象 {dates[0]}〜{dates[-1]}（ばんえい除く）')
    lo = [r for r in flat if r['odds'] < THRESHOLD]
    hi = [r for r in flat if r['odds'] >= THRESHOLD]
    if lo and hi:
        a, b = stats(lo), stats(hi)
        out += [_line('2倍未満', a), _line('2倍以上', b)]
        for key, name in (('win', '勝率'), ('top2', '連対率'), ('top3', '3着内率')):
            z = two_prop_z(a[key], a['n'], b[key], b['n'])
            out.append(f'  {name}の差 z={z:.2f} p={p_two_sided(z):.2g}')
    out.append('\nオッズ帯別')
    for lo_, hi_ in ((1.0, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 3.0), (3.0, 99.0)):
        s = [r for r in flat if lo_ <= r['odds'] < hi_]
        if s:
            out.append(_line(f'{lo_}-{hi_ if hi_ < 99 else ""}倍', stats(s)))
    out.append('\n場ごとの「実勝率と市場想定勝率のずれ」に差があるか')
    for name, grp in (('2倍未満', lo), ('2倍以上', hi)):
        if grp:
            chi, k, p = venue_heterogeneity(grp)
            out.append(f'  {name}: chi2={chi:.1f} df={k} p≈{p:.2g}')
    return '\n'.join(out)


def _months(a, b):
    y, m = int(a[:4]), int(a[4:])
    while f'{y}{m:02d}' <= b:
        yield f'{y}{m:02d}'
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ('fetch', 'report'):
        print(__doc__)
        return 2
    if argv[0] == 'fetch':
        a = argv[1]
        b = argv[2] if len(argv) > 2 else a
        for i, ym in enumerate(_months(a, b)):
            if i:
                time.sleep(2)
            fetch_month(ym)
        return 0
    print(report(load(*argv[1:3])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
