#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""地方競馬の当日カードを作る。data/cards/nar/YYYY-MM-DD.json。

中央の card.py と役割は同じ（取得側・判断しない）だが、中身はかなり違う。

  card.py（中央）                       nar_card.py（地方）
  netkeiba の HTML を1頭ずつ叩く         公式CSVを2〜3回ダウンロードして終わり
  1頭1.5秒のウェイトが要る               ウェイト不要（サーバに1回しか触らない）
  クラスをアイコン番号から推測する         `競走種類名称` に書いてある
  土日だけ                              **毎日ある。重賞も平日に出る**

平日に地方の重賞が組まれるので、土日固定の仕組みでは拾えない。
これがこのファイルを作った理由（2026-08-12）。

近走は月次ファイルから復元する。地方の出馬表CSVには「全成績 3-1-0-9」のような
通算成績しか載らないが、月次の出馬表CSVは確定後に着順・タイム・上がり3F・人気が
埋まるので、**同じ馬を過去の日付から引けば近走が組み上がる**。
中央のように1頭ずつページを叩く必要がない。

外部ライブラリは使わない。
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

import form as form_module
import nar

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))
CARDS_DIR = os.path.join(ROOT, 'data', 'cards', 'nar')

# 近走を何走まで載せるか。第2章の減点方式が見るのは主に前走と前々走で、
# 5走あれば「6か月以上の休み明け」の判定にも足りる。
RECENT_RUNS = 5

# 近走を復元するために遡る月数。
# 月初は前月分が要る（月をまたいだ直後に前走が引けないと、第2章のチェックが
# 丸ごと効かなくなる）。加えて第2章は「6か月以上の休み明け」を減点条件に
# 持つので、そこに届く長さが要る。6か月あれば「この範囲に1走も無い」が
# そのまま休み明けの根拠になる。月次ファイル1本あたり数秒なので、
# 長さより「判定できないまま通す」ほうが高くつく。
LOOKBACK_MONTHS = 6

# JpnI/II/IIIなどの交流重賞に出るJRA所属馬の近走をnetkeibaへ問い合わせる間隔。
# form.pyのREQUEST_INTERVALと同じ考え方（サーバへの配慮）。
JRA_REQUEST_INTERVAL = 1.5

# JRA所属馬は何走まで載せるか。**地方馬の RECENT_RUNS より深く取る。**
# 地方馬は月次CSVがあるので5走を超えて知りたければ後から遡れるが、JRA所属馬に
# ついてはこのフィールドが唯一の窓で、切り捨てた分は二度と見えない。
#
# 2026-08-27の門別11R・ペンダントで実害が出た。通算7戦のうち5走だけを保存した
# 結果、芝で敗れた2走のうち1走（2025/08/24 新馬 芝1600m 4着）がカードから
# 落ちた。この馬は「ダート5戦3-2-0-0・芝2戦0-0-0-2」で、**着外がどちらも芝**
# という点が本日のダート戦の評価を決める材料だったのに、カードだけを見ると
# 芝の敗戦が1走しか無く、その根拠を確かめられない状態になっていた。
# 通算20戦程度までなら1頭1ページで収まるので、深さで損はしない。
JRA_RECENT_RUNS = 20

# 終了コードの意味は check.py と揃える。
# **赤は「知らせられなかった」だけに絞る。** 買い目が無い日・重賞が無い日が
# 毎回赤くなると、本当の障害（SMTP断・公式サイトの構造変更）が同じ色に埋もれる。
EXIT_OK = 0
EXIT_ERROR = 1        # 知らせられなかった
EXIT_NOTIFIED = 3     # 要確認だが通知はできた


class CardError(RuntimeError):
    pass


# ----------------------------------------------------------------------
# 近走の復元
# ----------------------------------------------------------------------

def _horse_key(row):
    """同名馬を取り違えないよう、馬名だけでなく生年月日も鍵に入れる。"""
    return ((row.get('馬名') or '').strip(), (row.get('生年月日') or '').strip())


def corner_positions(race_row, umaban):
    """レース一覧の「コーナー通過順」から、その馬の各コーナーの位置を拾う。

    公式CSVはレース単位で全馬分の通過順を持っている（第14章）。
    馬番の並び順の中で何番目にいるかが、そのまま通過順位。

    記法の区切り（',' '-' '=' '( )'）は順序の解釈に影響しないので数字だけ拾う
    （nar_csv.py の parse_corner と同じ扱い）。
    """
    out = []
    for i in range(1, 9):
        raw = race_row.get(f'コーナー通過順{i}') or ''
        order = [int(x) for x in re.findall(r'\d+', raw)]
        if not order:
            continue
        out.append(order.index(umaban) + 1 if umaban in order else None)
    return out


