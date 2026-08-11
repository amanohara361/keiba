#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""確定した着順と払戻を取得する。

着順のパースは jra_bias.py が既に持っている（108レース以上で実績あり）ので
そのまま使い、本モジュールでは払戻の読み取りだけを足す。

払戻は「100円あたり」で表示される。買い目の金額が200円なら払戻も2倍になる。
検算：払戻金 ÷ 100 が該当組の確定オッズと一致する（CLAUDE.md）。

外部ライブラリは使わない。
"""

import re

import jra_bias

RACE_URL = 'https://db.netkeiba.com/race/{race_id}/'

# 払戻表に現れる券種名を、買い目ファイルの表記に揃える
BET_TYPE_ALIASES = {
    '単勝': '単勝',
    '複勝': '複勝',
    '枠連': '枠連',
    '馬連': '馬連',
    'ワイド': 'ワイド',
    '馬単': '馬単',
    '三連複': '3連複',
    '3連複': '3連複',
    '三連単': '3連単',
    '3連単': '3連単',
}

# 着順が確定していれば数字。除外・中止などは数字にならない。
ORDERED_TYPES = {'馬単', '3連単'}


class ResultsError(RuntimeError):
    pass


def _cells(row_html):
    return re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row_html, re.S)


def _split_lines(cell_html):
    """<br> 区切りのセルを行に分ける。複勝・ワイドは1セルに複数入る。"""
    parts = re.split(r'<br\s*/?>', cell_html, flags=re.I)
    return [jra_bias.strip_tags(p) for p in parts if jra_bias.strip_tags(p)]


def parse_payouts(page):
    """払戻表を {券種: [{'combination': [馬番...], 'yen': 金額}, ...]} にする。"""
    payouts = {}
    for table in re.findall(
            r'<table[^>]*class="[^"]*pay_table_01[^"]*"[^>]*>(.*?)</table>', page, re.S):
        for row in re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.S):
            cells = _cells(row)
            if len(cells) < 3:
                continue
            label = re.sub(r'\s+', '', jra_bias.strip_tags(cells[0]))
            bet_type = BET_TYPE_ALIASES.get(label)
            if not bet_type:
                continue

            combinations = _split_lines(cells[1])
            amounts = _split_lines(cells[2])

            entries = []
            for combination, amount in zip(combinations, amounts):
                numbers = [int(n) for n in re.findall(r'\d+', combination)]
                yen = re.sub(r'[^\d]', '', amount)
                if numbers and yen:
                    entries.append({'combination': numbers, 'yen': int(yen)})
            if entries:
                payouts[bet_type] = entries
    return payouts


def parse_finishing_order(page):
    """着順を [{'rank':1,'umaban':4,'name':'...','ninki':1,'odds':3.1}, ...] にする。"""
    table = jra_bias.find_result_table(page)
    if not table:
        return []
    rows = jra_bias.parse_rows(table)
    if len(rows) < 2:
        return []

    index = jra_bias.col_index(rows[0])
    if '着順' not in index or '馬番' not in index:
        return []

    order = []
    for cells in rows[1:]:
        if len(cells) <= max(index.values()):
            continue
        rank = cells[index['着順']].strip()
        if not rank.isdigit():
            continue      # 中止・除外・取消は着順に数字が入らない
        entry = {
            'rank': int(rank),
            'umaban': int(re.sub(r'\D', '', cells[index['馬番']]) or 0),
            'name': cells[index['馬名']] if '馬名' in index else '',
        }
        if '人気' in index and cells[index['人気']].strip().isdigit():
            entry['ninki'] = int(cells[index['人気']])
        if '単勝' in index:
            try:
                entry['odds'] = float(cells[index['単勝']])
            except ValueError:
                pass
        order.append(entry)

    order.sort(key=lambda h: h['rank'])
    return order


def fetch(race_id, fetcher=None):
    """1レースの確定結果。まだ出ていなければ None。"""
    fetcher = fetcher or jra_bias.fetch_html
    try:
        page = fetcher(RACE_URL.format(race_id=race_id))
    except Exception as exc:
        raise ResultsError(f'{race_id} の結果を取得できませんでした: {exc}')

    order = parse_finishing_order(page)
    if not order:
        return None
    return {
        'race_id': race_id,
        'finishing_order': order,
        'payouts': parse_payouts(page),
    }


# ----------------------------------------------------------------------
# 買い目との突き合わせ
# ----------------------------------------------------------------------

def payout_for(payouts, bet_type, horses):
    """その買い目の払戻（100円あたり）。外れていれば0。

    馬単・3連単は着順が意味を持つので並び順まで一致させる。
    それ以外は組み合わせが一致すればよい。
    """
    wanted = [int(h) for h in horses]
    if bet_type not in ORDERED_TYPES:
        wanted = sorted(wanted)

    for entry in payouts.get(bet_type, []):
        got = entry['combination']
        if bet_type not in ORDERED_TYPES:
            got = sorted(got)
        if got == wanted:
            return entry['yen']
    return 0


def settle(race, result):
    """1レースの買い目を精算する。

    払戻は100円あたりなので、賭け金に応じて按分する。
    """
    ranks = {h['umaban']: h['rank'] for h in result['finishing_order']}
    payouts = result['payouts']

    staked = returned = 0
    settled_bets = []
    for bet in race.bets:
        unit = payout_for(payouts, bet.type, bet.horses)
        amount = unit * bet.stake // 100
        staked += bet.stake
        returned += amount
        settled_bets.append({
            'bet': str(bet),
            'stake': bet.stake,
            'payout': amount,
            'hit': amount > 0,
        })

    return {
        'ranks': ranks,
        'staked': staked,
        'returned': returned,
        'bets': settled_bets,
        'hit': any(b['hit'] for b in settled_bets),
    }
