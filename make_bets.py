#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""買い目JSONを手で作るためのツール（Claudeが動かないときの代替）。

朝タスクが JSON を出せなかった日でも、これを実行すれば
data/bets/YYYY-MM-DD.json を作れる。あとは git push すれば
土日14:30の直前検算はいつもどおり動く。

買い目確認.bat と同じ発想の保険である。アプリが固まっても、
手元で買い目を確定させる経路を1本残しておく。

  python make_bets.py --paste          まとめて貼り付ける（おすすめ・手数が最少）
  python make_bets.py --file 買い目.txt  テキストファイルから読む
  python make_bets.py                  1問ずつ対話で入力する
  python make_bets.py --show           いまある買い目を表示する

■ 貼り付け・ファイルの書き方
  1レースにつき3ブロック。レースとレースの間は空行で区切る。

    202601020811 クイーンステークス 15:25 B 35%
    ◎7 ○11 ▲2 △3 △14
    馬連 7-11 100
    ワイド 7-14 100

    202604020207 アイビスサマーダッシュ 15:45 A 29%
    ◎16 ○6
    単勝 16 200

  1行目は race_id・レース名・発走時刻・勝負度・主観的中率。
  レース名以外は書き方で見分けるので**並び順は自由**。金額を省くと100円。

外部ライブラリは使わない（Python 3.x だけで動く）。
入力の形式間違いは bets.py が全部はじくので、通れば検算にかけられる。
"""

import argparse
import re
import subprocess
import sys
from datetime import date

import bets
import nar
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


# ----------------------------------------------------------------------
# まとめて貼り付ける経路
# ----------------------------------------------------------------------

def parse_bet_line(line):
    """「馬連 7-11 100」を Bet にする。読めなければ None。"""
    match = re.match(r'^(\S+)\s+([\d\s\-,]+?)(?:\s+(\d+)\s*円?)?$', line)
    if not match:
        return None
    bet_type = match.group(1).replace('３', '3')
    if bet_type not in bets.BET_TYPES:
        return None
    numbers = re.findall(r'\d+', match.group(2))
    stake = int(match.group(3)) if match.group(3) else 100
    return Bet(bet_type, numbers, stake)


def parse_header(line):
    """レースの1行目を読む。

    race_id・発走時刻・勝負度・主観的中率は書き方で見分けるので、
    並び順は自由。残った文字列をレース名とする。
    """
    rest = line

    # 主催者は書いてあればそれに従う。書いていなければ後で推測するが、
    # 推測は保険であって根拠ではない（地方を混ぜるなら書いたほうが確実）。
    org = None
    found_org = re.search(r'\borg\s*[:：]\s*(jra|nar)\b', rest, re.I)
    if found_org:
        org = found_org.group(1).lower()
        rest = rest.replace(found_org.group(0), ' ', 1)

    race_id = re.search(r'\b(\d{12})\b', rest)
    if not race_id:
        raise BetsError(f'12桁の race_id が見つかりません: {line}')
    rest = rest.replace(race_id.group(1), ' ', 1)

    start = re.search(r'\b(\d{1,2}:\d{2})\b', rest)
    if not start:
        raise BetsError(f'発走時刻（HH:MM）が見つかりません: {line}')
    rest = rest.replace(start.group(1), ' ', 1)

    rate = re.search(r'(\d+(?:\.\d+)?)\s*[%％]|\b(0\.\d+)\b', rest)
    hit_rate = None
    if rate:
        hit_rate = float(rate.group(1)) / 100 if rate.group(1) else float(rate.group(2))
        rest = rest.replace(rate.group(0), ' ', 1)

    confidence = 'B'
    grade = re.search(r'(?:^|\s)([ABC])(?:\s|$)', rest)
    if grade:
        confidence = grade.group(1)
        rest = rest[:grade.start(1)] + ' ' + rest[grade.end(1):]

    name = re.sub(r'\s+', ' ', rest).strip()
    hour, minute = (int(x) for x in start.group(1).split(':'))
    return {
        'race_id': race_id.group(1),
        'org': org,
        'name': name or '（レース名なし）',
        'start_time': f'{hour:02d}:{minute:02d}',
        'confidence': confidence,
        'subjective_hit_rate': round(hit_rate, 4) if hit_rate else None,
    }


def parse_block(block):
    """空行で区切られた1レース分のかたまりを RaceBets にする。"""
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    header = parse_header(lines[0])

    marks, entries = [], []
    for line in lines[1:]:
        found = re.findall(r'([◎○▲△])\s*(\d+)', line)
        if found:
            marks.extend({'mark': m, 'umaban': int(n)} for m, n in found)
            continue
        bet = parse_bet_line(line)
        if bet is None:
            raise BetsError(f'読み取れない行があります: {line}')
        entries.append(bet)

    # **場は org を決めてから引く。** 中央も地方も race_id は12桁の数字で、
    # 桁を見ても区別が付かない。地方の 202608124711（笠松11R）を中央として
    # 読むと [4:6] が '08' なので**京都と表示される**。黙って別の場になる。
    rid = header['race_id']
    org = header.pop('org', None) or _guess_org(rid)
    if org == 'nar':
        _, venue, race_no = nar.split_race_id(rid)
    else:
        venue, race_no = VENUES.get(rid[4:6]), int(rid[-2:])

    return RaceBets(
        marks=marks, bets=entries, venue=venue, race_no=race_no,
        org=org, **header,
    )


def _guess_org(race_id):
    """race_id から主催者を判定する。貼り付け経路の保険。

    9〜10桁目を見れば足りる。中央はそこが**開催何日目**で 01〜12 にしかならず、
    地方はそこが**場コード**で 30 以上しかない（`nar.VENUE_CODES`）。
    範囲が重ならないので取り違えようがない。

    - 中央 `202601020811` → 9〜10桁目 '08'（8日目）→ jra
    - 地方 `202608124711` → 9〜10桁目 '47'（笠松）  → nar

    それでも1行目に `org:nar` と書けるようにしてあるのは、
    **将来どちらかの採番が変わったときに、推測より書いたものを優先させるため**。
    """
    if len(race_id) == 12 and race_id[8:10] in nar.CODE_VENUES:
        return 'nar'
    return 'jra'


def parse_text(text):
    """全体を読む。空行区切りで1レース。"""
    races = []
    for index, block in enumerate(re.split(r'\n\s*\n', text.strip()), 1):
        if not block.strip():
            continue
        try:
            races.append(parse_block(block))
        except BetsError as exc:
            raise BetsError(f'{index}件目のレース: {exc}')
    if not races:
        raise BetsError('レースが1件も読み取れませんでした')
    return races


def read_pasted():
    """貼り付けを受け取る。ピリオドだけの行、または Ctrl+Z / Ctrl+D で終了。"""
    print('  買い目をまとめて貼り付けてください。')
    print('  貼り終わったら、ピリオド1文字だけの行を入れて Enter（または Ctrl+Z → Enter）')
    print('  ------------------------------------------------------------')
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == '.':
            break
        lines.append(line)
    return '\n'.join(lines)


def show(day):
    """買い目を表示し、契約を満たしていなければ問題の件数を返す。

    **戻り値は「問題のあるレース数」。** 朝タスクはこのコマンドを自己検証に
    使うので、黙って 0 を返すと欠落が素通りする。2026-08-26 に地方の朝タスクが
    win_probabilities を書かずに push し、直前検算で全レース見送りになったが、
    このコマンドは何も言わなかった。表示するだけの関数を検証に使っていたのが
    原因なので、ここで落とす。
    """
    sheet = bets.load_sheet(day)
    if sheet is None:
        print(f'{day} の買い目はまだありません（{bets.sheet_path(day)}）')
        return 0
    print(f'\n{day} / 作成元 {sheet.source} / 作成 {sheet.generated_at or "不明"}')
    problems = 0
    for race in sheet.races:
        marks = ' '.join(f"{m['mark']}{m['umaban']}" for m in race.marks)
        print(f'\n  【{race.name}】{race.venue or ""}{race.race_no or ""}R '
              f'{race.start_time}発走 勝負度{race.confidence}')
        print(f'    印: {marks}')
        for bet in race.bets:
            print(f'    {bet.type} {bet.combination}  {bet.stake}円')

        if race.win_probabilities:
            shown = ' '.join(f'{n}:{p:.3f}'
                             for n, p in sorted(race.win_probabilities.items()))
            total = sum(race.win_probabilities.values())
            print(f'    主観勝率 {shown}（記入分の合計 {total:.3f}）')
        elif race.marks:
            problems += 1
            print('    ⚠ win_probabilities がありません。')
            print('      **このレースは直前検算で必ず見送り（勝負度C）になります。**')
            print('      市場勝率だけでは期待値が控除率を超えないためです。')
            print('      書き方: docs/朝タスク手順.md「主観勝率の書き方」')

        if race.subjective_hit_rate:
            print(f'    （非推奨）subjective_hit_rate {race.subjective_hit_rate:.0%}'
                  ' … 2026-08-26に廃止。直前検算は読みません')

    if problems:
        print(f'\n  ⚠ {problems}件のレースに win_probabilities がありません。')
        print('  このまま push すると、その分の買い目は出ません。')
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description='買い目JSONを手で作る')
    parser.add_argument('--date', help='対象日 (YYYY-MM-DD)。既定は今日。')
    parser.add_argument('--paste', action='store_true', help='まとめて貼り付ける')
    parser.add_argument('--file', help='テキストファイルから読む')
    parser.add_argument('--show', action='store_true', help='いまある買い目を表示する')
    args = parser.parse_args(argv)

    day = date.fromisoformat(args.date) if args.date else bets.now_jst().date()

    if args.show:
        # 問題があれば非ゼロで終わる。朝タスクの自己検証がここで止まる。
        return 1 if show(day) else 0

    print()
    print('  ============================================')
    print('  買い目JSONの作成')
    print('  ============================================')
    print(f'  対象日: {day}')

    if args.file or args.paste:
        if args.file:
            with open(args.file, encoding='utf-8') as f:
                text = f.read()
        else:
            text = read_pasted()
        try:
            races = parse_text(text)
        except BetsError as exc:
            print(f'\n  !! {exc}')
            print('  書き方は make_bets.py の冒頭を見てください。')
            return 1
        print(f'\n  {len(races)}レースを読み取りました。')
    else:
        print('  発走時刻の早い順に、レースごとに入力してください。')
        print('  （--paste を使うとまとめて貼り付けられます）')
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