def running_style(positions, field_size):
    """通過順から脚質を1語で。第2章「前走逃げて好走」の判定に使う。

    地方ダートは構造的に前有利（第5章・第11章で指数2.07が平常値）なので、
    前走の位置取りは中央よりも重い情報になる。
    """
    known = [p for p in positions if p]
    if not known or not field_size:
        return None
    first = known[0]
    if first == 1:
        return '逃げ'
    if first <= max(2, round(field_size * 0.3)):
        return '先行'
    if first <= round(field_size * 0.7):
        return '差し'
    return '追い込み'


def build_history(racelist_rows, horselist_rows):
    """過去の出馬表を馬ごとにまとめる。戻り値は {馬の鍵: [走った記録, ...]}。

    着順が空の行は未確定（＝これから走るレース）なので入れない。
    """
    races = {}
    for row in racelist_rows:
        key = ((row.get('競馬場') or '').strip(),
               (row.get('競走年月日') or '').strip(),
               (row.get('レース番号') or '').strip())
        races[key] = row

    history = {}
    for row in horselist_rows:
        finish = (row.get('着順') or '').strip()
        if not finish.isdigit():
            continue
        key = ((row.get('競馬場') or '').strip(),
               (row.get('競走年月日') or '').strip(),
               (row.get('レース番号') or '').strip())
        race_row = races.get(key)
        if race_row is None:
            continue
        umaban = nar._int(row.get('馬番'))
        field_size = nar._int(race_row.get('頭数'))
        positions = corner_positions(race_row, umaban) if umaban else []
        history.setdefault(_horse_key(row), []).append({
            'date': key[1],
            'venue': key[0],
            'race_no': nar._int(key[2]),
            'race_name': (race_row.get('レース名') or '').strip(),
            'grade': (race_row.get('競走種類名称') or '').strip(),
            'distance': nar._int(race_row.get('距離')),
            'surface': (race_row.get('芝ダート区分') or '').strip(),
            'going': (race_row.get('馬場') or '').strip(),
            'field_size': field_size,
            'umaban': umaban,
            'finish': int(finish),
            'popularity': nar._int(row.get('人気')),
            'time': (row.get('タイム') or '').strip() or None,
            'margin': (row.get('着差') or '').strip() or None,
            'last_3f': (row.get('上がり3F') or '').strip() or None,
            'jockey': (row.get('騎手名') or '').strip(),
            'horse_weight': nar._int(row.get('馬体重')),
            'corners': positions,
            'style': running_style(positions, field_size),
        })

    for runs in history.values():
        runs.sort(key=lambda r: (r['date'], r['race_no'] or 0), reverse=True)
    return history


def months_back(day, count):
    """day を含む直近 count か月分の（年,月）を新しい順に返す。"""
    out = []
    year, month = day.year, day.month
    for _ in range(count):
        out.append(date(year, month, 1))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return out


# ----------------------------------------------------------------------
# 出走馬に近走を貼る
# ----------------------------------------------------------------------

