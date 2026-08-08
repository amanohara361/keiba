#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当日の馬場状態・天候を取得する。

朝の時点では馬場状態が未発表のことが多く、予想ログにも
「札幌の天候・馬場状態は朝時点で未発表。降雨でダートが前残り強化なら
◎2・○11が有利、差しの▲9は割引。14:30の直前タスクで要確認」
と書かれる。その「要確認」を直前検算で機械的に拾う。

**判断はしない。** 渋っていることを知らせて、メソッドの該当章を指すだけ。
道悪でどの馬を上げ下げするかは第5章・第10章に沿って人が決める。

外部ライブラリは使わない（jra_bias.py と同じ方針）。
"""

import re
import urllib.error
import urllib.request

SHUTUBA_URL = 'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'

# 予備の取得先。jra_bias.py がこのページから馬場状態を108レース分
# 正しく読めているため、出馬表側で読めなかったときはこちらに回す。
DB_RACE_URL = 'https://db.netkeiba.com/race/{race_id}/'

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')

# 良い順に並べる。良より下は「渋っている」と扱う。
GOING_LEVELS = ['良', '稍重', '重', '不良']
HEAVY_GOING = {'稍重', '重', '不良'}


class ConditionsError(RuntimeError):
    pass


def strip_tags(html):
    text = re.sub(r'<[^>]+>', ' ', html)
    text = text.replace('&nbsp;', ' ').replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def find_going(text):
    """テキストから馬場状態を拾う。

    2026-08-08、新潟は「良」が取れたのに札幌は取れず天候だけ読めた。
    書き方の揺れに弱かったため、次の順で緩く探す。
    「不良」を「良」より先に置かないと不良を良と誤読する。
    """
    patterns = [
        # 「馬場:良」「馬場状態 : 稍重」「芝:重」「ダート:不良」
        r'(?:馬場状態|馬場|芝|ダート|ダ)\s*[:：]\s*(不良|稍重|重|良)',
        # 区切りが無い書き方「馬場 良」
        r'(?:馬場状態|馬場)\s+(不良|稍重|重|良)',
        # 最後の手段。ヘッダ内に単独で現れる語を拾う
        r'(?<![^\s:：/])(不良|稍重|重|良)(?![^\s/])',
    ]
    for pattern in patterns:
        found = re.search(pattern, text)
        if found:
            return found.group(1)
    return None


def parse(page):
    """出馬表ページから馬場状態・天候・コースを読む。

    当日にならないと馬場状態は出ないため、取れない項目は None を返す。
    """
    header = re.search(r'<div class="RaceData01"[^>]*>(.*?)</div>', page, re.S)
    text = strip_tags(header.group(1)) if header else ''
    # db.netkeiba.com 側は data_intro に条件が入る（jra_bias.py と同じ場所）
    if not text:
        intro = re.search(r'<div class="data_intro"[^>]*>(.*?)</div>', page, re.S)
        text = strip_tags(intro.group(1)) if intro else strip_tags(page[:6000])

    weather = re.search(r'天候\s*[:：]\s*(\S+?)(?:\s|/|$)', text)
    distance = re.search(r'(\d{3,4})\s*m', text)

    surface = None
    if re.search(r'ダート|ダ\s*[右左直]|ダ\s*\d', text):
        surface = 'ダート'
    elif '芝' in text:
        surface = '芝'

    return {
        'going': find_going(text),
        'weather': weather.group(1) if weather else None,
        'surface': surface,
        'distance': int(distance.group(1)) if distance else None,
    }


def _get(url, timeout=30, opener=None):
    request = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        open_url = opener or urllib.request.urlopen
        with open_url(request, timeout=timeout) as response:
            raw = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise ConditionsError(f'{url} を取得できませんでした: {exc}')
    # race.netkeiba.com は UTF-8、db.netkeiba.com は EUC-JP
    for encoding in ('utf-8', 'euc_jp'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('euc_jp', errors='replace')


def fetch(race_id, timeout=30, opener=None):
    """1レース分の馬場状態を取る。

    出馬表ページで馬場が読めなければ db.netkeiba.com にも当たる。
    jra_bias.py が同ページから108レース分を正しく読めている実績があるため、
    出馬表側の書き方の揺れに巻き込まれない予備経路として使う。
    コースなど先に取れた項目は活かし、足りない項目だけ補う。
    """
    conditions = parse(_get(SHUTUBA_URL.format(race_id=race_id), timeout, opener))
    if conditions.get('going'):
        return conditions

    try:
        fallback = parse(_get(DB_RACE_URL.format(race_id=race_id), timeout, opener))
    except ConditionsError:
        return conditions   # 予備が駄目でも、取れているぶんは返す

    for key, value in fallback.items():
        if value is not None and conditions.get(key) is None:
            conditions[key] = value
    return conditions


def label(conditions):
    """「ダート1700m / 天候:雨 / 馬場:重」の1行。未発表の項目は省く。"""
    if not conditions:
        return ''
    parts = []
    if conditions.get('surface') and conditions.get('distance'):
        parts.append(f"{conditions['surface']}{conditions['distance']}m")
    if conditions.get('weather'):
        parts.append(f"天候:{conditions['weather']}")
    parts.append(f"馬場:{conditions['going']}" if conditions.get('going') else '馬場:未発表')
    return ' / '.join(parts)


def is_heavy(conditions):
    return bool(conditions) and conditions.get('going') in HEAVY_GOING


def probe(race_id, opener=None):
    """馬場が読めないときに、実際のページの中身を確認するための調査コマンド。

    2026-08-08、新潟は「良」が取れたのに札幌は取れず、天候だけ読めるという
    症状が出た。推測で正規表現をいじる前に、まず生のテキストを見る。
    （jra_bias.py の probe と同じ考え方）
    """
    url = SHUTUBA_URL.format(race_id=race_id)
    print(f'  URL: {url}')
    request = urllib.request.Request(url, headers={'User-Agent': UA})
    open_url = opener or urllib.request.urlopen
    with open_url(request, timeout=30) as response:
        page = response.read().decode('utf-8', errors='replace')

    print(f'  ページ長: {len(page)}')
    for name in ('RaceData01', 'RaceData02'):
        block = re.search(rf'<div class="{name}"[^>]*>(.*?)</div>', page, re.S)
        if block:
            print(f'\n  --- {name} の生HTML ---')
            print('  ' + block.group(1).strip()[:600].replace('\n', '\n  '))
            print(f'  --- {name} のテキスト ---')
            print('  ' + repr(strip_tags(block.group(1))))
        else:
            print(f'\n  --- {name} は見つかりません ---')

    # 馬場状態らしき語がページのどこにあるかを探す
    print('\n  --- 「馬場」「良/稍重/重/不良」の出現箇所 ---')
    for match in re.finditer(r'.{60}(?:馬場|稍重|不良).{60}', page, re.S):
        print('  ' + repr(match.group(0)))

    print(f'\n  --- parse() の結果 ---\n  {parse(page)}')


def main(argv=None):
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) >= 2 and argv[0] == 'probe':
        probe(argv[1])
        return 0
    print(__doc__)
    print('  python conditions.py probe <race_id>')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
