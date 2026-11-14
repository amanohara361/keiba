#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JRA 公式（www.jra.go.jp の JRADB）から、当日の単勝・騎手・馬場発表を読む。

なぜ作ったか（2026-11-14）：
- セッション（Routine・対話）からの race.netkeiba.com は、11-14 から大半が 400 で
  弾かれるようになった（同じ要求が 200 と 400 を行き来する。docs/決定ログ.md）。
- JRA 公式は同じ時刻に毎回 200 で、単勝は netkeiba の API と16頭すべて一致した。
  オッズ・騎手・馬場は JRA が発表する元の数字なので、食い違ったら公式を正とする。
- 近走・血統・追い切りなどカードの中身は JRA 公式に無い。カードと直前検算は
  今までどおり netkeiba（Actions）。これはセッションから当日の情報を見るための道具。

取り方：
- オッズのページは `cname` を POST して開く。末尾の2桁（`…Z/E1`）はレースごとに違う
  チェック値で、決め打ちできない。メニュー → 開催場 → レースの順にリンクから拾う。
- 馬場発表は JSON（`accessJ.html`・`CNAME=pw01iwtS3/CD`）。馬場情報のページの
  スクリプトが読んでいるもの。開催場ごとに天候・芝・ダート・変更の件数が入る。
- 文字コードは Shift_JIS。
- Python 既定の名乗り（Python-urllib）は 403 になる。このツールだと分かる名乗りを付ける
  （ブラウザは装わない）。

外部ライブラリは使わない。
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://www.jra.go.jp/JRADB/'
UA = 'keiba-tools/1.0 (personal use)'
ODDS_MENU = 'pw15oli00/6D'          # オッズ 開催選択
BABA_CNAME = 'pw01iwtS3/CD'          # 馬場情報（JSON）
ENCODING = 'shift_jis'


class JraError(RuntimeError):
    pass


def _post(page, value, field='cname', timeout=20, opener=None):
    data = urllib.parse.urlencode({field: value}).encode('ascii')
    request = urllib.request.Request(BASE + page, data=data, headers={'User-Agent': UA})
    try:
        open_url = opener or urllib.request.urlopen
        with open_url(request, timeout=timeout) as response:
            return response.read().decode(ENCODING, errors='replace')
    except (urllib.error.URLError, OSError) as exc:
        raise JraError(f'JRA 公式の {page}（{value}）を取得できませんでした: {exc}')


def _text(html):
    text = re.sub(r'<[^>]+>', ' ', html or '')
    text = text.replace('&nbsp;', ' ').replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', text).strip()


# ----------------------------------------------------------------------
# race_id と cname の対応
# ----------------------------------------------------------------------
# race_id（netkeiba と同じ12桁）：年(4) 場(2) 回(2) 日(2) R(2)。例 202605050311 = 2026 東京 5回 3日 11R
# 開催場の cname：pw15orl00{場}{年}{回}{日}{年月日}/xx
# 単勝の cname  ：pw151ouS3{場}{年}{回}{日}{R}{年月日}Z/xx

VENUE_RE = re.compile(r"pw15orl00(\d{2})(\d{4})(\d{2})(\d{2})(\d{8})/[0-9A-F]{2}")
RACE_RE = re.compile(r"pw151ouS3(\d{2})(\d{4})(\d{2})(\d{2})(\d{2})(\d{8})Z/[0-9A-F]{2}")


def parse_menu(html):
    """オッズの開催選択ページから {(場, 年, 回, 日): cname} を作る。"""
    found = {}
    for m in VENUE_RE.finditer(html):
        found.setdefault(m.group(1, 2, 3, 4), m.group(0))
    return found


def parse_races(html):
    """開催場（またはメニュー）のページから {race_id: 単勝の cname} を作る。"""
    found = {}
    for m in RACE_RE.finditer(html):
        jyo, year, kai, day, rr, _ymd = m.groups()
        found.setdefault(f'{year}{jyo}{kai}{day}{rr}', m.group(0))
    return found


def race_cname(race_id, opener=None):
    """race_id の単勝ページの cname を、メニュー → 開催場の順に拾う。"""
    race_id = str(race_id)
    if not re.fullmatch(r'\d{12}', race_id):
        raise JraError(f'race_id は12桁の数字です: {race_id}')
    menu = _post('accessO.html', ODDS_MENU, opener=opener)
    races = parse_races(menu)        # メニューに重賞などが直接載っていることがある
    if race_id in races:
        return races[race_id]
    key = (race_id[4:6], race_id[0:4], race_id[6:8], race_id[8:10])
    venue = parse_menu(menu).get(key)
    if not venue:
        raise JraError(f'{race_id} の開催がオッズのメニューにありません（発売前・終了後の可能性）')
    races = parse_races(_post('accessO.html', venue, opener=opener))
    if race_id not in races:
        raise JraError(f'{race_id} のレースが開催場のページにありません')
    return races[race_id]


# ----------------------------------------------------------------------
# 単勝・複勝（馬番順）
# ----------------------------------------------------------------------

def _cell(row, cls):
    m = re.search(r'<td[^>]*class="[^"]*\b' + cls + r'\b[^"]*"[^>]*>(.*?)</td>', row, re.S)
    return m.group(1) if m else None