def annotate(entries, history, day, distance=None):
    """出走各馬に近走と、そこから機械的に出る事実を足す。

    **判断はしない。** 足すのは第2章の減点方式チェックリストが要求する事実
    （前走着順・距離の延長短縮・乗り替わり・休み明けの期間・脚質）だけで、
    点数は付けない。点を付けるのは印を打つ側の仕事。
    """
    for entry in entries:
        key = (entry['name'], entry.get('birth') or '')
        runs = history.get(key)
        if runs is None:
            # 生年月日が引けない場合の保険。馬名だけで一致を探す。
            runs = next((v for k, v in history.items() if k[0] == entry['name']), [])
        runs = [r for r in runs if r['date'] < day.strftime('%Y%m%d')][:RECENT_RUNS]
        entry['recent_runs'] = runs

        if not runs:
            # **空の last_run を null で済ませない。**
            # 「調べたが1走も無かった」と「調べていない」を同じ形にすると、
            # 第2章の「6か月以上の休み明け」が静かに効かなくなる。
            #
            # そして中央馬（交流重賞の遠征馬）は、NAR のCSVに近走が
            # 1走も無いのが**正常**である。休み明けと同じ形で出すと真逆に読む。
            entry['last_run'] = {
                'note': ('中央（JRA）所属。地方のデータに近走は入らない。'
                         '休み明けではない。近走は別途あたること'
                         if entry.get('belongs_jra') else
                         f'直近{LOOKBACK_MONTHS}か月の地方に出走記録なし'
                         '（休み明け・中央からの移籍初戦・デビュー戦のいずれか）'),
            }
            continue

        last = runs[0]
        try:
            gap = (day - datetime.strptime(last['date'], '%Y%m%d').date()).days
        except ValueError:
            gap = None
        entry['last_run'] = {
            'finish': last['finish'],
            'style': last['style'],
            'distance': last['distance'],
            'venue': last['venue'],
            'jockey': last['jockey'],
            'days_since': gap,
            # 距離の延長・短縮は第2章の減点条件。事実なので機械が出す。
            'distance_change': (None if not last['distance'] or not distance
                                else distance - last['distance']),
            'jockey_change': (bool(entry.get('jockey'))
                              and entry['jockey'] != last['jockey']),
        }
    return entries


def _annotate_jra_horse(entry, fetcher, sleep, day):
    """交流重賞に出るJRA所属馬の近走をnetkeibaから補う。

    annotate() は地方のCSVに近走が無いJRA所属馬に定型文（「近走は別途あたること」）
    を入れる。ここは「別途あたる」側の実装で、馬名からnetkeibaを検索し、
    見つかれば jra_recent_runs に競走成績を入れて note を上書きする。

    **馬名検索は同姓同名で複数該当することがある。1頭に絞れないときは
    fetcher が None を返す設計にしてあり、その場合は定型文のまま何もしない
    （推測で当てない。第12章「不明点は推測せず要確認と明記する」）。**

    ただし出馬表CSVは父名と馬齢を持っているので、それを手がかりとして渡す。
    突き合わせできる材料を使うのは推測ではない。2026-08-27の門別11R・
    ペンダント（オルフェーヴル産駒・2023年生）は、netkeibaに1997年生の
    同名馬がいるため名前だけでは割れて取りこぼしていた。
    """
    sleep(JRA_REQUEST_INTERVAL)
    birth_year = None
    if entry.get('age'):
        # 馬齢は「年 − 生年」（2001年からの満年齢表記）。
        birth_year = day.year - entry['age']
    try:
        runs = fetcher(entry['name'], sire=entry.get('sire'),
                       birth_year=birth_year, limit=JRA_RECENT_RUNS)
    except form_module.FormError as exc:
        logger.warning('%s（JRA所属）の近走を取得できませんでした: %s', entry['name'], exc)
        return
    if runs is None:
        logger.info('%s（JRA所属）はnetkeibaで馬名を1頭に絞れませんでした', entry['name'])
        return
    entry['jra_recent_runs'] = runs
    note = ('中央（JRA）所属。netkeibaの競走成績を jra_recent_runs に入れた'
            if runs else
            '中央（JRA）所属。netkeibaで馬は特定できたが競走成績が0件（新馬の可能性）')

    # **地方の近走がある馬の last_run を、注記だけの辞書で上書きしない。**
    # JRA所属でも交流重賞に遠征していれば地方のCSVに近走が残る。annotate() が
    # そこから組み立てた last_run（着順・脚質・距離・場・間隔・距離増減・
    # 乗り替わり）は第2章の減点条件をあてるための材料で、注記に差し替えると
    # 消えてしまう。2026-08-27の門別11R・ペンダントがこの形で、関東オークス
    # JpnII を勝った事実が last_run から落ちる寸前だった（このときは netkeiba
    # 側の検索が失敗して上書き自体が起きず、結果的に露見しなかった）。
    # 注記は既存の last_run に足すだけにする。
    if entry.get('recent_runs'):
        entry['last_run'] = dict(entry.get('last_run') or {}, note=note)
    else:
        entry['last_run'] = {'note': note}


# ----------------------------------------------------------------------
# カード作成
# ----------------------------------------------------------------------

