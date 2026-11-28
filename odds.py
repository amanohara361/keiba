#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""実オッズの取得。

CLAUDE.md「オッズの情報源」の方針に従う:
  発走前 … netkeiba 公式API（action=update）。status="middle" の発売中オッズが欲しい。
  発走後 … 無条件でスポーツナビ。netkeiba APIは叩かない（朝の値しか返らない）。

判定条件は「発走時刻を過ぎているか」の一点のみ。経過時間は数えない
（旧「5時間ルール」は2026-08-02に反証済み）。

単勝・複勝は JRA 公式（jra_official.py）を正本にし、netkeiba は予備にする（2026-11-28）。
- JRA 公式は JRA が発表する元の数字。11-14 に武蔵野Sの単勝16頭が netkeiba と一致し、
  11-28 には中央6レースの◎の単勝が5レースで一致した（残り1レースは5分間の変動）。
- 11-28 12:07 の検算で netkeiba が全券種 HTTP 400 を返し、中央6レースが全部見送りになった。
  固定形（◎の単勝＋複勝）はこの2つで成り立つので、ここを netkeiba に頼らないようにする。
- JRA 公式が取れなければ netkeiba で取り直す。馬連・ワイド・3連複は JRA 公式から読む道具が
  無いので、今までどおり netkeiba（参考の組み立てにしか使わない）。

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
    'jra_official': '発売中の途中経過（JRA公式）',
}

# JRA 公式を正本にする券種（jra_official.parse_win の1ページに両方載っている）
OFFICIAL_TYPES = ('単勝', '複勝')

_DEFAULT = object()

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


def tables_from_jra_official(parsed):
    """jra_official.parse_win の結果を、netkeiba API と同じ形の単勝・複勝表にする。

    netkeiba の形は 単勝 {'07': [オッズ, '', 人気]}、複勝 {'07': [下限, 上限, 人気]}。
    同じ形にそろえれば、odds_for・win_odds_table・bet_builder を一切変えずに済む。
    JRA 公式のページに人気は無いので、単勝オッズの低い順で付ける（同じオッズは同じ人気）。
    オッズが数字でない馬（取消など）は取消の印（SCRATCHED）を入れ、買い目から外れるようにする。
    """
    horses = parsed.get('horses') or {}
    prices = [h['win'] for h in horses.values() if h.get('win') is not None]
    win, place = {}, {}
    for number, h in sorted(horses.items()):
        key = f'{int(number):02d}'
        if h.get('win') is None:
            win[key] = [SCRATCHED, '', '']
            place[key] = [SCRATCHED, '', '']
            continue
        ninki = str(1 + sum(1 for p in prices if p < h['win']))
        win[key] = [f"{h['win']:.1f}", '', ninki]
        if h.get('place_min') is not None:
            pmax = h.get('place_max')
            place[key] = [f"{h['place_min']:.1f}",
                          f'{pmax:.1f}' if pmax is not None else '', ninki]
    return {'単勝': win, '複勝': place}


def _jra_official_fetch(race_id):
    # 通信が届かないときに残りのレースで叩かない仕組みと、馬体重との共用は jra_official 側にある。
    import jra_official
    return jra_official.fetch_win_once(race_id)


def fetch_tables(race_id, bet_types, fetcher=None, official=_DEFAULT):
    """指定した券種ぶんのオッズ表をまとめて取得する。

    買い目がまだ確定していない段階（bet_builder が候補の組み合わせを
    試す場面）で使う。券種の表さえあれば `odds_for()` で任意の組み合わせを
    照会できるので、買い目を先に決めておく必要が無い。

    戻り値は (券種ごとのオッズ表, メタ情報)。`collect()` はこれの薄いラッパー。

    単勝・複勝は official（既定は JRA 公式）を先に読み、取れなかった分だけ netkeiba で取る。
    fetcher を差し替えたとき（テスト）は、本物の JRA 公式を叩かないよう既定では使わない。
    check.py は本物の `fetch` をそのまま渡してくるので、それは差し替えに数えない。
    """
    if official is _DEFAULT:
        official = _jra_official_fetch if fetcher in (None, fetch) else None
    fetcher = fetcher or fetch
    needed = set(bet_types) | {'単勝'}
    tables = {}
    meta = {}
    errors = []

    wanted = [t for t in OFFICIAL_TYPES if t in needed]
    if wanted and official:
        try:
            parsed = official(race_id)
        except Exception as exc:   # JRA 公式の失敗で検算全体を止めない（netkeiba へ回る）
            errors.append(f'{race_id} の単勝・複勝を JRA 公式から取得できませんでした'
                          f'（netkeiba で取り直します）: {exc}')
        else:
            jra = tables_from_jra_official(parsed)
            for bet_type in wanted:
                if not jra.get(bet_type):   # 空の表は取れたに数えない（発売前など）
                    continue
                tables[bet_type] = jra[bet_type]
                meta[bet_type] = {
                    'status': 'jra_official',
                    'reason': None,
                    'official_datetime': parsed.get('updated'),
                }

    for bet_type in sorted(needed - set(tables)):
        try:
            result = fetcher(race_id, bet_type)
        except OddsError as exc:
            errors.append(str(exc))
            continue
        tables[bet_type] = result['odds']
        meta[bet_type] = {
            'status': result['status'],
            'reason': result['reason'],
            'official_datetime': result['official_datetime'],
        }

    return tables, {
        'per_type': meta,
        'errors': errors,
        'status': (meta.get('単勝') or {}).get('status'),
        'official_datetime': (meta.get('単勝') or {}).get('official_datetime'),
    }


def collect(race, fetcher=None):
    """1レースの買い目に必要なオッズを、券種ごとに1回だけ取得してまとめる。

    戻り値は (買い目ごとのオッズのリスト, 単勝表, メタ情報)。
    取得できなかった買い目は None が入る（呼び出し側が「暫定」として扱う）。

    fetcher は既定値ではなく呼び出し時に解決する。既定引数に束縛すると
    差し替えが効かず、テストが本物のAPIを叩きにいってしまうため。
    """
    tables, meta = fetch_tables(race.race_id, {bet.type for bet in race.bets}, fetcher)
    bet_odds = [
        odds_for(tables.get(bet.type, {}), bet.horses, bet.type)
        for bet in race.bets
    ]
    return bet_odds, win_odds_table(tables.get('単勝', {})), meta
