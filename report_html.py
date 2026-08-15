#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check.py の検算結果を、1枚のHTMLレポートとして書き出す。

メール（check.py の format_report）はテキストのままにする。**このモジュールは
メールを置き換えない。** GitHub Pages（docs/index.html）で「最新の検算結果」を
見やすく確認できるようにするための、もう1つの出力先を追加するだけ。

数字はすべて check.py が computed した RaceVerdict からそのまま取る。
このモジュール自身はオッズも馬場も取りに行かない（第13章の判断には関与しない、
という check.py 全体の方針をそのまま引き継ぐ）。

馬名は地方（NAR）だけ表示できる。data/cards/nar/YYYY-MM-DD.json に馬名入りの
出馬表がすでにローカルにあるため、追加のアクセスなしで引ける。中央は
verdict.forms が馬場が渋ったときしか埋まらない（check.py の設計）ため、
埋まっていれば使い、無ければ馬番だけを表示する。
"""

import html
import json
import os
from datetime import datetime

import bets
import conditions as conditions_module
import discipline
import nar_card
import odds as odds_module

HTML_PATH = os.path.join('docs', 'index.html')

_TITLE = "地方競馬・中央 直前検算"

_CSS = """
:root {
  --bg: #E4E7DE; --surface: #F8F9F3; --surface-2: #ECEEE4;
  --border: #C9CDBD; --border-soft: #D7DACC;
  --text: #1C2320; --text-muted: #566057;
  --accent: #2C5560; --clay: #A15A34;
  --good: #3E7D45; --good-soft: #E1EBE0;
  --warn: #B8862E; --warn-soft: #F3EAD7;
  --critical: #B23B2E; --critical-soft: #F3E1DC;
  --shadow: 0 1px 2px rgba(28,35,32,0.06), 0 4px 14px rgba(28,35,32,0.05);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #12181A; --surface: #1A2225; --surface-2: #212B2E;
    --border: #33413F; --border-soft: #2A3634;
    --text: #E7ECE8; --text-muted: #93A29C;
    --accent: #7FB8C4; --clay: #DB9163;
    --good: #6FCB7A; --good-soft: #1E3021;
    --warn: #E0B15B; --warn-soft: #362B16;
    --critical: #F0776A; --critical-soft: #3A211D;
    --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 4px 18px rgba(0,0,0,0.25);
  }
}
:root[data-theme="dark"] {
  --bg: #12181A; --surface: #1A2225; --surface-2: #212B2E;
  --border: #33413F; --border-soft: #2A3634;
  --text: #E7ECE8; --text-muted: #93A29C;
  --accent: #7FB8C4; --clay: #DB9163;
  --good: #6FCB7A; --good-soft: #1E3021;
  --warn: #E0B15B; --warn-soft: #362B16;
  --critical: #F0776A; --critical-soft: #3A211D;
  --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 4px 18px rgba(0,0,0,0.25);
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: "Hiragino Kaku Gothic ProN", "Yu Gothic", "Noto Sans JP",
    -apple-system, BlinkMacSystemFont, sans-serif;
  line-height: 1.65; -webkit-font-smoothing: antialiased;
}
.serif { font-family: "Hiragino Mincho ProN", "Yu Mincho", "Noto Serif JP", Georgia, serif; }
.page { max-width: 760px; margin: 0 auto; padding: 48px 20px 80px; }
.eyebrow { font-size: 12.5px; letter-spacing: .09em; color: var(--text-muted); text-transform: uppercase; }
h1 { font-size: 28px; font-weight: 600; margin: 2px 0 0; text-wrap: balance; }
.masthead-sub { color: var(--text-muted); font-size: 14px; margin-top: 4px; }
.summary { display: grid; grid-template-columns: repeat(3,1fr); gap: 1px;
  background: var(--border-soft); border: 1px solid var(--border-soft);
  border-radius: 10px; overflow: hidden; margin: 32px 0 40px; }
.stat { background: var(--surface); padding: 16px 15px; display: flex; flex-direction: column; gap: 4px; }
.stat-label { font-size: 11.5px; color: var(--text-muted); }
.stat-value { font-size: 24px; font-variant-numeric: tabular-nums; font-weight: 600; }
.stat-value.critical { color: var(--critical); }
.stat-value.good { color: var(--good); }
.stat-note { font-size: 11.5px; color: var(--text-muted); }
.race { background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  box-shadow: var(--shadow); padding: 22px 22px 20px; margin-bottom: 22px; }
.race-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; flex-wrap: wrap; }
.race-title { font-size: 19px; font-weight: 600; text-wrap: balance; margin: 0 0 4px; }
.race-meta { color: var(--text-muted); font-size: 13px; font-variant-numeric: tabular-nums; }
.pill { display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px; border-radius: 999px;
  font-size: 12px; font-weight: 600; white-space: nowrap; }
