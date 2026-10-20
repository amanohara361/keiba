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


# 着順欄の数字以外の表記（db は1文字、race.netkeiba.com は2文字）
NON_FINISH_STATUS = {'中': '中止', '中止': '中止', '除': '除外', '除外': '除外',
                     '取': '取消', '取消': '取消', '失': '失格', '失格': '失格'}


def _non_finisher(status_text, umaban, odds_text, name=''):
    status = NON_FINISH_STATUS.get(status_text.strip())
    if not status or not umaban:
        return None
    entry = {'umaban': umaban, 'status': status, 'name': name}
    try:
        entry['odds'] = float(odds_text)
    except (TypeError, ValueError):
        pass
    return entry


def parse_non_finishers(page):
    """db.netkeiba.com の結果表から、着順の付かなかった馬を [{'umaban','status','odds'}] にする。

    finishing_order は着順のある馬だけを並べる（既存の集計がそれを前提にしている）。
    中止の馬を黙って落とすと「その馬がいなかったレース」として数えることになるので、
    別の欄に残す（2026-10-20 決定ログ。記事「競馬AIの開発過程④」と同じ考え方）。
    """
    table = jra_bias.find_result_table(page)
    if not table:
        return []
    rows = jra_bias.parse_rows(table)
    if len(rows) < 2:
        return []
    index = jra_bias.col_index(rows[0])
    if '着順' not in index or '馬番' not in index:
        return []
    out = []
    for cells in rows[1:]:
        if len(cells) <= max(index.values()) or cells[index['着順']].strip().isdigit():
            continue
        entry = _non_finisher(cells[index['着順']],
                              int(re.sub(r'\D', '', cells[index['馬番']]) or 0),
                              cells[index['単勝']] if '単勝' in index else None,
                              cells[index['馬名']] if '馬名' in index else '')
        if entry:
            out.append(entry)
    return out


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


def parse_nk_payouts(page):
    """race.netkeiba.com の払戻表（Payout_Detail_Table）を parse_payouts と同じ形にする。

    組み合わせは Result セルの中で、馬連・ワイド等は <ul> 1つが1組、
    単勝・複勝は数字の入った <span> 1つが1頭。金額は Payout セルの <br> 区切り。
    """
    payouts = {}
    for table in re.findall(
            r'<table[^>]*class="[^"]*Payout_Detail_Table[^"]*"[^>]*>(.*?)</table>', page, re.S):
        for row in re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.S):
            label = re.search(r'<th[^>]*>(.*?)</th>', row, re.S)
            result = re.search(r'<td class="Result">(.*?)</td>', row, re.S)
            amount = re.search(r'<td class="Payout">(.*?)</td>', row, re.S)
            if not (label and result and amount):
                continue
            bet_type = BET_TYPE_ALIASES.get(re.sub(r'\s+', '', jra_bias.strip_tags(label.group(1))))
            if not bet_type:
                continue
            groups = re.findall(r'<ul>(.*?)</ul>', result.group(1), re.S)
            if groups:
                combinations = [[int(n) for n in re.findall(r'<span>(\d+)</span>', g)]
                                for g in groups]
            else:
                combinations = [[int(n)] for n in re.findall(r'<span>(\d+)</span>', result.group(1))]
            amounts = [int(re.sub(r'[^\d]', '', a)) for a in _split_lines(amount.group(1))
                       if re.sub(r'[^\d]', '', a)]
            entries = [{'combination': c, 'yen': y}
                       for c, y in zip(combinations, amounts) if c]
            if entries:
                payouts[bet_type] = entries
    return payouts


def _nk_finishing_order(page, race_id):
    parsed = jra_bias.parse_nk_result(page, race_id)
    if not parsed:
        return []
    order = []
    for h in parsed['horses']:
        entry = {'rank': h['着順'], 'umaban': h['馬番'], 'name': h['馬名']}
        if str(h.get('人気', '')).isdigit():
            entry['ninki'] = int(h['人気'])
        try:
            entry['odds'] = float(h['単勝'])
        except (TypeError, ValueError):
            pass
        order.append(entry)
    return order


def _nk_non_finishers(page):
    """race.netkeiba.com の結果ページから、着順の付かなかった馬（parse_non_finishers と同じ形）。"""
    out = []
    for row in re.split(r'<tr[^>]*class="HorseList"', page)[1:]:
        row = row.split('</tr>')[0]
        rank = re.search(r'<div class="Rank">\s*([^<]*?)\s*</div>', row)
        num = re.search(r'<td class="Num Txt_C">\s*<div>\s*(\d+)', row)
        if not rank or rank.group(1).isdigit() or not num:
            continue
        odds = re.search(r'<td class="Odds Txt_R">\s*<span[^>]*>(.*?)</span>', row, re.S)
        name = re.search(r'HorseNameSpan">(.*?)</span>', row, re.S)
        entry = _non_finisher(rank.group(1), int(num.group(1)),
                              jra_bias.strip_tags(odds.group(1)).strip() if odds else None,
                              jra_bias.strip_tags(name.group(1)).strip() if name else '')
        if entry:
            out.append(entry)
    return out


def fetch(race_id, fetcher=None):
    """1レースの確定結果。まだ出ていなければ None。

    まず db.netkeiba.com を読み、着順が取れなければ race.netkeiba.com の
    結果ページを読む。db 側は 2026-09-19 以降のレースで着順が取れなくなり、
    週次レビューで中央のレースが軒並み「結果未確定」になっていた
    （2026-09-21締めの週で12レース）。race.netkeiba.com は確定直後から
    着順と払戻を載せる（jra_bias.py live と同じページ）。
    """
    try:
        page = (fetcher or jra_bias.fetch_html)(RACE_URL.format(race_id=race_id))
    except Exception as exc:
        page = ''
        db_error = exc
    else:
        db_error = None

    order = parse_finishing_order(page) if page else []
    if order:
        return {
            'race_id': race_id,
            'finishing_order': order,
            'non_finishers': parse_non_finishers(page),
            'payouts': parse_payouts(page),
        }

    try:
        page = (fetcher or jra_bias.fetch_html_utf8)(
            jra_bias.NK_RESULT_URL.format(race_id=race_id))
    except Exception as exc:
        raise ResultsError(f'{race_id} の結果を取得できませんでした: '
                           f'db={db_error} race={exc}')
    order = _nk_finishing_order(page, race_id)
    if not order:
        return None
    return {
        'race_id': race_id,
        'finishing_order': order,
        'non_finishers': _nk_non_finishers(page),
        'payouts': parse_nk_payouts(page),
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
