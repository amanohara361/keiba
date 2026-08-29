#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check.py の検算結果を、日付ごとのMarkdownファイルとして書き出す。

docs/index.html（GitHub Pages）は「最新1回分」しか見られない設計だが、
GitHub上でリポジトリのファイルをそのまま見て回ったときに、data/bets/や
data/checks/の生JSONがコードとして表示されて読みにくい、という指摘を受けて
追加した（2026-08-29）。data/review/のweekly reviewと同じ発想で、日付ごとに
1ファイル残せば、過去分もGitHub上でそのまま読める。

report_html.py と同じ RaceVerdict から数字を取るだけで、判断には関与しない
（第13章の判断はしない、という check.py 全体の方針をそのまま引き継ぐ）。
"""

import os

import conditions as conditions_module
import discipline
import odds as odds_module
import report_html

CHECKS_MD_DIR = os.path.join('data', 'checks_md')


def _status_label(verdict):
    if verdict.blocked:
        return '見送り（BLOCK）'
    if not verdict.race.bets:
        return '印のみ（買い目なし）'
    if verdict.warnings:
        return '要検討'
    return 'クリア'


def _race_section(verdict, nar_names):
    race = verdict.race
    name_of = report_html._horse_name(verdict, nar_names)

    meta_bits = []
    if race.venue and race.race_no:
        meta_bits.append(f'{race.venue}{race.race_no}R')
    cond = verdict.conditions or {}
    if cond.get('surface') and cond.get('distance'):
        meta_bits.append(f"{cond['surface']}{cond['distance']}m")
    if race.start_time:
        meta_bits.append(f'発走 {race.start_time}')
    meta_line = ' / '.join(meta_bits)

    lines = [f'## {race.name}', '']
    if meta_line:
        lines.append(f'{meta_line}')
    lines.append(f'状態: **{_status_label(verdict)}** / 勝負度: {race.confidence or "未評価"}')
    if race.subjective_hit_rate is not None:
        lines.append(f'主観的中率: {race.subjective_hit_rate:.0%}')
    if cond.get('going'):
        heavy = '（要注意）' if conditions_module.is_heavy(cond) else ''
        lines.append(f'馬場: {cond["going"]}{heavy}')
    lines.append('')

    marks = []
    for m in race.marks:
        nm = name_of(m['umaban'])
        marks.append(f'{m["mark"]}{m["umaban"]}{nm or ""}')
    if marks:
        lines.append('印: ' + ' '.join(marks))
        lines.append('')

    if race.bets:
        lines.append('| 券種 | 組み合わせ | 実オッズ | 金額 |')
        lines.append('|---|---|---|---|')
        for bet, odds_value in zip(race.bets, verdict.bet_odds):
            shown = f'{odds_value:.1f}倍' if odds_value else 'オッズ取得できず'
            lines.append(f'| {bet.type} | {bet.combination} | {shown} | {bet.stake}円 |')
        lines.append('')
        if verdict.composite is not None:
            ev = verdict.expected_value
            ev_text = f'{ev:.2f}' if ev is not None else '-'
            lines.append(
                f'合成オッズ {verdict.composite:.2f}倍（基準{discipline.MIN_COMPOSITE_ODDS}倍）'
                f' / 期待値 {ev_text}（基準{discipline.MIN_EXPECTED_VALUE}）'
            )
        else:
            status = (verdict.meta or {}).get('status')
            label = odds_module.STATUS_LABELS.get(status, status or '未取得')
            lines.append(f'実オッズ: {label}（合成オッズ・期待値は未計算）')
        lines.append('')
    else:
        lines.append('買い目なし（勝負度Cまたは印のみ）')
        lines.append('')

    for f in verdict.blocks:
        lines.append(f'- **BLOCK** — {f.message}' + (f'（{f.remedy}）' if f.remedy else ''))
    for f in verdict.warnings:
        lines.append(f'- **WARN** — {f.message}' + (f'（{f.remedy}）' if f.remedy else ''))
    if not verdict.blocked and not verdict.warnings:
        if race.bets:
            lines.append('- 第13章の規律をすべてクリアしています')
        else:
            lines.append('- 見送り（買い目なし。規律クリアではない）')

    if getattr(verdict, 'bet_note', None):
        lines.append('')
        lines.append(f'買い目メモ: {verdict.bet_note}')
    if race.note:
        lines.append('')
        lines.append(f'メモ: {race.note}')

    lines.append('')
    return '\n'.join(lines)


def render(sheet, verdicts, now):
    day = sheet.date
    nar_names = report_html._nar_horse_names(day) if any(r.org == 'nar' for r in sheet.races) else {}

    blocked = [v for v in verdicts if v.blocked]
    orderable = [v for v in verdicts if v.race.bets and not v.blocked]

    lines = [
        f'# {day.isoformat()} 検算結果', '',
        f'対象 {len(verdicts)}レース / 購入 {len(orderable)}件 / {len(blocked)}件 BLOCK'
        f' / 検算時刻 {now:%H:%M} JST（買い目作成 {sheet.generated_at or "-"}）',
        '', '本検算は買い目設計の規律のみを見ています。印の判断には関与しません。', '',
    ]
    for v in verdicts:
        lines.append(_race_section(v, nar_names))
    return '\n'.join(lines)


def render_missing(day, now):
    return (
        f'# {day.isoformat()} 検算結果\n\n'
        f'**朝の買い目が届いていません**（`data/bets/{day.isoformat()}.json` が見つかりません）\n\n'
        f'確認時刻 {now:%H:%M} JST\n'
    )


def path_for(day):
    return os.path.join(CHECKS_MD_DIR, f'{day.isoformat()}.md')


def save(md_text, path):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(md_text)
    return path