def build_card(day, levels=('重賞', '準重賞'), with_entries=True, fetcher=None, today=None,
               jra_fetcher=None, jra_sleep=None):
    """その日のカードを組む。

    fetcher はテスト用の差し替え口。`fetcher(type_, month)` が
    {'racelist':..., 'horselist':...} を返せばよい。
    today もテスト用の差し替え口（既定は実際の現在日）。**ここを実時刻に
    直結させたままだと、day と実際の「今日」がたまたま一致する/しないかで
    テストの通る道筋が日付をまたぐたびに変わり、テストの結果が壊れる**
    （2026-08-12に書いたテストが翌13日に失敗した）。
    jra_fetcher も同様にテスト用の差し替え口（既定は netkeiba への実アクセス）。
    """
    fetch = fetcher or (lambda type_, month=None: nar.race_data(type_, month))
    jra_fetcher = jra_fetcher or form_module.fetch_jra_recent_runs
    jra_sleep = jra_sleep or time.sleep

    # 当日ファイルにはその日のレースだけが入っており、いちばん新しい。
    # 取れなければ月次で代替する（月次は当日＋2日先まで入っている）。
    today = today if today is not None else nar.today_jst()
    daily = None
    if day == today:
        try:
            daily = fetch(nar.DAILY)
        except nar.NarError as exc:
            logger.warning('当日ファイルを取れませんでした（月次で代替します）: %s', exc)

    months = [fetch(nar.MONTHLY, m) for m in months_back(day, LOOKBACK_MONTHS)]

    racelist = (daily or {}).get('racelist') or []
    horselist = (daily or {}).get('horselist') or []
    if not racelist:
        racelist = [r for m in months for r in m['racelist']]
        horselist = [r for m in months for r in m['horselist']]

    races = nar.parse_races(racelist, day)
    if not races:
        raise CardError(f'{day} のレースが公式データに見つかりません（開催なし、または未掲載）')

    targets = nar.graded(races, levels)
    for race in targets:
        race['candidate'] = True

    if with_entries and targets:
        history = build_history(
            [r for m in months for r in m['racelist']],
            [r for m in months for r in m['horselist']],
        )
        for race in targets:
            entries = nar.parse_entries(horselist, race['race_id'])
            annotate(entries, history, day, race['distance'])
            for entry in entries:
                # 生年月日は近走の突き合わせにしか使わないので、カードには残さない。
                entry.pop('birth', None)
                # JRA所属馬は地方のCSVに近走が無いのが仕様。netkeibaを馬名で
                # 検索できれば近走を補う（見つからなければ定型文のまま）。
                if entry.get('belongs_jra'):
                    _annotate_jra_horse(entry, jra_fetcher, jra_sleep, day)
            race['entries'] = entries
            # 交流重賞かどうか。中央馬が1頭でもいれば、そのレースの近走は
            # 地方のデータだけでは揃わない。判断側にそれを先に知らせる。
            race['jra_horses'] = [e['umaban'] for e in entries if e.get('belongs_jra')]

    return {
        'date': day.isoformat(),
        'org': 'nar',
        'generated_at': nar.datetime.now(nar.JST).isoformat(timespec='seconds'),
        'source': 'keiba.go.jp 公式データダウンロード',
        'levels': list(levels),
        'race_count': len(races),
        # 該当なしでも races: [] のカードを書く。**ファイルが無い状態は
        # 「取得できなかった」を意味させたい**（決定ログ「取得できなかったことを
        # 静かに0件と出さない」）。両方を同じ形にすると区別が付かなくなる。
        'races': targets,
    }


def card_path(day):
    return os.path.join(CARDS_DIR, f'{day.isoformat()}.json')


