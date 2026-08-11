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

# 馬の情報は3ページに分かれている。
# 2026-08-11、トップページ（/horse/{id}/）から競走成績と血統表が消えて
# いることを Actions 側の probe で確認した（table は db_prof_table 1つだけ）。
# それまで競走成績をトップページから読もうとしていたため、
# **道悪実績の照会は本番でずっと空を返していた**。
HORSE_URL = 'https://db.netkeiba.com/horse/{horse_id}/'
RESULT_URL = 'https://db.netkeiba.com/horse/result/{horse_id}/'
PED_URL = 'https://db.netkeiba.com/horse/ped/{horse_id}/'

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
    return parse_going_record(_fetch(RESULT_URL.format(horse_id=horse_id), opener=opener))


# ----------------------------------------------------------------------
# 近走と血統（方針B・カード作成で使う）
# ----------------------------------------------------------------------

# 競走成績の列。第2章の減点方式チェックリストと第8章の7つの視点が
# 必要とする事実は、ほぼこの1つの表から取れる。
#   前走着順（4〜6着ゾーン）／距離の延長短縮／騎手の乗り替わり／
#   休み明けの期間（日付の間隔）／脚質（通過順）／前走がローカル場か
RUN_COLUMNS = {
    '日付': 'date', '開催': 'meeting', '天気': 'weather', 'R': 'race_no',
    'レース名': 'race', '頭数': 'field_size', '枠番': 'waku', '馬番': 'umaban',
    'オッズ': 'odds', '人気': 'ninki', '着順': 'rank', '騎手': 'jockey',
    '斤量': 'kinryo', '距離': 'distance', '馬場': 'going', 'タイム': 'time',
    '着差': 'margin', '通過': 'passing', 'ペース': 'pace', '上り': 'last3f',
    '馬体重': 'weight', '勝ち馬': 'winner', '勝ち馬(2着馬)': 'winner',
}

NUMERIC_RUN_FIELDS = {'field_size', 'waku', 'umaban', 'ninki', 'rank', 'race_no'}
FLOAT_RUN_FIELDS = {'odds', 'kinryo', 'last3f'}


def _run_columns(header_cells):
    index = {}
    for position, cell in enumerate(header_cells):
        key = re.sub(r'\s+', '', cell)
        field = RUN_COLUMNS.get(key)
        if field:
            index.setdefault(field, position)
    return index


def parse_recent_runs(page, limit=5):
    """競走成績から直近の走りを取り出す。新しい順。

    表の列順は netkeiba 側でよく変わるので、位置ではなく見出しで引く
    （jra_bias.py の col_index と同じ考え方）。
    """
    table = re.search(
        r'<table[^>]*class="[^"]*db_h_race_results[^"]*"[^>]*>(.*?)</table>', page, re.S)
    if not table:
        return []

    rows = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', table.group(1), re.S):
        cells = [strip_tags(c) for c in
                 re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', tr, re.S)]
        if cells:
            rows.append(cells)
    if len(rows) < 2:
        return []

    index = _run_columns(rows[0])
    runs = []
    for cells in rows[1:]:
        if not index or len(cells) <= max(index.values()):
            continue
        run = {}
        for field, position in index.items():
            value = cells[position].strip()
            if not value:
                continue
            if field in NUMERIC_RUN_FIELDS and value.isdigit():
                run[field] = int(value)
            elif field in FLOAT_RUN_FIELDS:
                try:
                    run[field] = float(value)
                except ValueError:
                    run[field] = value
            else:
                run[field] = value
        # 距離は「芝1200」の形。コースと数字に割る。
        distance = run.get('distance', '')
        matched = re.match(r'(芝|ダ|障)\s*(\d{3,4})', distance)
        if matched:
            run['surface'] = {'ダ': 'ダート'}.get(matched.group(1), matched.group(1))
            run['distance'] = int(matched.group(2))
        # 馬体重は「424(-2)」の形。
        weight = str(run.get('weight', ''))
        matched = re.match(r'(\d{3})\(([-+±]?\d+)\)', weight)
        if matched:
            run['weight'] = int(matched.group(1))
            try:
                run['weight_diff'] = int(matched.group(2).replace('±', ''))
            except ValueError:
                pass
        if run:
            runs.append(run)
        if len(runs) >= limit:
            break
    return runs


def parse_pedigree(page):
    """血統表から父・母・母父を取る。

    第5章（ダート適性の父系）と第12章（血統分析）が使う。
    表の作りに深く踏み込むと壊れやすいので、血統表の中に現れる
    馬名リンクの順序だけを使う。netkeiba の blood_table は
    父・父父・父母・母・母父・母母 の順に並ぶ。
    """
    table = re.search(
        r'<table[^>]*class="[^"]*blood_table[^"]*"[^>]*>(.*?)</table>', page, re.S)
    if not table:
        return {}
    names = [strip_tags(n) for n in
             re.findall(r'<a[^>]*href="[^"]*/horse/[^"]*"[^>]*>(.*?)</a>',
                        table.group(1), re.S)]
    names = [n for n in names if n]
    if len(names) < 5:
        return {}
    return {'sire': names[0], 'dam': names[3], 'broodmare_sire': names[4]}


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
