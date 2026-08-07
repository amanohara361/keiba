#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""買い目JSONを手で作るためのツール（Claudeが動かないときの代替）。

朝タスクが JSON を出せなかった日でも、これを実行すれば
data/bets/YYYY-MM-DD.json を作れる。あとは git push すれば
土日14:30の直前検算はいつもどおり動く。

買い目確認.bat と同じ発想の保険である。アプリが固まっても、
手元で買い目を確定させる経路を1本残しておく。

  python make_bets.py            対話式で入力する
  python make_bets.py --show     いまある買い目を表示する

外部ライブラリは使わない（Python 3.x だけで動く）。
入力の形式間違いは bets.py が全部はじくので、通れば検算にかけられる。
"""

import argparse
import re
import subprocess
import sys
from datetime import date

import bets
from bets import Bet, BetSheet, BetsError, RaceBets

VENUES = {
    '01': '札幌', '02': '函館', '03': '福島', '04': '新潟', '05': '東京',
    '06': '中山', '07': '中京', '08': '京都', '09': '阪神', '10': '小倉',
}


def ask(prompt, default=None, required=True):
    """1行入力。空Enterなら default を使う。"""
    label = f'{prompt}'
    if default is not None:
        label += f'（空Enterで {default}）'
    while True:
        value = input(f'  {label}: ').strip()
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ''
        print('  !! 入力してください')


def ask_marks():
    """印を1行で受け取る。例: ◎7 ○11 ▲2 △3 △14"""
    print('  印を1行で入力（例: ◎7 ○11 ▲2 △3 △14）')
    while True:
        line = input('  印: ').strip()
        marks = []
        for token in re.findall(r'([◎○▲△])\s*(\d+)', line):
            marks.append({'mark': token[0], 'umaban': int(token[1])})
        if marks:
            return marks
        print('  !! 読み取れません。◎○▲△ と馬番の組で入力してください')


def ask_bets():
    """買い目を1行ずつ受け取る。例: 馬連 7-11 100"""
    print('  買い目を1行ずつ入力（例: 馬連 7-11 100 ／ 空Enterで確定）')
    print('  券種: ' + ' '.join(bets.BET_TYPES))
    entries = []
    while True:
        line = input('  買い目: ').strip()
        if not line:
            return entries
        match = re.match(r'^(\S+)\s+([\d\s\-,]+?)(?:\s+(\d+))?$', line)
        if not match:
            print('  !! 読み取れません。「馬連 7-11 100」のように入力してください')
            continue
        bet_type = match.group(1).replace('３', '3')
        numbers = re.findall(r'\d+', match.group(2))
        stake = int(match.group(3)) if match.group(3) else 100
        try:
            entries.append(Bet(bet_type, numbers, stake))
        except BetsError as exc:
            print(f'  !! {exc}')
            continue
        print(f'    -> {entries[-1]} {stake}円')


def ask_rate():
    """主観的中率。第13章の期待値判定に使うので必須。"""
    print('  主観的中率（期待値1.2の判定に使う。0.16 でも 16% でも可）')
    while True:
        raw = input('  主観的中率: ').strip().rstrip('%％')
        try:
            value = float(raw)
        except ValueError:
            print('  !! 数字で入力してください')
            continue
        if value > 1:
            value /= 100
        if 0 < value <= 1:
            return round(value, 4)
        print('  !! 0より大きく1以下（または100%以下）で入力してください')


def ask_race():
    """1レース分を対話で組み立てる。空Enterで終了を表す None を返す。"""
    race_id = input('\n  race_id を12桁で入力（空Enterで入力終了）: ').strip()
    if not race_id:
        return None
    if not re.fullmatch(r'\d{12}', race_id):
        print('  !! 12桁の数字で入力してください（例 202601020811）')
        return ask_race()

    venue = VENUES.get(race_id[4:6])
    race_no = int(race_id[-2:])
    print(f'  -> {venue or "?"}{race_no}R として扱います')

    name = ask('レース名')
    start_time = ask('発走時刻 HH:MM')
    if not re.fullmatch(r'\d{1,2}:\d{2}', start_time):
        print('  !! HH:MM で入力してください')
        return ask_race()

    confidence = ask('勝負度 A/B/C', default='B').upper()
    marks = ask_marks()

    if confidence == 'C':
        # 第13章：勝負度Cは印を記録するのみで購入しない
        print('  勝負度Cのため買い目は記録しません（第13章）')
        entries, rate = [], None
    else:
        entries = ask_bets()
        rate = ask_rate() if entries else None

    try:
        return RaceBets(
            race_id=race_id, name=name, start_time=start_time,
            marks=marks, bets=entries, confidence=confidence,
            subjective_hit_rate=rate, venue=venue, race_no=race_no,
        )
    except BetsError as exc:
        print(f'  !! {exc}')
        return ask_race()


def show(day):
    sheet = bets.load_sheet(day)
    if sheet is None:
        print(f'{day} の買い目はまだありません（{bets.sheet_path(day)}）')
        return
    print(f'\n{day} / 作成元 {sheet.source} / 作成 {sheet.generated_at or "不明"}')
    for race in sheet.races:
        marks = ' '.join(f"{m['mark']}{m['umaban']}" for m in race.marks)
        print(f'\n  【{race.name}】{race.venue or ""}{race.race_no or ""}R '
              f'{race.start_time}発走 勝負度{race.confidence}')
        print(f'    印: {marks}')
        for bet in race.bets:
            print(f'    {bet.type} {bet.combination}  {bet.stake}円')
        if race.subjective_hit_rate:
            print(f'    主観的中率 {race.subjective_hit_rate:.0%}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='買い目JSONを手で作る')
    parser.add_argument('--date', help='対象日 (YYYY-MM-DD)。既定は今日。')
    parser.add_argument('--show', action='store_true', help='いまある買い目を表示する')
    args = parser.parse_args(argv)

    day = date.fromisoformat(args.date) if args.date else bets.now_jst().date()

    if args.show:
        show(day)
        return 0

    print()
    print('  ============================================')
    print('  買い目JSONの作成')
    print('  ============================================')
    print(f'  対象日: {day}')
    print('  発走時刻の早い順に、レースごとに入力してください。')

    races = []
    while True:
        race = ask_race()
        if race is None:
            break
        races.append(race)
        print(f'  -> {race.name} を追加しました（計{len(races)}レース）')

    if not races:
        print('\n  1レースも入力されませんでした。終了します。')
        return 1

    sheet = BetSheet(
        date=day, races=races,
        generated_at=bets.now_jst().isoformat(),
        source='manual',      # 朝タスクが作った分と区別できるようにする
    )
    path = bets.save_sheet(sheet)

    print()
    print('  ============================================')
    print(f'  保存しました: {path}')
    print('  ============================================')
    show(day)

    print('\n  この内容でよければ、続けて次を実行してください:')
    print(f'    git add {path}')
    print(f'    git commit -m "買い目を追加: {day}"')
    print('    git push')
    print('    git log --oneline -1 origin/main    ← 反映の確認')

    if input('\n  いまコミットして push しますか？ (y/N): ').strip().lower() == 'y':
        return commit_and_push(path, day)
    return 0


def commit_and_push(path, day):
    """git を順に実行する。失敗したらそこで止めて理由を出す。"""
    steps = [
        ['git', 'add', path],
        ['git', 'commit', '-m', f'買い目を追加: {day}'],
        ['git', 'push'],
    ]
    for command in steps:
        print(f'  $ {" ".join(command)}')
        result = subprocess.run(command, capture_output=True, text=True)
        if result.stdout.strip():
            print('   ', result.stdout.strip().replace('\n', '\n    '))
        if result.returncode != 0:
            print('   ', (result.stderr or '').strip().replace('\n', '\n    '))
            print('  !! 失敗しました。上の内容を確認してください。')
            return 1

    check = subprocess.run(['git', 'log', '--oneline', '-1', 'origin/main'],
                           capture_output=True, text=True)
    print(f'  反映を確認: {check.stdout.strip()}')
    print('  完了しました。14:30の直前検算がこの買い目を読みます。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