def save_card(card):
    path = card_path(date.fromisoformat(card['date']))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(card, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return path


# カードの状態を1語で表す。地方は毎日どこかで開催しているが、重賞は毎日ない。
# **「今日は重賞が無い」と「カードが作れていない」を同じ扱いにしない。**
# 前者で警報を鳴らすと、平日はほぼ毎日鳴ることになり、
# 2026-08-01 を捕まえるための「買い目が届いていません」が意味を失う。
GATE_RUN = 'run'          # 対象レースがある → 検算する
GATE_NONE = 'none'        # 取得できて、該当なし → 何もしない（正常）
GATE_MISSING = 'missing'  # カードそのものが無い → 取得側が転んでいる（要確認）


def gate(day):
    path = card_path(day)
    if not os.path.exists(path):
        return GATE_MISSING, f'{path} がありません'
    try:
        with open(path, encoding='utf-8') as f:
            card = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return GATE_MISSING, f'{path} を読めません: {exc}'
    races = card.get('races') or []
    if not races:
        return GATE_NONE, f"{day} は {'・'.join(card.get('levels') or [])} がありません"
    return GATE_RUN, '／'.join(
        f"{r['venue']}{r['race_no']}R {r['name']}（{r['start_time']}）" for r in races)


def card_age_hours(day):
    """既にあるカードが何時間前のものか。無ければ None。"""
    path = card_path(day)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            generated = json.load(f).get('generated_at')
        made = datetime.fromisoformat(generated)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if made.tzinfo is None:
        made = made.replace(tzinfo=nar.JST)
    return (nar.datetime.now(nar.JST) - made).total_seconds() / 3600


# ----------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description='地方競馬の当日カードを作る')
    parser.add_argument('--date', help='対象日 (YYYY-MM-DD)。既定は今日（JST）')
    parser.add_argument('--levels', default='重賞,準重賞',
                        help='拾う競走種類。既定は 重賞,準重賞')
    parser.add_argument('--no-entries', action='store_true',
                        help='出馬表を付けずに一覧だけ作る（動作確認用）')
    parser.add_argument('--skip-if-fresh', type=float, metavar='時間',
                        help='この時間以内に作ったカードがあれば何もしない')
    parser.add_argument('--notify-missing', action='store_true',
                        help='カードが無いときだけメールで知らせる')
    parser.add_argument('--gate', action='store_true',
                        help=f'カードを作らず、状態を1語で出す'
                             f'（{GATE_RUN}／{GATE_NONE}／{GATE_MISSING}）。'
                             '検算を回すかどうかの判断に使う')
    args = parser.parse_args(argv)

    day = date.fromisoformat(args.date) if args.date else nar.today_jst()
    levels = tuple(x.strip() for x in args.levels.split(',') if x.strip())

    if args.gate:
        state, detail = gate(day)
        print(state)
        logger.info('%s: %s', state, detail)
        return EXIT_OK

    if args.notify_missing:
        state, detail = gate(day)
        if state != GATE_MISSING:
            logger.info('カードはあります（%s）。知らせることはありません。', state)
            return EXIT_OK
        body = '\n'.join([
            '⚠ 地方のカードが作れていません',
            '-' * 46,
            f'対象日: {day.isoformat()}',
            f'状態: {detail}',
            '',
            'data/cards/nar/ にその日のカードがないため、地方の直前検算を',
            '回せませんでした。重賞・準重賞があったかどうかも分かりません。',
            '',
            '「地方カード作成」ワークフローの実行結果を確認してください。',
            '手動実行（workflow_dispatch）は定時実行と違って遅延しません。',
            '',
        ])
        from mailer import Mailer
        if not Mailer().send(f'⚠ 地方カード未作成 {day.isoformat()}', body):
            # 通知できなかったときだけ赤にする（check.py と同じ規約）。
            return EXIT_ERROR
        return EXIT_NOTIFIED

    if args.skip_if_fresh:
        age = card_age_hours(day)
        if age is not None and age <= args.skip_if_fresh:
            logger.info('%s のカードは%.1f時間前に作成済みです。何もしません。', day, age)
            return EXIT_OK

    try:
        card = build_card(day, levels=levels, with_entries=not args.no_entries)
    except nar.NarError as exc:
        logger.error('%s', exc)
        return EXIT_ERROR
    except CardError as exc:
        # 開催が無い日は珍しくない（地方は毎日どこかでやっているが、
        # 対象日のデータが未掲載のこともある）。異常ではないので赤にしない。
        logger.info('%s', exc)
        return EXIT_OK

    path = save_card(card)
    logger.info('カードを書き出しました: %s', path)

    print(f"## {day} の地方カード（全{card['race_count']}レース / "
          f"{'・'.join(card['levels'])} {len(card['races'])}鞍）")
    print('')
    if not card['races']:
        print('該当なし。この日は重賞・準重賞がありません。')
        return EXIT_OK
    print('| 場 | R | 発走 | 種別 | レース名 | 距離 | 頭数 |')
    print('|---|---|---|---|---|---|---|')
    for race in card['races']:
        print(f"| {race['venue']} | {race['race_no']} | {race['start_time'] or ''} "
              f"| {race['grade']} | {race['name']} | {race['surface']}{race['distance']} "
              f"| {len(race.get('entries', [])) or race['field_size']} |")
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
