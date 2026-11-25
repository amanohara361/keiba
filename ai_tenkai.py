#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""netkeiba 出馬表ページの「AI展開予測」（無料部分）を読む（2026-11-25）。

出馬表（race.netkeiba.com / nar.netkeiba.com の shutuba.html）には、AI展開予測の
隊列が JavaScript の座標として埋め込まれている。無料で読めるのは次の2通り×3時点。
- 'plain'  … チェックなし（基本の予測）
- 'jockey' … 「騎手傾向を考慮」
- 時点は 'start'（スタート後）・'c3'（3コーナー）・'c4'（4コーナー）

各馬の値
- gap   … 先頭からの差（0＝先頭、100＝最後方。ページの left%）
- lane  … 内外（0＝最内。ページの top% を -4,12,30,46,64… の順で数えたもの）
- speed … 4コーナーで付く加速の印（SpeedUp_01〜03。数字の意味は未確認）

出遅れ率を考慮した版・レースラップ予測・波乱度は有料会員向けで、ページにはダミー
（出走しない馬番が入る）しか無い。取らない。出走馬に無い馬番が出てきた版は
ダミーとみなして捨てる。

使い方: python ai_tenkai.py <race_id>    （12桁。地方は場コード30以上で nar を使う）
"""
import json
import re
import sys

import form

JRA_URL = 'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'
NAR_URL = 'https://nar.netkeiba.com/race/shutuba.html?race_id={race_id}'

MODES = {'plain': 'チェックなし', 'jockey': '騎手傾向'}
PHASES = {'start': '１', 'c3': '３', 'c4': '４'}

_LINE = re.compile(
    r"#Horse(\d+)\"\)\.css\(\{\s*'top'\s*:\s*'(-?[\d.]+)%'\s*,\s*'left'\s*:\s*'(-?[\d.]+)%'\s*,?\s*\}\)"
    r"\.(?:append\('<span class=\"([^\"]*)\"|children)")
_UMABAN = re.compile(r'<td class="Umaban\d*[^"]*"[^>]*>\s*(\d+)\s*</td>')
# 有料版（出遅れ率）の見本の行。無料版の行がこれと同じならダミーとみなす。
_PAID_LABELS = ('出遅れ率', '出遅れ騎手')


def is_nar(race_id):
    return int(str(race_id)[4:6]) >= 30


def url_for(race_id):
    return (NAR_URL if is_nar(race_id) else JRA_URL).format(race_id=race_id)


def _lane_index(tops):
    order = sorted(set(tops))
    return {t: i for i, t in enumerate(order)}


def parse(html):
    """ページから {'runners': [...], 'plain': {...}, 'jockey': {...}} を作る。無ければ None。

    各モードは {'start': [{'umaban','gap','lane','speed'}, ...（前から順）], 'c3': ..., 'c4': ...}。
    """
    start = html.find('function updateHorsePosition')
    if start < 0:
        return None
    end = html.find('// コーナーのクリックイベント', start)
    block = html[start:end if end > 0 else None]
    runners = sorted({int(u) for u in _UMABAN.findall(html)})

    paid = set()
    for label in _PAID_LABELS:
        for m in re.finditer(r'\n(\$\("#Horse[^\n]*)\n\s*// console\.log\("' + label + r'[１３４]コーナー', block):
            paid.add(m.group(1).strip())

    out = {'runners': runners}
    for mode, label in MODES.items():
        phases = {}
        for phase, num in PHASES.items():
            m = re.search(r'\n(\$\("#Horse[^\n]*)\n\s*// console\.log\("' + label + num + 'コーナー', block)
            if not m:
                continue
            if m.group(1).strip() in paid:
                continue  # 有料版の見本と同じ＝ダミー
            rows = [(int(u), float(t), float(l), sp or '') for u, t, l, sp in _LINE.findall(m.group(1))]
            if not rows or (runners and any(u not in runners for u, *_ in rows)):
                continue  # ダミー（有料部分の見本）
            lanes = _lane_index([t for _, t, _, _ in rows])
            rows.sort(key=lambda r: (r[2], r[1], r[0]))
            phases[phase] = [
                {'umaban': u, 'gap': round(l, 2), 'lane': lanes[t],
                 'speed': (re.search(r'SpeedUp_(\d+)', sp) or [None, None])[1]}
                for u, t, l, sp in rows
            ]
        if phases:
            out[mode] = phases
    return out if len(out) > 1 else None


def fetch(race_id, opener=None):
    return parse(form._fetch(url_for(race_id), opener=opener))


def order(pred, mode='plain', phase='start'):
    """前から順の馬番リスト。無ければ []。"""
    return [r['umaban'] for r in ((pred or {}).get(mode) or {}).get(phase) or []]


if __name__ == '__main__':
    result = fetch(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, indent=1))
