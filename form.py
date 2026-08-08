#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""各馬の馬場状態別の実績と馬体重を取得する。

予想メソッド 第12章の分析シートは「馬場適性（良・稍重・重・不良）」を
必須項目に挙げている。当日の馬場が渋ったとき、印を打った馬がその馬場で
走った実績があるかどうかは**事実の照会**であって判断ではない。
ここはむしろ機械のほうが速く正確なので、直前検算で引いてくる。

**評価はしない。** 「重[0-1-0-1]」という着別度数を出すところまで。
それを見てどの馬を上げ下げするかは第5章・第9章・第10章に沿って人が決める。
かつて analyzer.py で「ゴールドシップは道悪向き」と決め打ちして
第5章（ダート不向きの芝向き父系として名指し）に真っ向から反した過ちを繰り返さない。

外部ライブラリは使わない（jra_bias.py と同じ方針）。
"""

import re
import time
import urllib.error
import urllib.request

SHUTUBA_URL = 'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'
HORSE_URL = 'https://db.netkeiba.com/horse/{horse_id}/'

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')

GOING_LEVELS = ['良', '稍重', '重', '不良']

# サーバへの配慮。jra_bias.py と同じ間隔にそろえる。
REQUEST_INTERVAL = 1.5


class FormError(RuntimeError):
    pass


def _fetch(url, timeout=30, opener=None):
    """db.netkeiba.com は EUC-JP、race.netkeiba.com は UTF-8。両対応で読む。"""
    request = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        open_url = opener or urllib.request.urlopen
        with open_url(request, timeout=timeout) as response:
            raw = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise FormError(f'{url} を取得できませんでした: {exc}')

    for encoding in ('utf-8', 'euc_jp'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('euc_jp', errors='replace')


def strip_tags(html):
    text = re.sub(r'<[^>]+>', ' ', html)
    text = text.replace('&nbsp;', ' ').replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', text).strip()


# ----------------------------------------------------------------------
# 出馬表から 馬番 → horse_id・馬体重 を引く
# ----------------------------------------------------------------------

def parse_entries(page):
    """出馬表から {馬番: {horse_id, name, weight, weight_diff}} を作る。"""
    entries = {}
    for row in re.findall(r'<tr class="[^"]*HorseList[^"]*"[^>]*>(.*?)</tr>', page, re.S):
        umaban = re.search(r'<td[^>]*class="[^"]*Umaban[^"]*"[^>]*>\s*(\d+)\s*</td>', row)
        horse = re.search(r'/horse/(\d+)', row)
        if not umaban or not horse:
            continue

        name = re.search(r'/horse/\d+/?"[^>]*>([^<]+)<', row)
        weight_cell = re.search(r'<td[^>]*class="[^"]*Weight[^"]*"[^>]*>(.*?)</td>', row, re.S)
        weight = weight_diff = None
        if weight_cell:
            text = strip_tags(weight_cell.group(1))
            matched = re.match(r'(\d{3})', text)
            weight = int(matched.group(1)) if matched else None
            diff = re.search(r'\(([-+±]?\d+)\)', text)
            if diff:
                try:
                    weight_diff = int(diff.group(1).replace('±', ''))
                except ValueError:
                    weight_diff = None

        entries[int(umaban.group(1))] = {
            'horse_id': horse.group(1),
            'name': (name.group(1).strip() if name else ''),
            'weight': weight,
            'weight_diff': weight_diff,
        }
    return entries


def fetch_entries(race_id, opener=None):
    return parse_entries(_fetch(SHUTUBA_URL.format(race_id=race_id), opener=opener))


# ----------------------------------------------------------------------
# 馬ごとの馬場状態別成績
# ----------------------------------------------------------------------

def _column_index(header_cells):
    """ヘッダー行から列位置を特定する（列順の変更に耐えるため）。"""
    index = {}
    for position, cell in enumerate(header_cells):
        key = re.sub(r'\s+', '', cell)
        if key.startswith('着順'):
            index.setdefault('着順', position)
        elif key == '馬場':
            index.setdefault('馬場', position)
        elif key == '距離':
            index.setdefault('距離', position)
    return index


def parse_going_record(page):
    """競走成績から馬場状態別の着別度数を作る。

    戻り値は {'良': [1着, 2着, 3着, 着外], ...}。出走が無い馬場は入らない。
    """
    table = re.search(
        r'<table[^>]*class="[^"]*db_h_race_results[^"]*"[^>]*>(.*?)</table>', page, re.S)
    if not table:
        return {}

    rows = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', table.group(1), re.S):
        cells = [strip_tags(c) for c in
                 re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', tr, re.S)]
        if cells:
            rows.append(cells)
    if len(rows) < 2:
        return {}

    index = _column_index(rows[0])
    if '着順' not in index or '馬場' not in index:
        return {}

    record = {}
    for cells in rows[1:]:
        if len(cells) <= max(index['着順'], index['馬場']):
            continue
        going = cells[index['馬場']].strip()
        rank = cells[index['着順']].strip()
        if going not in GOING_LEVELS or not rank.isdigit():
            continue   # 中止・除外や、障害の「障」などは数えない
        counts = record.setdefault(going, [0, 0, 0, 0])
        place = int(rank)
        counts[place - 1 if place <= 3 else 3] += 1
    return record


def fetch_going_record(horse_id, opener=None):
    return parse_going_record(_fetch(HORSE_URL.format(horse_id=horse_id), opener=opener))


# ----------------------------------------------------------------------
# 表示
# ----------------------------------------------------------------------

def format_record(counts):
    """[1,0,1,3] を "[1-0-1-3]" にする。"""
    return '[' + '-'.join(str(c) for c in counts) + ']'


def summarise(record, going):
    """当日の馬場での実績を1行にする。出走が無ければそう書く。"""
    if not record:
        return '戦績を取得できませんでした'

    parts = []
    if going:
        counts = record.get(going)
        parts.append(f'{going}{format_record(counts)}' if counts
                     else f'{going}は出走なし')
    for level in GOING_LEVELS:
        if level != going and level in record:
            parts.append(f'{level}{format_record(record[level])}')
    return ' / '.join(parts)


def has_experience(record, going):
    """その馬場で走ったことがあるか。"""
    return bool(record.get(going)) if going else True


def collect(race_id, umabans, opener=None, interval=REQUEST_INTERVAL, sleep=time.sleep):
    """指定した馬番について、馬体重と馬場別成績をまとめて引く。

    印を打った馬だけに絞って呼ぶこと（全頭引くとアクセスが増えすぎる）。
    1頭でも失敗したら、その馬だけ空にして続行する。
    """
    entries = fetch_entries(race_id, opener=opener)
    out = {}
    for umaban in umabans:
        entry = entries.get(umaban)
        if not entry:
            out[umaban] = {'record': {}, 'error': '出馬表に見つかりません'}
            continue
        sleep(interval)
        try:
            record = fetch_going_record(entry['horse_id'], opener=opener)
            error = None
        except FormError as exc:
            record, error = {}, str(exc)
        out[umaban] = {**entry, 'record': record, 'error': error}
    return out
