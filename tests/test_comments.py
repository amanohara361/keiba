#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重賞の陣営コメント（comments.py）。ネットワークには接続しない。

HTMLは 2026-10-01 に Actions の probe url-raw で見たスポニチの実物の形
（日別一覧の <a href=".../kiji/...">…【レース名】見出し［日時］…</a>、
「追ってひと言」の本文は ▼馬名（話し手）コメント の行、head の meta にも
先頭の1行が入っている）を写したもの。談話の文面は実物から一部を引用している。
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import comments  # noqa: E402

INDEX = '''
<li data-component="list-item" class="cateGamble">
  <a href="/gamble/news/2026/10/01/kiji/20261001s00004048060000c.html">
    <figure><img src="/gamble/images/thumbnail/005.webp"></figure>
    <p class="title">【毎日王冠】アドマイヤクワッズ 坂路をサラリ 安田助手「いつも通りの調整で順調」</p>
    <p class="date">［ 2026年10月1日 05:24 ］&nbsp;競馬</p>
  </a>
</li>
<li data-component="list-item" class="cateGamble">
  <a href="/gamble/news/2026/10/01/kiji/20261001s00004048061000c.html">
    <p class="title">【毎日王冠】追ってひと言</p>
    <p class="date">［ 2026年10月1日 05:23 ］&nbsp;競馬</p>
  </a>
</li>
<li><a href="/gamble/news/2026/10/01/kiji/20261001s00004048061000c.html">【毎日王冠】追ってひと言</a></li>
<li><a href="/gamble/news/2026/10/01/kiji/20261001s00004050055000c.html">【凱旋門賞】メイショウタバル 戦闘モード［ 2026年10月1日 05:30 ］</a></li>
<li><a href="/gamble/news/2026/10/01/kiji/20261001s00004050065000c.html">アメリカンステージも米国遠征へ［ 2026年10月1日 05:15 ］</a></li>
'''

ARTICLE = '''<html><head>
<meta name="description" content="　▼ヴィンセンシオ（森一師）道中力むところがあって、追い出してからも体がうまく使えていなかった。"/>
</head><body>
<!-- ▼▼HEADER▼▼ -->
<div class="article">
<h1>【毎日王冠】追ってひと言</h1>
<p>　▼ヴィンセンシオ（森一師）道中力むところがあって、追い出してからも体がうまく使えていなかった。まだ改善の余地がある。<br>
　▼エルトンバローズ（杉山晴師）坂路でやる予定だったんですが、朝一番、馬場が悪かったのでCWコースへ切り替えました。<br>
　▼シャンパンカラー（内田）以前に比べて大人になった。千六はこなしているし、少し距離が延びるくらいなら問題ない。</p>
<p>　▼ロングラン（中野助手）当該週なので直線は時計を出さずに併せる程度。</p>
</div></body></html>'''


def race(name='毎日王冠', grade='G2'):
    return {
        'race_id': '202605040811', 'venue': '東京', 'name': name, 'grade': grade,
        'entries': [
            {'umaban': 1, 'name': 'ヴィンセンシオ'},
            {'umaban': 4, 'name': 'エルトンバローズ'},
            {'umaban': 7, 'name': 'アドマイヤクワッズ'},
            {'umaban': 9, 'name': 'シャンパンカラー'},
        ],
    }


def fake_fetch(pages):
    def fetch(url):
        if url in pages:
            return pages[url]
        raise OSError('not found: ' + url)
    return fetch


def test_日別一覧から重賞記事だけを拾う():
    arts = comments.parse_index(INDEX)
    assert [(a['race'], a['rest']) for a in arts] == [
        ('毎日王冠', 'アドマイヤクワッズ 坂路をサラリ 安田助手「いつも通りの調整で順調」'),
        ('毎日王冠', '追ってひと言'),
        ('凱旋門賞', 'メイショウタバル 戦闘モード'),
    ]
    assert arts[1]['published'] == '2026-10-01 05:23'
    assert arts[1]['url'].startswith('https://www.sponichi.co.jp/gamble/news/')


