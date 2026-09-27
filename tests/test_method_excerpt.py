#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""朝タスク用の抜粋版が、原本 予想メソッド.md の承認済み改訂を取りこぼしていないか。

朝タスク（印を打つセッション）は原本を読まず、抜粋版だけを読む。原本に承認済みの
改訂を入れても、抜粋版へ写し忘れると**改訂は黙って効かない**。2026-09-22 に承認した
第1章・第2章・第12章の追加が、2026-09-27 まで抜粋版に一度も写されていなかった
（その間の朝タスクは旧ルールのまま印を打っていた）。

抜粋版は章番号を振り直しているので全文一致は比べない。原本の「承認済み」と
書かれた行の太字の見出し句が、抜粋版のどこかにあることだけを確かめる。
買い目の章（原本の第7章・第13章）は抜粋版の対象外なので除く。
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGINAL = os.path.join(ROOT, '予想メソッド.md')
EXCERPT = os.path.join(ROOT, 'docs', '予想メソッド_朝タスク抜粋版.md')

# 抜粋版に写さない章（原本の章番号）
EXCLUDED_CHAPTERS = {7, 13}


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def approved_phrases(text):
    chapter = None
    for line in text.split('\n'):
        m = re.match(r'##\s*(\d+)\.', line)
        if m:
            chapter = int(m.group(1))
            continue
        if chapter in EXCLUDED_CHAPTERS or '承認済み' not in line:
            continue
        bold = re.search(r'\*\*(.+?)\*\*', line)
        if bold:
            yield chapter, bold.group(1)


def test_承認済みの改訂が抜粋版に写されている():
    excerpt = read(EXCERPT)
    phrases = list(approved_phrases(read(ORIGINAL)))
    assert phrases, '原本から承認済みの行を1つも拾えていない（見出しの形が変わった？）'
    missing = [f'原本第{ch}章: {p}' for ch, p in phrases if p not in excerpt]
    assert not missing, '抜粋版に写されていない承認済みの改訂:\n' + '\n'.join(missing)


def test_オッズ帯を本命の選び方の基準として載せていない():
    """2026-09-16 東京記念で、抜粋版の見出し「◎候補評価の参考値」を根拠に
    1.7倍の1番人気から◎を外した（その馬が1着）。原本ではこの帯は単勝を買う目安。"""
    headings = [l for l in read(EXCERPT).split('\n') if l.startswith('#')]
    assert not [h for h in headings if '◎候補評価' in h]