.pill::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.pill-critical { background: var(--critical-soft); color: var(--critical); }
.pill-good { background: var(--good-soft); color: var(--good); }
.pill-warn { background: var(--warn-soft); color: var(--warn); }
.pill-neutral { background: var(--surface-2); color: var(--text-muted); }
.chips { display: flex; gap: 8px; flex-wrap: wrap; margin: 13px 0 17px; }
.chip { font-size: 12px; padding: 4px 10px; border-radius: 7px; background: var(--surface-2); color: var(--text-muted); }
.chip.warn { background: var(--warn-soft); color: var(--warn); font-weight: 600; }
.marks { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px,1fr)); gap: 8px; margin-bottom: 18px; }
.mark { background: var(--surface-2); border-radius: 8px; padding: 9px 10px; display: flex; flex-direction: column; gap: 2px; }
.mark-symbol { font-size: 12.5px; color: var(--accent); font-weight: 700; }
.mark-symbol .um { color: var(--text-muted); font-weight: 500; margin-left: 4px; font-variant-numeric: tabular-nums; }
.mark-name { font-size: 13px; font-weight: 600; }
.section-label { font-size: 11.5px; letter-spacing: .05em; text-transform: uppercase; color: var(--text-muted); margin: 0 0 8px; }
.table-wrap { overflow-x: auto; border: 1px solid var(--border-soft); border-radius: 8px; margin-bottom: 18px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th { text-align: left; background: var(--surface-2); color: var(--text-muted); font-weight: 600;
  font-size: 11.5px; padding: 8px 11px; border-bottom: 1px solid var(--border-soft); }
tbody td { padding: 8px 11px; border-bottom: 1px solid var(--border-soft); font-variant-numeric: tabular-nums; }
tbody tr:last-child td { border-bottom: none; }
td.odds { color: var(--clay); font-weight: 600; text-align: right; }
td.stake { text-align: right; color: var(--text-muted); }
.no-bets { font-size: 13px; color: var(--text-muted); background: var(--surface-2); border-radius: 8px;
  padding: 10px 12px; margin-bottom: 18px; }
.gauges { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 18px; }
@media (max-width: 480px) { .gauges { grid-template-columns: 1fr; } }
.gauge-label { display: flex; justify-content: space-between; align-items: baseline; font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }
.gauge-value { font-size: 14px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--text); }
.gauge-track { position: relative; height: 9px; border-radius: 5px; background: var(--surface-2); }
.gauge-fill { position: absolute; inset: 0 auto 0 0; border-radius: 5px; background: var(--critical); }
.gauge-fill.pass { background: var(--good); }
.gauge-threshold { position: absolute; top: -3px; bottom: -3px; width: 2px; background: var(--text-muted); opacity: .55; }
.gauge-threshold-label { font-size: 10px; color: var(--text-muted); margin-top: 4px; }
.odds-note { font-size: 12.5px; color: var(--text-muted); background: var(--surface-2); border-radius: 8px;
  padding: 9px 12px; margin-bottom: 18px; }
.findings { display: flex; flex-direction: column; gap: 8px; margin-bottom: 2px; }
.finding { border-radius: 8px; padding: 10px 12px; font-size: 12.5px; display: flex; flex-direction: column; gap: 3px; }
.finding.critical { background: var(--critical-soft); }
.finding.warn { background: var(--warn-soft); }
.finding.good { background: var(--good-soft); }
.finding.neutral { background: var(--surface-2); }
.finding-title { font-weight: 700; }
.finding.critical .finding-title { color: var(--critical); }
.finding.warn .finding-title { color: var(--warn); }
.finding.good .finding-title { color: var(--good); }
.finding.neutral .finding-title { color: var(--text-muted); }
.finding-remedy { color: var(--text-muted); }
.note { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--border-soft); font-size: 12px; color: var(--text-muted); }
.note-label { font-size: 10.5px; letter-spacing: .05em; text-transform: uppercase; color: var(--text-muted); margin-bottom: 5px; opacity: .85; }
footer { margin-top: 32px; padding-top: 18px; border-top: 1px solid var(--border-soft); font-size: 12px; color: var(--text-muted);
  display: flex; flex-direction: column; gap: 4px; }
