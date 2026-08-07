#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""実オッズの取得。

CLAUDE.md「オッズの情報源」の方針に従う:
  発走前 … netkeiba 公式API（action=update）。status="middle" の発売中オッズが欲しい。
  発走後 … 無条件でスポーツナビ。netkeiba APIは叩かない（朝の値しか返らない）。

判定条件は「発走時刻を過ぎているか」の一点のみ。経過時間は数えない
（旧「5時間ルール」は2026-08-02に反証済み）。

本モジュールが担うのは発走前のAPI取得まで。直前検算（14:30）は必ず発走前なので
これで足りる。発走後の確定オッズが要る場面は jra_bias.py の ingest 系を使う。

外部ライブラリは使わない。
"""

import json
import urllib.error
import urllib.request

from bets import BET_TYPES

API = ('https://race.netkeiba.com/api/api_get_jra_odds.html'
       '?type={t}&locale=ja&race_id={r}&action=update')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')

STATUS_LABELS = {
    'yoso': '予想オッズ（発売前）',
    'middle': '発売中の途中経過',
    'result': '確定オッズ',
}

# 出走取消・除外の馬に API が返す値
SCRATCHED = '-3.0'


class OddsError(RuntimeError):
    pass


def key_of(numbers, bet_type):
    """馬番のリストをAPIのキー形式に変換する。例 [1,3,8] -> '010308'

    馬単は着順が意味を持つので並べ替えない。それ以外は昇順に揃える。
    （jra_bias.py の key_of と同じ規則）
    """
    code = BET_TYPES[bet_type]
    numbers = [int(x) for x in numbers]
    if code != '6':
        numbers = sorted(numbers)
    return ''.join(f'{n:02d}' for n in numbers)


def fetch(race_id, bet_type='単勝', timeout=30, opener=None):
    """指定した券種のオッズ表を丸ごと取得する。

    戻り値は {'status':..., 'official_datetime':..., 'odds': {キー: [オッズ,...]}}。
    """
    code = BET_TYPES[bet_type]
    url = API.format(t=code, r=race_id)
    request = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Referer': 'https://race.netkeiba.com/',
    })
    try:
        open_url = opener or urllib.request.urlopen
        with open_url(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise OddsError(f'{race_id} の{bet_type}オッズを取得できませんでした: {exc}')

    data = payload.get('data') or {}
    return {
        'status': payload.get('status'),
        'reason': payload.get('reason'),
        'official_datetime': data.get('official_datetime'),
        'odds': (data.get('odds') or {}).get(code, {}) or {},
    }


def odds_for(table, numbers, bet_type):
    """買い目1点の実オッズ。見つからなければ None。"""
    entry = table.get(key_of(numbers, bet_type))
    if not entry:
        return None
    raw = str(entry[0]).replace(',', '')
    if raw == SCRATCHED:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def win_odds_table(table):
    """単勝オッズ表を {馬番: (オッズ, 人気)} にほぐす。取消馬は除く。"""
    out = {}
    for key, entry in table.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            continue
        raw = str(entry[0]).replace(',', '')
        if raw == SCRATCHED:
            continue
        try:
            odds = float(raw)
        except ValueError:
            continue
        ninki = None
        if len(entry) > 2 and str(entry[2]).isdigit():
            ninki = int(entry[2])
        out[number] = (odds, ninki)
    return out


def collect(race, fetcher=None):
    """1レースの買い目に必要なオッズを、券種ごとに1回だけ取得してまとめる。

    戻り値は (買い目ごとのオッズのリスト, 単勝表, メタ情報)。
    取得できなかった買い目は None が入る（呼び出し側が「暫定」として扱う）。

    fetcher は既定値ではなく呼び出し時に解決する。既定引数に束縛すると
    差し替えが効かず、テストが本物のAPIを叩きにいってしまうため。
    """
    fetcher = fetcher or fetch
    needed = {bet.type for bet in race.bets} | {'単勝'}
    tables = {}
    meta = {}
    errors = []

    for bet_type in sorted(needed):
        try:
            result = fetcher(race.race_id, bet_type)
        except OddsError as exc:
            errors.append(str(exc))
            continue
        tables[bet_type] = result['odds']
        meta[bet_type] = {
            'status': result['status'],
            'reason': result['reason'],
            'official_datetime': result['official_datetime'],
        }

    bet_odds = [
        odds_for(tables.get(bet.type, {}), bet.horses, bet.type)
        for bet in race.bets
    ]
    return bet_odds, win_odds_table(tables.get('単勝', {})), {
        'per_type': meta,
        'errors': errors,
        'status': (meta.get('単勝') or {}).get('status'),
        'official_datetime': (meta.get('単勝') or {}).get('official_datetime'),
    }
