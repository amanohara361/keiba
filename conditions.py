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


def parse(page):
    """出馬表ページから馬場状態・天候・コースを読む。

    当日にならないと馬場状態は出ないため、取れない項目は None を返す。
    """
    header = re.search(r'<div class="RaceData01"[^>]*>(.*?)</div>', page, re.S)
    text = strip_tags(header.group(1)) if header else strip_tags(page[:6000])

    # 「馬場:良」「芝 : 稍重」「ダート:重」いずれの書き方でも拾う
    going = re.search(r'(?:馬場|芝|ダート|ダ)\s*[:：]\s*(良|稍重|重|不良)', text)
    weather = re.search(r'天候\s*[:：]\s*(\S+?)(?:\s|/|$)', text)
    distance = re.search(r'(\d{3,4})\s*m', text)

    surface = None
    if re.search(r'ダート|ダ\s*[右左直]|ダ\s*\d', text):
        surface = 'ダート'
    elif '芝' in text:
        surface = '芝'

    return {
        'going': going.group(1) if going else None,
        'weather': weather.group(1) if weather else None,
        'surface': surface,
        'distance': int(distance.group(1)) if distance else None,
    }


def fetch(race_id, timeout=30, opener=None):
    """1レース分の馬場状態を取る。取れなければ ConditionsError。"""
    url = SHUTUBA_URL.format(race_id=race_id)
    request = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        open_url = opener or urllib.request.urlopen
        with open_url(request, timeout=timeout) as response:
            page = response.read().decode('utf-8', errors='replace')
    except (urllib.error.URLError, OSError) as exc:
        raise ConditionsError(f'{race_id} の馬場状態を取得できませんでした: {exc}')
    return parse(page)


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