footer code { background: var(--surface-2); padding: 1px 5px; border-radius: 4px; font-size: 11px; }
"""


def _esc(value):
    return html.escape(str(value)) if value is not None else ''


def _nar_horse_names(day):
    """当日の地方カードから {race_id: {umaban: 馬名}} を作る。ローカルファイルのみ、追加アクセスなし。"""
    path = nar_card.card_path(day)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            card = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    names = {}
    for race in card.get('races') or []:
        names[str(race.get('race_id'))] = {
            e['umaban']: e['name'] for e in race.get('entries') or [] if e.get('name')
        }
    return names


def _horse_name(verdict, nar_names):
    if verdict.race.org == 'nar':
        table = nar_names.get(str(verdict.race.race_id)) or {}
    else:
        table = {u: d.get('name') for u, d in (verdict.forms or {}).items()}

    def lookup(umaban):
        return table.get(umaban)
    return lookup


def _gauge(label, value, threshold, unit=''):
    """基準線つきのゲージバー。value が None のときは呼び出し側で使わない。"""
    scale = threshold / 0.75
    pct = max(0.0, min(100.0, value / scale * 100))
    passed = value >= threshold
    state = 'クリア' if passed else '未達'
    return f'''
      <div>
        <div class="gauge-label"><span>{_esc(label)}</span><span class="gauge-value">{value:.2f}{unit}</span></div>
        <div class="gauge-track">
          <div class="gauge-fill {"pass" if passed else ""}" style="width:{pct:.1f}%"></div>
          <div class="gauge-threshold" style="left:75%"></div>
        </div>
        <div class="gauge-threshold-label">基準 {threshold}{unit} &mdash; {state}</div>
      </div>'''


def _status_pill(verdict):
    if verdict.blocked:
        return '<span class="pill pill-critical">見送り（BLOCK）</span>'
    if not verdict.race.bets:
        return '<span class="pill pill-neutral">印のみ（買い目なし）</span>'
    if verdict.warnings:
        return '<span class="pill pill-warn">要検討</span>'
    return '<span class="pill pill-good">クリア</span>'


def _race_block(verdict, nar_names):
    race = verdict.race
    name_of = _horse_name(verdict, nar_names)

    meta_bits = []
    if race.venue and race.race_no:
        meta_bits.append(f'{race.venue}{race.race_no}R')
    cond = verdict.conditions or {}
    if cond.get('surface') and cond.get('distance'):
        meta_bits.append(f"{cond['surface']}{cond['distance']}m")
    if race.start_time:
        meta_bits.append(f'発走 {race.start_time}')
    meta_line = ' &middot; '.join(_esc(b) for b in meta_bits)

    chips = [f'<span class="chip">勝負度 {_esc(race.confidence or "未評価")}</span>']
    if race.subjective_hit_rate is not None:
        chips.append(f'<span class="chip">主観的中率 {race.subjective_hit_rate:.0%}</span>')
    if cond.get('weather'):
        chips.append(f'<span class="chip">天候 {_esc(cond["weather"])}</span>')
    if cond.get('going'):
        heavy = conditions_module.is_heavy(cond)
        cls = 'chip warn' if heavy else 'chip'
        label = f'馬場 {_esc(cond["going"])}' + ('（要注意）' if heavy else '')
        chips.append(f'<span class="{cls}">{label}</span>')

    marks_html = []
    for m in race.marks:
        nm = name_of(m['umaban'])
        marks_html.append(
            f'<div class="mark"><div class="mark-symbol">{_esc(m["mark"])}'
            f'<span class="um">{m["umaban"]}番</span></div>'
            + (f'<div class="mark-name">{_esc(nm)}</div>' if nm else '') + '</div>'
        )

    bets_html = ''
    gauges_html = ''
    odds_note = ''
    if race.bets:
        rows = []
        for bet, odds_value in zip(race.bets, verdict.bet_odds):
            shown = f'{odds_value:.1f}倍' if odds_value else 'オッズ取得できず'
            rows.append(
                f'<tr><td>{_esc(bet.type)}</td><td>{_esc(bet.combination)}</td>'
                f'<td class="odds">{shown}</td><td class="stake">{bet.stake}円</td></tr>'
            )
        bets_html = (
            '<div class="section-label">買い目</div>'
            '<div class="table-wrap"><table><thead><tr>'
            '<th>券種</th><th>組み合わせ</th><th style="text-align:right">実オッズ</th>'
            '<th style="text-align:right">金額</th></tr></thead><tbody>'
            + ''.join(rows) + '</tbody></table></div>'
        )

        if verdict.composite is not None:
            ev = verdict.expected_value
            gauges_html = '<div class="gauges">' + _gauge(
                '合成オッズ', verdict.composite, discipline.MIN_COMPOSITE_ODDS, '倍')
            if ev is not None:
                gauges_html += _gauge('期待値', ev, discipline.MIN_EXPECTED_VALUE)
            gauges_html += '</div>'
        else:
            status = (verdict.meta or {}).get('status')
            label = odds_module.STATUS_LABELS.get(status, status or '未取得')
            odds_note = f'<div class="odds-note">実オッズ: {_esc(label)}（合成オッズ・期待値は未計算）</div>'
    else:
        bets_html = '<div class="no-bets">買い目なし（勝負度Cまたは印のみ）</div>'

    findings_html = []
    for f in verdict.blocks:
        findings_html.append(
            f'<div class="finding critical"><div class="finding-title">BLOCK &mdash; {_esc(f.message)}</div>'
            + (f'<div class="finding-remedy">{_esc(f.remedy)}</div>' if f.remedy else '') + '</div>'
        )
    for f in verdict.warnings:
        findings_html.append(
            f'<div class="finding warn"><div class="finding-title">WARN &mdash; {_esc(f.message)}</div>'
            + (f'<div class="finding-remedy">{_esc(f.remedy)}</div>' if f.remedy else '') + '</div>'
        )
    if not verdict.blocked and not verdict.warnings:
        if race.bets:
            findings_html.append(
                '<div class="finding good"><div class="finding-title">'
                '第13章の規律をすべてクリアしています</div></div>'
            )
        else:
            # 買い目が無いのに「規律をクリア」は誤読を招く（check.py と同じ理由）。
            # BLOCK・WARNが無いのは買い目自体を検証していないからで、
            # 買い目が規律に合格したわけではない。
            findings_html.append(
                '<div class="finding neutral"><div class="finding-title">'
                '見送り（買い目なし）</div></div>'
            )

    note_html = ''
    if race.note:
        note_html = (f'<div class="note"><div class="note-label">メモ</div>{_esc(race.note)}</div>')

    return f'''
  <div class="race">
    <div class="race-head">
      <div>
        <div class="race-title serif">{_esc(race.name)}</div>
        <div class="race-meta">{meta_line}</div>
      </div>
      {_status_pill(verdict)}
    </div>
    <div class="chips">{''.join(chips)}</div>
    <div class="section-label">印</div>
    <div class="marks">{''.join(marks_html)}</div>
    {bets_html}
    {gauges_html}
    {odds_note}
    <div class="findings">{''.join(findings_html)}</div>
    {note_html}
  </div>'''


def render(sheet, verdicts, now):
    day = sheet.date
    nar_names = _nar_horse_names(day) if any(r.org == 'nar' for r in sheet.races) else {}

    blocked = [v for v in verdicts if v.blocked]
    orderable = [v for v in verdicts if v.race.bets and not v.blocked]

    stat_purchase_cls = 'good' if orderable and not blocked else ('critical' if blocked else '')
    summary = f'''
  <div class="summary">
    <div class="stat"><div class="stat-label">対象レース</div>
      <div class="stat-value">{len(verdicts)}</div>
      <div class="stat-note">{_esc(sheet.source)} 作成</div></div>
    <div class="stat"><div class="stat-label">購入</div>
      <div class="stat-value {stat_purchase_cls}">{len(orderable)}件</div>
      <div class="stat-note">{len(blocked)}件 BLOCK</div></div>
    <div class="stat"><div class="stat-label">検算時刻</div>
      <div class="stat-value" style="font-size:17px;">{now:%H:%M} JST</div>
      <div class="stat-note">買い目作成 {_esc(sheet.generated_at or "-")}</div></div>
  </div>'''

    races_html = ''.join(_race_block(v, nar_names) for v in verdicts)

    return _page(
        eyebrow='直前検算（予想メソッド 第13章）',
        title=f'{day.isoformat()} 検算結果',
        subtitle=f'対象 {len(verdicts)}レース &mdash; 実オッズ反映後の判定',
        body=summary + races_html,
        footer=f'データ元: <code>data/bets/{day.isoformat()}.json</code> / '
               f'<code>data/checks/{day.isoformat()}.json</code>'
               '<div>本検算は買い目設計の規律のみを見ています。印の判断には関与しません。</div>',
    )


def render_missing(day, now):
    body = '''
  <div class="race">
    <div class="race-head">
      <div>
        <div class="race-title serif">朝の買い目が届いていません</div>
        <div class="race-meta">data/bets/''' + _esc(day.isoformat()) + '''.json が見つかりませんでした</div>
      </div>
      <span class="pill pill-critical">要確認</span>
    </div>
    <div class="findings">
      <div class="finding critical">
        <div class="finding-title">朝の予想タスクが完走していないか、コミットされていない可能性があります</div>
        <div class="finding-remedy">2026-08-01は朝07:21に予想が完成していたのに配信が20:30まで止まり、
        ◎2鞍とも1着（払戻1,300円相当）を購入できませんでした。このページはその再発を検知するためのものです。</div>
      </div>
    </div>
  </div>'''
    return _page(
        eyebrow='直前検算（予想メソッド 第13章）',
        title=f'{day.isoformat()} 検算結果',
        subtitle='買い目ファイルが見つかりません',
        body=body,
        footer=f'確認時刻 {now:%H:%M} JST',
    )


def render_no_history(day):
    """過去のある日を見返そうとしたが、記録が無かった場合。"""
    body = f'''
  <div class="race">
    <div class="race-head">
      <div>
        <div class="race-title serif">この日の記録が見つかりません</div>
        <div class="race-meta">data/bets/{_esc(day.isoformat())}.json が存在しません</div>
      </div>
      <span class="pill pill-neutral">記録なし</span>
    </div>
  </div>'''
    return _page(
        eyebrow='過去の検算結果を見返す',
        title=f'{day.isoformat()} の記録',
        subtitle='買い目が作られなかった日か、対象レースが無かった日です',
        body=body,
        footer='',
    )


class _SavedFinding:
    """data/checks/ に保存済みの findings 辞書を、discipline.Finding と同じ形で読ませる。"""

    def __init__(self, raw):
        self.severity = raw.get('severity')
        self.code = raw.get('code')
        self.message = raw.get('message')
        self.remedy = raw.get('remedy', '')


class _SavedVerdict:
    """data/checks/YYYY-MM-DD.json の1レース分を、report_html が読める形に包む。

    本番の RaceVerdict はオッズ取得の直後にその場で作られるが、見返すときは
    その場で再計算できない（実オッズはもう変わっている）。保存済みの数字を
    そのまま表示するだけにして、再取得はしない。
    """

    def __init__(self, race, saved):
        self.race = race
        self.composite = saved.get('composite_odds')
        self._expected_value = saved.get('expected_value')
        self.bet_odds = [b.get('odds') for b in (saved.get('bet_odds') or [])]
        self.conditions = saved.get('conditions') or {}
        self.meta = saved.get('odds_meta') or {}
        self.forms = {}
        self.findings = [_SavedFinding(f) for f in (saved.get('findings') or [])]

    @property
    def blocked(self):
        return any(f.severity == discipline.BLOCK for f in self.findings)

    @property
    def blocks(self):
        return [f for f in self.findings if f.severity == discipline.BLOCK]

    @property
    def warnings(self):
        return [f for f in self.findings if f.severity == discipline.WARN]

    @property
    def expected_value(self):
        return self._expected_value


def render_history(day):
    """過去のある日の買い目・検算結果を、docs/index.html と同じ見た目で描く。

    毎日は残さない設計（decision log:「過去分は data/ の Git履歴で十分」）なので、
    これは見たいときだけ呼ぶ関数。data/checks/ は1日に複数回分たまるため、
    その日の**最後の検算**（＝実オッズが最も締まった時点の判定）を使う。
    """
    sheet = bets.load_sheet(day)
    if sheet is None:
        return render_no_history(day)

    checks_path = os.path.join(bets.CHECKS_DIR, f'{day.isoformat()}.json')
    saved_by_id = {}
    checked_at = None
    if os.path.exists(checks_path):
        with open(checks_path, encoding='utf-8') as f:
            history = json.load(f)
        if history:
            latest = history[-1]
            checked_at = latest.get('checked_at')
            saved_by_id = {r['race_id']: r for r in latest.get('races') or []}

    verdicts = [_SavedVerdict(race, saved_by_id.get(race.race_id, {})) for race in sheet.races]
    now = datetime.fromisoformat(checked_at) if checked_at else datetime.now(bets.JST)
    return render(sheet, verdicts, now)


def _page(eyebrow, title, subtitle, body, footer):
    return f'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(_TITLE)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="page">
  <div class="eyebrow">{_esc(eyebrow)}</div>
  <h1 class="serif">{_esc(title)}</h1>
  <div class="masthead-sub">{_esc(subtitle)}</div>
  {body}
  <footer>{footer}</footer>
</div>
</body>
</html>
'''


def save(html_text, path=None):
    path = path or HTML_PATH
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html_text)
    return path
