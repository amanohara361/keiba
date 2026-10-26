#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""過去のJRA・GⅠを集め、「前走がどこだったか」などの要因ごとに成績を出す。

レースごとに10年分を見ても延べ160頭ほどで、傾向というには母数が足りない
（予想メソッド 第4章「母数の少ない傾向は採用しない」）。そこで要因ごとに
全GⅠを横断して数え、人気をそろえて比べる（人気別の基準値に対する倍率）。
人気馬ほど来るのは当たり前なので、勝率そのものではなく倍率で読む。

使い方:
  python trends.py collect 2016 2025   # data/trends/races/ に1レース1ファイルで保存（途中再開できる）
  python trends.py summary             # data/trends/summary.md を作る

経緯：docs/決定ログ.md「要因別の過去データ（2026-10-26）」
外部ライブラリは使わない。
"""

import datetime
import glob
import json
import os
import re
import sys
import time

import jra_bias

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRENDS_DIR = os.path.join(BASE_DIR, 'data', 'trends')
RACES_DIR = os.path.join(TRENDS_DIR, 'races')

CALENDAR_URL = 'https://race.netkeiba.com/top/calendar.html?year={year}&month={month}'
DAY_LIST_URL = 'https://db.netkeiba.com/race/list/{date}/'
RACE_URL = 'https://db.netkeiba.com/race/{race_id}/'
HORSE_RESULT_URL = 'https://db.netkeiba.com/horse/result/{horse_id}/'

# サーバに負荷をかけない（1リクエストごとに待つ）
WAIT_SECONDS = 1.0

# 人気の区切り。人気別の基準値はこの区分ごとに出す。
NINKI_BUCKETS = [(1, 1), (2, 2), (3, 3), (4, 5), (6, 9), (10, 99)]

# 前走からの間隔（週）
INTERVAL_BUCKETS = [(0, 8, '8週以内'), (9, 12, '9〜12週'), (13, 25, '13〜25週'), (26, 9999, '26週以上')]


# ---------------- 分類 ----------------

def venue_kind(race_id):
    """race_id の5〜6文字目（場コード）で中央・地方・海外を分ける。

    中央は 01〜10、地方は 30 以降の数字、海外は英字を含む（例 2026A0010105 アスコット）。
    """
    code = race_id[4:6]
    if not code.isdigit():
        return 'overseas'
    return 'jra' if 1 <= int(code) <= 10 else 'nar'


def is_jra_g1(race_id, title):
    """平地の中央GⅠか。障害（J.GI）は除く。"""
    return venue_kind(race_id) == 'jra' and title.endswith('(GI)') and 'J.G' not in title


def ninki_bucket(ninki):
    for lo, hi in NINKI_BUCKETS:
        if lo <= ninki <= hi:
            return f'{lo}' if lo == hi else f'{lo}-{hi}'
    return None


def interval_bucket(days):
    if days is None:
        return None
    weeks = days // 7
    for lo, hi, label in INTERVAL_BUCKETS:
        if lo <= weeks <= hi:
            return label
    return None


# ---------------- パース ----------------

def parse_kaisai_dates(page):
    """race.netkeiba.com のカレンダーから開催日（YYYYMMDD）を拾う。"""
    return sorted(set(re.findall(r'kaisai_date=(\d{8})', page)))


def parse_day_races(page):
    """db.netkeiba.com の日別一覧から [(race_id, レース名), ...]。"""
    return [(rid, jra_bias.strip_tags(title))
            for rid, title in re.findall(r'/race/(\w{12})/" title="([^"]*)"', page)]


def parse_race_runners(page):
    """GⅠの結果ページから出走馬を読む。取消・除外（人気が付かない馬）は数えない。

    着順が数字でない（中止・失格）馬は rank=None として「着外」に数える。
    """
    table = jra_bias.find_result_table(page)
    if not table:
        return []
    trs = re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.S)
    if len(trs) < 2:
        return []
    header = [jra_bias.strip_tags(c) for c in re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', trs[0], re.S)]
    index = jra_bias.col_index(header)
    ninki_col = next((i for i, h in enumerate(header) if re.sub(r'\s+', '', h) == '人気'), None)
    if ninki_col is None or '着順' not in index:
        return []

    runners = []
    for tr in trs[1:]:
        horse = re.search(r'/horse/(\d{10})/', tr)
        cells = [jra_bias.strip_tags(c) for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
        if not horse or len(cells) <= ninki_col:
            continue
        ninki = cells[ninki_col].strip()
        if not ninki.isdigit():
            continue
        rank = re.match(r'\d+', cells[index['着順']].strip())
        runners.append({
            'horse_id': horse.group(1),
            'name': cells[index['馬名']] if '馬名' in index else '',
            'ninki': int(ninki),
            'rank': int(rank.group()) if rank else None,
        })
    return runners


def parse_previous_race(page, race_id):
    """馬の競走成績ページから、race_id の1つ前（＝前走）を返す。無ければ None。

    成績表は新しい順に並ぶので、対象レースの次の行が前走になる。
    """
    rows = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', page, re.S):
        rid = re.search(r'db\.netkeiba\.com/race/(\w{12})/', tr)
        cells = [jra_bias.strip_tags(c) for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
        if rid and cells and re.match(r'\d{4}/\d{2}/\d{2}', cells[0]):
            rows.append((rid.group(1), cells))

    for i, (rid, cells) in enumerate(rows):
        if rid != race_id:
            continue
        if i + 1 >= len(rows):
            return None
        prev_id, prev = rows[i + 1]
        days = (_date(cells[0]) - _date(prev[0])).days
        return {
            'race_id': prev_id,
            'date': prev[0],
            'venue': prev[1],
            'race_name': prev[4] if len(prev) > 4 else '',
            'kind': venue_kind(prev_id),
            'days': days,
        }
    return None


def _date(text):
    return datetime.datetime.strptime(text[:10], '%Y/%m/%d').date()


# ---------------- 収集 ----------------

class Fetcher:
    """待ち時間を挟んで取りに行く。同じURLは1回の実行で1度だけ取る。"""

    def __init__(self, wait=WAIT_SECONDS):
        self.wait = wait
        self.cache = {}

    def get(self, url, utf8=False):
        if url not in self.cache:
            fetch = jra_bias.fetch_html_utf8 if utf8 else jra_bias.fetch_html
            # 連続して取ると時々 400 が返る。間を空けて取り直せば通る。
            for retry_wait in (5, 20, 60, None):
                time.sleep(self.wait)
                try:
                    self.cache[url] = fetch(url)
                    break
                except OSError as e:
                    if retry_wait is None:
                        raise
                    print(f'  取り直し（{e}）: {url}', flush=True)
                    time.sleep(retry_wait)
        return self.cache[url]


def g1_races_of_year(year, fetcher):
    races = []
    for month in range(1, 13):
        calendar = fetcher.get(CALENDAR_URL.format(year=year, month=month), utf8=True)
        for date in parse_kaisai_dates(calendar):
            if not date.startswith(f'{year}{month:02d}'):
                continue
            for race_id, title in parse_day_races(fetcher.get(DAY_LIST_URL.format(date=date))):
                if is_jra_g1(race_id, title):
                    races.append({'race_id': race_id, 'date': date,
                                  'race_name': re.sub(r'\(GI\)$', '', title)})
    return races


def collect_race(race, fetcher):
    runners = parse_race_runners(fetcher.get(RACE_URL.format(race_id=race['race_id'])))
    for r in runners:
        page = fetcher.get(HORSE_RESULT_URL.format(horse_id=r['horse_id']))
        r['prev'] = parse_previous_race(page, race['race_id'])
    return dict(race, runners=runners)


def collect(year_from, year_to, fetcher=None):
    fetcher = fetcher or Fetcher()
    os.makedirs(RACES_DIR, exist_ok=True)
    for year in range(year_from, year_to + 1):
        races = g1_races_of_year(year, fetcher)
        print(f'{year}: GⅠ {len(races)}レース', flush=True)
        for race in races:
            path = os.path.join(RACES_DIR, f"{race['race_id']}.json")
            if os.path.exists(path):
                continue
            data = collect_race(race, fetcher)
            if not data['runners']:
                print(f"  {race['race_id']} {race['race_name']}: 出走馬が読めない（スキップ）", flush=True)
                continue
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            print(f"  {race['date']} {race['race_name']} {len(data['runners'])}頭", flush=True)


# ---------------- 集計 ----------------

def load_races(races_dir=RACES_DIR):
    races = []
    for path in sorted(glob.glob(os.path.join(races_dir, '*.json'))):
        with open(path, encoding='utf-8') as f:
            races.append(json.load(f))
    return races


def flatten(races):
    """1頭1行にする。集計はすべてこの行の上で行う。"""
    rows = []
    for race in races:
        for r in race['runners']:
            prev = r.get('prev') or {}
            rows.append({
                'year': race['date'][:4],
                'race_name': race['race_name'],
                'name': r['name'],
                'ninki': r['ninki'],
                'rank': r['rank'],
                'bucket': ninki_bucket(r['ninki']),
                'prev_kind': prev.get('kind'),
                'prev_name': prev.get('race_name', ''),
                'interval': interval_bucket(prev.get('days')),
            })
    return rows


def baseline(rows):
    """人気区分ごとの勝率・3着内率（全GⅠの平均）。"""
    base = {}
    for _, label in [(b, ninki_bucket(b[0])) for b in NINKI_BUCKETS]:
        group = [r for r in rows if r['bucket'] == label]
        n = len(group)
        base[label] = {
            'n': n,
            'win': sum(1 for r in group if r['rank'] == 1) / n if n else 0.0,
            'top3': sum(1 for r in group if r['rank'] and r['rank'] <= 3) / n if n else 0.0,
        }
    return base


def score(group, base):
    """着度数と、人気をそろえた期待値に対する倍率。"""
    n = len(group)
    counts = [sum(1 for r in group if r['rank'] == k) for k in (1, 2, 3)]
    top3 = sum(counts)
    exp_win = sum(base[r['bucket']]['win'] for r in group)
    exp_top3 = sum(base[r['bucket']]['top3'] for r in group)
    return {
        'n': n,
        'record': counts + [n - top3],
        'win_ratio': counts[0] / exp_win if exp_win else None,
        'top3_ratio': top3 / exp_top3 if exp_top3 else None,
        'exp_win': exp_win,
        'exp_top3': exp_top3,
    }


KIND_LABELS = {'jra': '前走中央', 'overseas': '前走海外', 'nar': '前走地方'}


def _line(label, s):
    rec = '-'.join(str(x) for x in s['record'])
    wr = f"{s['win_ratio']:.2f}" if s['win_ratio'] is not None else '-'
    tr = f"{s['top3_ratio']:.2f}" if s['top3_ratio'] is not None else '-'
    return (f"| {label} | {s['n']} | {rec} | {s['exp_win']:.1f} | {wr} "
            f"| {s['exp_top3']:.1f} | {tr} |")


TABLE_HEAD = ('| 区分 | 頭数 | 着度数(1-2-3-着外) | 期待勝利数 | 勝利の倍率 | 期待3着内数 | 3着内の倍率 |\n'
              '|---|---|---|---|---|---|---|')


def summary(races):
    rows = flatten(races)
    base = baseline(rows)
    years = sorted({r['year'] for r in rows})
    out = [
        '# 要因別の成績（JRA平地GⅠ）',
        '',
        f'- 対象：{years[0]}〜{years[-1]}年の平地GⅠ {len(races)}レース・延べ{len(rows)}頭（取消・除外を除く）',
        '- 「倍率」は、同じ人気区分の馬の平均（全GⅠ）と比べて何倍走ったか。1.00 が平均並み。',
        '  人気馬ほど来るのは当たり前なので、勝率そのものではなく倍率で読む。',
        '- 母数が小さい区分（目安として期待3着内数が5未満）の倍率は振れが大きい。単独で印を動かす根拠にしない'
        '（予想メソッド 第4章）。',
        '- `python trends.py summary` で作り直す。手で書き換えない。',
        '',
        '## 人気別の基準値',
        '',
        '| 人気 | 頭数 | 勝率 | 3着内率 |',
        '|---|---|---|---|',
    ]
    for label, b in base.items():
        out.append(f"| {label} | {b['n']} | {b['win']:.3f} | {b['top3']:.3f} |")

    out += ['', '## 前走の場所', '', TABLE_HEAD]
    for kind in ('jra', 'overseas', 'nar'):
        out.append(_line(KIND_LABELS[kind], score([r for r in rows if r['prev_kind'] == kind], base)))

    out += ['', '## 前走の場所 × 間隔', '',
            '海外帰りが悪いのが「遠征」のせいか「休み明け」のせいかを分けるため、同じ間隔の中央組と並べる。',
            '', TABLE_HEAD]
    for kind in ('jra', 'overseas'):
        for _, _, label in INTERVAL_BUCKETS:
            group = [r for r in rows if r['prev_kind'] == kind and r['interval'] == label]
            if group:
                out.append(_line(f'{KIND_LABELS[kind]}・{label}', score(group, base)))

    overseas = [r for r in rows if r['prev_kind'] == 'overseas']
    out += ['', '## 前走海外 × レース', '', TABLE_HEAD]
    for name in sorted({r['race_name'] for r in overseas}):
        out.append(_line(name, score([r for r in overseas if r['race_name'] == name], base)))

    out += ['', '## 前走海外の該当馬', '',
            '| 年 | レース | 馬名 | 人気 | 着順 | 前走 | 間隔 |', '|---|---|---|---|---|---|---|']
    for r in sorted(overseas, key=lambda r: (r['race_name'], r['year'])):
        rank = r['rank'] if r['rank'] else '着外'
        out.append(f"| {r['year']} | {r['race_name']} | {r['name']} | {r['ninki']} | {rank} "
                   f"| {r['prev_name']} | {r['interval'] or '-'} |")
    return '\n'.join(out) + '\n'


def main(argv):
    if len(argv) >= 4 and argv[1] == 'collect':
        collect(int(argv[2]), int(argv[3]))
    elif len(argv) >= 2 and argv[1] == 'summary':
        text = summary(load_races())
        path = os.path.join(TRENDS_DIR, 'summary.md')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        print(text)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