def _float(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_win(html):
    """単勝・複勝（馬番順）のページを読む。

    戻り値：{'updated': '10時38分', 'start': '15時30分',
            'horses': {馬番: {name, win, place_min, place_max, sex_age, weight_text,
                              kinryo, jockey, trainer}}}
    取消などでオッズが数字でない馬は win を None にし、win_text に元の文字を残す。
    """
    table = re.search(r'<table[^>]*class="[^"]*tanpuku[^"]*"[^>]*>(.*?)</table>', html, re.S)
    if not table:
        raise JraError('単勝・複勝の表が見つかりません（ページの書き方が変わった可能性）')
    horses = {}
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', table.group(1), re.S):
        num = _text(_cell(row, 'num'))
        if not num.isdigit():
            continue
        win_text = _text(_cell(row, 'odds_tan'))
        place = _cell(row, 'odds_fuku') or ''
        pmin = re.search(r'class="min">([^<]*)<', place)
        pmax = re.search(r'class="max">([^<]*)<', place)
        horses[int(num)] = {
            'name': _text(_cell(row, 'horse')),
            'win': _float(win_text),
            'win_text': win_text,
            'place_min': _float(pmin.group(1)) if pmin else None,
            'place_max': _float(pmax.group(1)) if pmax else None,
            'sex_age': _text(_cell(row, 'age')) or None,
            'weight_text': _text(_cell(row, 'h_weight')) or None,   # 馬体重（発表前は空）
            'kinryo': _float(_text(_cell(row, 'weight'))),
            'jockey': _text(_cell(row, 'jockey')) or None,
            'trainer': _text(_cell(row, 'trainer')) or None,
        }
    if not horses:
        raise JraError('単勝・複勝の表に馬の行がありません（ページの書き方が変わった可能性）')
    updated = re.search(r'(\d+時\d+分)現在オッズ', html)
    start = re.search(r'(\d+時\d+分)', html[:table.start()])
    return {'updated': updated.group(1) if updated else None,
            'start': start.group(1) if start else None,
            'horses': horses}


def fetch_win(race_id, opener=None):
    return parse_win(_post('accessO.html', race_cname(race_id, opener=opener), opener=opener))


# ----------------------------------------------------------------------
# 馬場発表（JSON）
# ----------------------------------------------------------------------

CHANGE_LABELS = {
    'entry_cancel': '出走取消', 'exclusion': '競走除外', 'jockey_change': '騎手変更',
    'start_change': '発走時刻変更', 'course_change': 'コース変更', 'grade_change': '格付変更',
    'weight_over': '負担重量変更', 'blinkers_cancel': 'ブリンカー取りやめ',
}


def parse_baba(text):
    """馬場情報の JSON を読む。開催場ごとに1件。未発表の項目は None。"""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise JraError(f'馬場情報の JSON を読めません（書き方が変わった可能性）: {exc}')
    out = []
    for item in data.get('kaisai_info') or []:
        changes = {}
        for block in item.get('changes') or []:
            for key, value in block.items():
                try:
                    count = int(value)
                except (TypeError, ValueError):
                    continue
                if count:
                    changes[CHANGE_LABELS.get(key, key)] = count
        out.append({
            'venue': item.get('jyoname') or None,
            'date': item.get('kaisai_ymd') or None,
            'kai': item.get('kai') or None,
            'day': item.get('day') or None,
            'weather': item.get('weather') or None,
            'going_turf': item.get('ba_s') or None,
            'going_dirt': item.get('ba_d') or None,
            'decided': item.get('decided') or None,
            'cancel_info': item.get('cancel_info') or None,
            'changes': changes,
        })
    return out


def fetch_baba(opener=None):
    return parse_baba(_post('accessJ.html', BABA_CNAME, field='CNAME', opener=opener))


# ----------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description='JRA 公式から当日の単勝・騎手・馬場発表を読む')
    sub = parser.add_subparsers(dest='command', required=True)
    odds = sub.add_parser('odds', help='単勝・複勝・騎手（馬番順）')
    odds.add_argument('race_id', help='12桁（netkeiba と同じ。例 202605050311）')
    sub.add_parser('baba', help='開催場ごとの天候・馬場・変更')
    args = parser.parse_args(argv)

    try:
        if args.command == 'odds':
            got = fetch_win(args.race_id)
            print(f"{args.race_id}  発走 {got['start']}  {got['updated']}現在（JRA 公式）")
            for num, h in sorted(got['horses'].items()):
                place = (f"{h['place_min']}-{h['place_max']}"
                         if h['place_min'] is not None else '-')
                print(f"{num:>2} {h['name']:<12} 単{h['win_text']:>6}  複{place:<11} "
                      f"{h['sex_age'] or ''} {h['kinryo'] or ''} {h['jockey'] or ''}"
                      f"{'  ' + h['weight_text'] if h['weight_text'] else ''}")
        else:
            for v in fetch_baba():
                changes = '、'.join(f'{k}{n}件' for k, n in v['changes'].items()) or 'なし'
                print(f"{v['date']} {v['venue']} {v['kai']}回{v['day']}日  天候:{v['weather'] or '-'}  "
                      f"芝:{v['going_turf'] or '-'}  ダ:{v['going_dirt'] or '-'}  "
                      f"{v['decided'] or ''}  変更:{changes}")
    except JraError as exc:
        print(f'エラー: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
