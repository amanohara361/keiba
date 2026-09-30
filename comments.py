#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重賞の陣営コメントを、無料で公開されているスポーツ紙の記事から集める。

予想メソッド第1章 手順2「厩舎コメントの確認」の材料。netkeiba の厩舎コメントは
プレミアム会員向けで取れない（2026-09-30 の調査）。代わりに、スポニチ（Sponichi Annex）
が重賞ごとに出している次の2種類を使う。

- 「【レース名】追ってひと言」（G1では「陣営のひと言」）… 出走馬ごとに1行、
  `▼馬名（話し手）コメント`。追い切り後の調教師・助手・騎手の談話で、ほぼ全頭がそろう。
- 「【レース名】馬名 …」「【レース名】（馬番）馬名 …」… 各馬の追い切り・枠順確定後の記事。
  見出しに要点と談話が入っているので、見出しだけを持つ（本文は取らない）。
- **対象日の前日までに出た記事だけを使う。** 当日以降の記事（レース結果・回顧）を
  混ぜないため。カードは前夜に作るので、通常はそもそも存在しない。

**判断はしない。** 集めた談話をカードの各馬に付けるだけで、どう読むかは朝タスクの仕事。
取れなかったものは空のまま（推測で埋めない）。取得に失敗してもカード作りは止めない。

GitHub Actions からしか届かない（セッションのサンドボックスからは egress で遮断）。
スポーツ紙の記事なので、保存するのは1頭あたり短い談話と見出し・URLだけにする。

