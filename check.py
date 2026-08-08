#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直前検算タスク（GitHub Actions から定刻に実行される）。

やること:
  1. その日の買い目ファイル（data/bets/YYYY-MM-DD.json）を読む
  2. 発走前のレースについて実オッズを取り直す
  3. 予想メソッド 第13章の規律を機械的にあてる
  4. 結果をメールで送り、data/checks/ に記録する

**朝の買い目が届いていなければ、それ自体を警報としてメールする。**
2026-08-01は朝07:21に予想が完成していたのに配信が20:30まで止まり、
13時間だれも気づかなかった。あの日に鳴るべきだったのがこの警報である。

印は打たない。判断には関与しない。算術と規則だけを担当する。
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime

import bets
import conditions as conditions_module
import discipline
import form as form_module
import odds as odds_module
from mailer import Mailer

# 終了コードは「このジョブが役目を果たせたか」で決める。
#
# 「朝の買い目が無い」「発注を止めた」は、検知して知らせた時点で役目は果たしている。
# これを失敗扱いにすると買い目を出さない日は毎回赤くなり、本当の障害
# （SMTPが落ちた、netkeibaの構造が変わった）が同じ色に埋もれて気づけなくなる。
EXIT_OK = 0                # 検算完了・規律クリア
EXIT_NEEDS_ATTENTION = 3   # 要確認の事象を検知し、正常に通知した（ジョブは成功）
EXIT_ERROR = 1             # 知らせられなかった（送信失敗・設定不備）

# mailer など各モジュールのログを実行ログへ出す。
# これが無いとメール送信の成否が一切表示されず、SMTPが落ちても気づけない。
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logger = logging.getLogger(__name__)


def deliver(mailer, subject, body):
    """メールを送り、結果を必ず実行ログに残す。"""
    if not mailer.is_configured():
        logger.error('メールの認証情報が未設定です。GitHub Secrets を確認してください。')
        return False
    if not mailer.send(subject, body):
        logger.error('メール送信に失敗しました（件名: %s）', subject)
        return False
    logger.info('メールを送信しました（件名: %s）', subject)
    return True


def resolve_now(override=None):
    """現在時刻を決める。

    CLAUDE.md の「現在時刻の確定」に対応する。GitHub Actions のランナーは
    毎回新しいコンテナで起動するため、Coworkで起きたような
    「システム時計が起動時刻で凍結する」事象は構造的に起こらない。
    それでも手動確認できるよう、上書きの口を用意しておく。
    """
    if override:
        return datetime.fromisoformat(override).replace(tzinfo=bets.JST)
    return datetime.now(bets.JST)


def review_sheet(sheet, now, fetcher=None, conditions_fetcher=None,
                 forms_fetcher=None):
    """買い目ファイル全体を検算して、レースごとの判定を返す。"""
    fetcher = fetcher or odds_module.fetch
    conditions_fetcher = conditions_fetcher or conditions_module.fetch
    forms_fetcher = forms_fetcher or form_module.collect
    verdicts = []
    for race in sheet.races:
        # 発走済みなら実オッズを取りに行かない（netkeibaは朝の値しか返さない）
        start = race.start_datetime(sheet.date)
        if start and now >= start:
            verdicts.append(discipline.review_race(
                race, [], {}, {'skipped': '発走済みのため取得せず'}, now, sheet.date))
            continue

        # 馬場状態は朝は未発表のことが多い。取れなくても検算は続ける。
        try:
            track = conditions_fetcher(race.race_id)
        except conditions_module.ConditionsError as exc:
            logger.warning('%s の馬場状態を取得できませんでした: %s', race.name, exc)
            track = {}

        # 馬場が渋ったときだけ各馬の実績を引く。良馬場なら不要なアクセスをしない。
        forms = {}
        if conditions_module.is_heavy(track):
            marked = sorted(race.marked_horses)
            try:
                forms = forms_fetcher(race.race_id, marked)
            except form_module.FormError as exc:
                logger.warning('%s の各馬の戦績を取得できませんでした: %s', race.name, exc)

        bet_odds, win_table, meta = odds_module.collect(race, fetcher=fetcher)
        verdicts.append(discipline.review_race(
            race, bet_odds, win_table, meta, now, sheet.date, track, forms))
    return verdicts


# ----------------------------------------------------------------------
# 本文の組み立て
# ----------------------------------------------------------------------

DIVIDER = '-' * 46


def format_missing_sheet(day, now):
    return '\n'.join([
        '⚠ 朝の買い目が届いていません',
        DIVIDER,
        f'対象日: {day.isoformat()} / 確認時刻: {now:%H:%M} JST',
        '',
        f'data/bets/{day.isoformat()}.json が見つかりませんでした。',
        '朝の予想タスクが完走していないか、コミットされていない可能性があります。',
        '',
        '2026-08-01は朝07:21に予想が完成していたのに配信が20:30まで止まり、',
        '◎2鞍とも1着（払戻1,300円相当）を購入できませんでした。',
        'このメールはその再発を検知するためのものです。手元の状況を確認してください。',
        '',
    ])