def test_追ってひと言を馬ごとに読み_metaの行は数えない():
    rows = comments.parse_hitokoto(ARTICLE)
    assert [r['horse'] for r in rows] == ['ヴィンセンシオ', 'エルトンバローズ', 'シャンパンカラー', 'ロングラン']
    assert rows[0]['speaker'] == '森一師'
    assert rows[0]['text'].endswith('まだ改善の余地がある。')
    assert rows[2]['speaker'] == '内田'


def test_レース名の略称と記事の表記を突き合わせる():
    assert comments.same_race('スプリンター', 'スプリンターズS')
    assert comments.same_race('シリウスS', 'シリウスステークス')
    assert comments.same_race('毎日王冠', '毎日王冠')
    assert not comments.same_race('毎日王冠', '京都大賞典')
    assert not comments.same_race('S', 'スプリンターズS')


def test_カードの馬番に談話と見出しを付ける():
    index_url = 'https://www.sponichi.co.jp/gamble/news/2026/10/01/'
    article_url = 'https://www.sponichi.co.jp/gamble/news/2026/10/01/kiji/20261001s00004048061000c.html'
    races = [race(), race(name='2歳未勝利', grade=None)]
    n = comments.attach(races, date(2026, 10, 4),
                        fetch=fake_fetch({index_url: INDEX, article_url: ARTICLE}),
                        sleep=lambda s: None)
    assert n == 1
    got = races[0]['comments']
    assert got['source'] == 'スポニチ'
    assert got['by_umaban']['1'][0] == {
        'kind': '追ってひと言', 'speaker': '森一師',
        'text': '道中力むところがあって、追い出してからも体がうまく使えていなかった。まだ改善の余地がある。',
        'url': article_url, 'published': '2026-10-01 05:23'}
    assert got['by_umaban']['7'][0]['kind'] == '見出し'
    assert '安田助手' in got['by_umaban']['7'][0]['text']
    assert '9' in got['by_umaban']
    assert 'comments' not in races[1]            # 重賞でないレースには付けない


def test_記事が無ければNone_一覧が読めなくても落ちない():
    races = [race(name='京都大賞典')]
    comments.attach(races, date(2026, 10, 4), fetch=fake_fetch({}), sleep=lambda s: None)
    assert races[0]['comments'] is None


def test_長い談話は切り詰める():
    long_line = '<p>▼ヴィンセンシオ（森一師）' + 'あ' * 500 + '</p>'
    rows = comments.parse_hitokoto(long_line)
    assert len(rows[0]['text']) == comments.MAX_TEXT


def test_見出しの馬番と馬名を読む():
    assert comments.headline_horse('（10）レイピア 坂路4F52秒3') == ('10', 'レイピア')
    assert comments.headline_horse('ルガル 心身共に衰えなし') == (None, 'ルガル')


def test_G1の陣営のひと言と番号付き見出しと当日以降の記事():
    index = INDEX.replace('【毎日王冠】追ってひと言', '【毎日王冠】陣営のひと言') + \
        '<a href="/gamble/news/2026/10/03/kiji/a.html">【毎日王冠】（9）シャンパンカラー 外枠歓迎［ 2026年10月3日 05:00 ］</a>' + \
        '<a href="/gamble/news/2026/10/04/kiji/b.html">【毎日王冠】7番人気シャンパンカラー3着［ 2026年10月4日 17:00 ］</a>' + \
        '<a href="/gamble/news/2026/10/03/kiji/c.html">【毎日王冠】（4）ヴィンセンシオ 取り違え［ 2026年10月3日 05:00 ］</a>'
    article_url = 'https://www.sponichi.co.jp/gamble/news/2026/10/01/kiji/20261001s00004048061000c.html'
    races = [race()]
    comments.attach(races, date(2026, 10, 4),
                    fetch=fake_fetch({'https://www.sponichi.co.jp/gamble/news/2026/10/01/': index,
                                      article_url: ARTICLE}),
                    sleep=lambda s: None)
    got = races[0]['comments']['by_umaban']
    assert got['1'][0]['kind'] == '追ってひと言'           # 「陣営のひと言」も読む
    texts9 = [c['text'] for c in got['9']]
    assert '（9）シャンパンカラー 外枠歓迎' in texts9      # 番号付きの見出し
    assert not any('3着' in x for x in texts9)             # 当日以降の記事は使わない
    assert all('取り違え' not in c['text'] for c in got.get('4', []))  # 馬番と馬名が食い違えば付けない