外部ライブラリは使わない。
"""

import logging
import re
import time
from datetime import timedelta

import form as form_module

logger = logging.getLogger(__name__)

SOURCE = 'スポニチ'
BASE = 'https://www.sponichi.co.jp'
INDEX_URL = BASE + '/gamble/news/{y:04d}/{m:02d}/{d:02d}/'

# 重賞の追い切り記事は水曜〜金曜に出る。前夜のカード作りから6日さかのぼれば足りる。
LOOKBACK_DAYS = 6
GRADES = ('G1', 'G2', 'G3')
MAX_TEXT = 200          # 1コメントあたりの保存文字数の上限
REQUEST_INTERVAL = 1.5  # form.py と同じ

ANCHOR = re.compile(r'(?s)<a[^>]+href=["\'](/gamble/news/[^"\']+/kiji/[^"\']+)["\'][^>]*>(.*?)</a>')
TITLE = re.compile(r'【([^】]+)】\s*(.+)')
STAMP = re.compile(r'［\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}:\d{2})\s*］')
ROUNDUP = re.compile(r'^(追って|陣営の)ひと言')
NUMBERED = re.compile(r'^[（(](\d{1,2})[）)]\s*')
HITOKOTO = re.compile(r'^▼\s*([^（(]+?)\s*[（(]([^）)]+)[）)]\s*(.+)$')


def normalize_race_name(name):
    """カードの略称（例：スプリンター）と記事の表記（スプリンターズS）をそろえる。"""
    name = re.sub(r'\s+', '', name or '')
    name = name.replace('ステークス', 'S')
    name = re.sub(r'[（(].*?[）)]', '', name)
    return name.rstrip('S')


def same_race(card_name, article_name):
    a, b = normalize_race_name(card_name), normalize_race_name(article_name)
    if len(a) < 3 or len(b) < 3:
        return False
    return a.startswith(b) or b.startswith(a)


def _text_lines(html):
    """本文を行に分ける。head・コメント・script は捨てる（meta にも ▼ 行が入っているため）。"""
    html = re.sub(r'(?s)<head.*?</head>', '', html)
    html = re.sub(r'(?s)<!--.*?-->', '', html)
    html = re.sub(r'(?s)<(script|style|noscript)[^>]*>.*?</\1>', '', html)
    html = re.sub(r'(?i)<br\s*/?>|</p>|</div>|</li>|<p[^>]*>', '\n', html)
    lines = []
    for line in html.split('\n'):
        text = form_module.strip_tags(line).replace('　', ' ').strip()
        if text:
            lines.append(text)
    return lines


def parse_index(page):
    """日別一覧から重賞記事を拾う。[{race, rest, url, published}]（重複は除く）。"""
    articles, seen = [], set()
    for href, body in ANCHOR.findall(page):
        label = form_module.strip_tags(body)
        match = TITLE.search(label)
        if not match or href in seen:
            continue
        seen.add(href)
        stamp = STAMP.search(label)
        rest = STAMP.sub('', match.group(2)).replace('競馬', '').strip()
        articles.append({
            'race': match.group(1).strip(),
            'rest': rest,
            'url': BASE + href,
            'published': (f'{stamp.group(1)}-{int(stamp.group(2)):02d}-{int(stamp.group(3)):02d} '
                          f'{stamp.group(4)}') if stamp else None,
        })
    return articles


def parse_hitokoto(page):
    """「追ってひと言」の本文から [{horse, speaker, text}] を取る。"""
    out, seen = [], set()
    for line in _text_lines(page):
        match = HITOKOTO.match(line)
        if not match:
            continue
        horse = match.group(1).strip()
        if horse in seen:
            continue
        seen.add(horse)
        out.append({'horse': horse, 'speaker': match.group(2).strip(),
                    'text': match.group(3).strip()[:MAX_TEXT]})
    return out


def headline_horse(rest):
    """各馬の記事の見出しから (馬番 or None, 馬名) を取る。

    「馬名 要点…」と、枠順確定後の「（10）馬名 要点…」の2通りがある。
    """
    if not rest:
        return None, ''
    match = NUMBERED.match(rest)
    umaban = match.group(1) if match else None
    rest = rest[match.end():] if match else rest
    return umaban, rest.split(' ')[0].strip()


def gather_index(day, fetch=None, sleep=time.sleep):
    """対象日の前 LOOKBACK_DAYS 日ぶんの一覧を読む。読めなかった日は飛ばす。"""
    fetch = fetch or form_module._fetch
    articles = []
    for back in range(LOOKBACK_DAYS, -1, -1):
        d = day - timedelta(days=back)
        url = INDEX_URL.format(y=d.year, m=d.month, d=d.day)
        try:
            articles += parse_index(fetch(url))
        except Exception as exc:
            logger.warning('%s を読めませんでした: %s', url, exc)
        sleep(REQUEST_INTERVAL)
    unique = {}
    for a in articles:
        unique.setdefault(a['url'], a)
    return list(unique.values())


def comments_for_race(race, articles, fetch=None, sleep=time.sleep, day=None):
    """1レース分。{'source','articles','by_umaban'}。該当記事が無ければ None。"""
    fetch = fetch or form_module._fetch
    cutoff = day.isoformat() if day else None
    mine = [a for a in articles if same_race(race.get('name'), a['race'])
            and not (cutoff and a['published'] and a['published'][:10] >= cutoff)]
    if not mine:
        return None

    by_name = {e.get('name'): str(e.get('umaban')) for e in race.get('entries') or []
               if e.get('name') and e.get('umaban') is not None}
    by_umaban = {}

    def add(name, item, umaban=None):
        # 馬番が見出しにあっても、馬名がカードと食い違えば付けない（取り違えを避ける）
        if umaban and by_name.get(name) not in (None, umaban):
            return
        umaban = umaban if umaban and name in by_name else by_name.get(name)
        if umaban:
            by_umaban.setdefault(umaban, []).append(item)

    for a in mine:
        if ROUNDUP.match(a['rest']):
            try:
                rows = parse_hitokoto(fetch(a['url']))
            except Exception as exc:
                logger.warning('%s を読めませんでした: %s', a['url'], exc)
                continue
            finally:
                sleep(REQUEST_INTERVAL)
            for row in rows:
                add(row['horse'], {'kind': '追ってひと言', 'speaker': row['speaker'],
                                   'text': row['text'], 'url': a['url'],
                                   'published': a['published']})
        else:
            umaban, name = headline_horse(a['rest'])
            add(name, {'kind': '見出し', 'text': a['rest'][:MAX_TEXT],
                       'url': a['url'], 'published': a['published']}, umaban=umaban)

    return {
        'source': SOURCE,
        'articles': [{'title': f"【{a['race']}】{a['rest']}", 'url': a['url'],
                      'published': a['published']} for a in mine],
        'by_umaban': by_umaban,
    }


def attach(races, day, fetch=None, sleep=time.sleep):
    """重賞（G1〜G3）で出走馬がそろっているレースに comments を付ける。失敗しても落ちない。"""
    targets = [r for r in races if r.get('grade') in GRADES and r.get('entries')]
    if not targets:
        return 0
    try:
        articles = gather_index(day, fetch=fetch, sleep=sleep)
    except Exception as exc:
        logger.warning('陣営コメントの一覧を読めませんでした: %s', exc)
        return 0
    count = 0
    for race in targets:
        try:
            race['comments'] = comments_for_race(race, articles, fetch=fetch, sleep=sleep, day=day)
        except Exception as exc:
            logger.warning('%s の陣営コメントを取れませんでした: %s', race.get('race_id'), exc)
            race['comments'] = None
        if race['comments']:
            count += 1
            logger.info('%s %s：陣営コメント %d頭', race.get('venue'), race.get('name'),
                        len(race['comments']['by_umaban']))
    return count