def format_verdict(verdict):
    race = verdict.race
    lines = []

    header = f'【{race.name}】'
    if race.venue and race.race_no:
        header += f' {race.venue}{race.race_no}R'
    if race.start_time:
        header += f' {race.start_time}発走'
    header += f' 勝負度{race.confidence}'
    lines.append(header)

    track = conditions_module.label(verdict.conditions)
    if track:
        lines.append(f'  {track}')

    marks = ' '.join(f"{m['mark']}{m['umaban']}" for m in race.marks)
    if marks:
        lines.append(f'  印: {marks}')

    going = (verdict.conditions or {}).get('going')
    for entry in race.marks:
        detail = verdict.forms.get(entry['umaban'])
        if not detail:
            continue
        bits = []
        if detail.get('weight'):
            diff = detail.get('weight_diff')
            bits.append(f"{detail['weight']}kg"
                        + (f'({diff:+d})' if diff is not None else ''))
        bits.append(form_module.summarise(detail.get('record') or {}, going))
        lines.append(f"    {entry['mark']}{entry['umaban']} "
                     f"{detail.get('name', '')} " + ' / '.join(bits))

    if race.bets:
        lines.append('  買い目:')
        for bet, value in zip(race.bets, verdict.bet_odds):
            shown = f'{value:.1f}倍' if value else 'オッズ取得できず'
            lines.append(f'    {bet.type} {bet.combination}  {shown}  {bet.stake}円')

    if verdict.composite:
        summary = f'  合成オッズ {verdict.composite:.2f}倍'
        if verdict.expected_value:
            summary += f' / 期待値 {verdict.expected_value:.2f}'
        lines.append(summary)

    if verdict.blocked:
        lines.append('')
        lines.append('  ■ 発注を止めました')
        for finding in verdict.blocks:
            lines.append(f'    × {finding.message}')
            if finding.remedy:
                lines.append(f'      → {finding.remedy}')

    for finding in verdict.warnings:
        lines.append(f'  △ {finding.message}')
        if finding.remedy:
            lines.append(f'      → {finding.remedy}')

    if not verdict.blocked and not verdict.warnings:
        lines.append('  ✓ 第13章の規律をすべてクリアしています')

    meta = verdict.meta or {}
    status = meta.get('status')
    if status:
        label = odds_module.STATUS_LABELS.get(status, status)
        lines.append(f'  （オッズ: {label} {meta.get("official_datetime") or ""}）')
    for error in meta.get('errors', []):
        lines.append(f'  ！ {error}')

    lines.append(DIVIDER)
    return lines


def format_report(sheet, verdicts, now):
    blocked = [v for v in verdicts if v.blocked]
    warned = [v for v in verdicts if not v.blocked and v.warnings]

    lines = ['🏇 直前検算（予想メソッド 第13章）', DIVIDER]
    if blocked:
        lines.append(f'■ 発注を止めたレース: {len(blocked)}件')
        for verdict in blocked:
            lines.append(f'   ・{verdict.race.name}')
        lines.append('')
    elif warned:
        lines.append(f'△ 要検討: {len(warned)}件')
        lines.append('')
    else:
        lines.append('✓ 全レース、規律をクリアしています')
        lines.append('')

    lines.append(f'対象日 {sheet.date.isoformat()} / 検算 {now:%H:%M} JST'
                 f' / 買い目の作成元 {sheet.source}')
    if sheet.generated_at:
        lines.append(f'買い目の作成時刻 {sheet.generated_at}')
    lines.append(DIVIDER)

    for verdict in verdicts:
        lines.extend(format_verdict(verdict))

    lines.append('')
    lines.append('※ 本検算は買い目設計の規律のみを見ています。印の判断には関与しません。')
    lines.append('※ 基準: 合成オッズ3.0倍以上 / 期待値1.2以上 / 単勝4.0〜9.9倍。')
    return '\n'.join(lines)


def write_job_summary(body):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not path:
        return
    with open(path, 'a', encoding='utf-8') as f:
        f.write('```\n' + body + '\n```\n')


# ----------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description='買い目に第13章の規律をあてる')
    parser.add_argument('--date', help='対象日 (YYYY-MM-DD)。既定は今日。')
    parser.add_argument('--now', help='現在時刻 (YYYY-MM-DDTHH:MM)。動作確認用。')
    parser.add_argument('--no-email', action='store_true', help='メールを送らない')
    parser.add_argument('--no-save', action='store_true', help='検算結果を保存しない')
    args = parser.parse_args(argv)

    now = resolve_now(args.now)
    day = date.fromisoformat(args.date) if args.date else now.date()

    mailer = Mailer()
    sheet = bets.load_sheet(day)

    if sheet is None:
        body = format_missing_sheet(day, now)
        print(body)
        write_job_summary(body)
        if args.no_email:
            return EXIT_NEEDS_ATTENTION
        # 知らせられたかどうかで結果を分ける。
        # 知らせられたなら役目は果たしている（＝ジョブとしては成功）。
        return (EXIT_NEEDS_ATTENTION
                if deliver(mailer, '【要確認】朝の買い目が届いていません', body)
                else EXIT_ERROR)

    verdicts = review_sheet(sheet, now)
    body = format_report(sheet, verdicts, now)
    print(body)
    write_job_summary(body)

    if not args.no_save:
        path = bets.save_check_result(day, {
            'checked_at': now.isoformat(),
            'races': [v.to_dict() for v in verdicts],
        })
        print(f'\n検算結果を保存しました: {path}')

    if not args.no_email:
        blocked = sum(1 for v in verdicts if v.blocked)
        subject = ('【要確認】直前検算で発注を止めた買い目があります'
                   if blocked else '【自動配信】直前検算 完了')
        if not deliver(mailer, subject, body):
            return EXIT_ERROR

    return EXIT_NEEDS_ATTENTION if any(v.blocked for v in verdicts) else EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
