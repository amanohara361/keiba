#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""その日の出馬表データを集めて data/cards/YYYY-MM-DD.json にする。

**方針B（朝タスクのクラウド化）の取得側。判断は一切しない。**

  GitHub Actions（ここ）          Claude のセッション（判断側）
  netkeiba に到達できる            netkeiba に到達できない
  事実を集める・機械的に絞る        7ステップで印を打つ
  ↓                                ↓
  data/cards/YYYY-MM-DD.json ─読む→ data/bets/YYYY-MM-DD.json
                                    ↓
                            直前検算（check.py）が読む

分担線は予想メソッド 第14章がすでに引いている。1次スクリーニングは
「出馬表・クラス・頭数・オッズの概況のみで**機械的に**候補を絞り込む」と
書かれており、除外条件も優先度の上下も条文になっている。ここはそのまま実装できる。
2次選別（7ステップ本分析）は判断なので手を出さない。

このファイルが集めるのは、第2章の減点方式チェックリストと第8章の7つの視点が
必要とする事実である。前走着順・距離の延長短縮・騎手の乗り替わり・休み明けの
期間・脚質（通過順）・昇級初戦かどうか・前走がローカル場か——これらは
すべて1頭あたり1ページ（競走成績）から取れる。

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

import bets
import form as form_module

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))
CARDS_DIR = os.path.join(ROOT, 'data', 'cards')

RACE_LIST_URL = 'https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={ymd}'

VENUES = {
    '01': '札幌', '02': '函館', '03': '福島', '04': '新潟', '05': '東京',
    '06': '中山', '07': '中京', '08': '京都', '09': '阪神', '10': '小倉',
}

# 中央のローカル場。第2章の「前走ローカル場」判定に使う。
LOCAL_VENUES = {'小倉', '福島', '新潟', '函館', '札幌', '中京'}

# netkeiba のクラス表示アイコン。2026-08-09の実物で確認した対応。
#
# **16〜18は重賞ではなくクラス**である。ここを重賞と読み違えると、
# 浜名湖特別（2勝クラス）がG2に化けて1次スクリーニングを素通りする。
# 実際に一度そうなった。番号の意味は推測せず、実物を見て決めること。
#   3 → レパードS・CBC賞（本物のGIII）
#   5 → UHB賞（オープン特別）
#   16 → 佐渡S（3勝クラス）  17 → 浜名湖特別・驀進特別（2勝クラス）
#   18 → 石狩特別（1勝クラス）
# 知らない番号は None にする（重賞に化けるより、印が付かないほうが安全）。
GRADE_ICONS = {'1': 'G1', '2': 'G2', '3': 'G3', '4': '重賞', '5': 'OP',
               '15': 'L', '16': '3勝', '17': '2勝', '18': '1勝'}

# クラス表示は Icon_GradePos01 が付いた方。同じ塊にもう1つ
# Icon_GradeType13（別の意味の印）が入るので、位置で選び分ける。
GRADE_PATTERN = r'Icon_GradeType(\d+)\s+Icon_GradePos01'

REQUEST_INTERVAL = 1.0
HORSE_INTERVAL = 1.5

# 1次スクリーニングを通す候補の上限。2次選別で2〜5レースに絞るので、
# その倍ほど渡しておけば足りる。増やすほど取得が重くなる。
DEFAULT_CANDIDATES = 6

EXIT_OK = 0
EXIT_ERROR = 1


class CardError(RuntimeError):
    pass


# ----------------------------------------------------------------------
# その日のレース一覧
# ----------------------------------------------------------------------

def _blocks(page, marker='RaceList_DataItem'):
    """目印で切り分ける。入れ子の構造に依存しない。

    netkeiba は div の階層をよく変えるが、race_id を持つ塊が
    レース1つに対応するという関係は変わらない。
    """
    parts = page.split(marker)
    return parts[1:] if len(parts) > 1 else []


def parse_race_list(page):
    """その日の全レースを返す。

    race_id の12桁から場・回・日・R が引けるので、HTMLの見た目に依存する
    項目（レース名・発走時刻・距離）だけを緩く拾う。
    """
    races = []
    seen = set()
    for block in _blocks(page):
        found = re.search(r'race_id=(\d{12})', block)
        if not found:
            continue
        race_id = found.group(1)
        if race_id in seen:
            continue
        seen.add(race_id)

        text = form_module.strip_tags(block)
        name = re.search(r'class="ItemTitle"[^>]*>\s*([^<]+?)\s*<', block)
        start = re.search(r'(\d{1,2}:\d{2})', text)
        # 「芝1200m」「芝・右 1200m」「ダ・左1800m」など書き方に揺れがある。
        course = re.search(r'(芝|ダート|ダ|障)[・\s右左直外内]{0,6}(\d{3,4})\s*m', text)
        turn = re.search(r'(?:芝|ダート|ダ|障)\s*・?\s*(右|左|直線?)', text)
        field = re.search(r'(\d{1,2})\s*頭', text)
        grade = re.search(GRADE_PATTERN, block)

        surface = course.group(1) if course else None
        if surface == 'ダ':
            surface = 'ダート'

        races.append({
            'race_id': race_id,
            'venue': VENUES.get(race_id[4:6], race_id[4:6]),
            'race_no': int(race_id[10:12]),
            'name': name.group(1).strip() if name else '',
            'start_time': start.group(1) if start else None,
            'surface': surface,
            'distance': int(course.group(2)) if course else None,
            'turn': turn.group(1) if turn else None,
            'field_size': int(field.group(1)) if field else None,
            'grade': GRADE_ICONS.get(grade.group(1)) if grade else None,
        })
    races.sort(key=lambda r: (r['venue'], r['race_no']))
    return races


def fetch_race_list(day, opener=None):
    page = form_module._fetch(
        RACE_LIST_URL.format(ymd=day.strftime('%Y%m%d')), opener=opener)
    races = parse_race_list(page)
    if not races:
        raise CardError(f'{day} のレース一覧を読めませんでした（開催が無い日かページ構造の変更）')
    return races


# ----------------------------------------------------------------------
# 1次スクリーニング（第14章・機械的な絞り込み）
# ----------------------------------------------------------------------

# 上位クラスほど条件好転・危険な人気馬の判定がしやすい（第14章）
SENIOR_CLASSES = ('G1', 'G2', 'G3', '重賞', 'L')
JUNIOR_CLASSES = ('未勝利', '1勝')


def race_class(race):
    """クラスを判定する。アイコンを優先し、無ければレース名から読む。

    特別戦（浜名湖特別など）は名前にクラスが入らないので、アイコンが
    唯一の手がかりになる。逆に「3歳以上2勝クラス」のような名前の
    レースは名前から読める。"""
    if race.get('grade'):
        return race['grade']
    name = race.get('name') or ''
    for keyword, label in [('新馬', '新馬'), ('未勝利', '未勝利'),
                           ('1勝クラス', '1勝'), ('2勝クラス', '2勝'),
                           ('3勝クラス', '3勝'), ('オープン', 'OP')]:
        if keyword in name:
            return label
    return None


def screen(race, odds_view=None):
    """第14章の1次スクリーニングを機械的にあてる。

    条文をそのまま写しただけで、上乗せはしていない。点数の重みだけは
    条文に無いので決めたが、除外条件（新馬・障害）は点数と関係なく効く。
    """
    name = race.get('name') or ''
    klass = race_class(race)
    reasons = []

    # --- 除外条件（該当すれば無条件で不採用）---
    if race.get('surface') == '障' or '障害' in name:
        return {'excluded': '障害レース（メソッドが平地競走を前提にしている）',
                'score': 0, 'reasons': [], 'race_class': klass}
    if klass == '新馬' or ('新馬' in name and '2歳' in name):
        return {'excluded': '2歳新馬戦（情報不足のため無理に買わない）',
                'score': 0, 'reasons': [], 'race_class': klass}

    score = 0

    # --- 優先度を上げる材料 ---
    if klass in SENIOR_CLASSES:
        score += 3
        reasons.append(f'{klass}（データが厚く条件好転・危険な人気馬を判定しやすい）')
    elif klass in ('OP', '3勝'):
        score += 2
        reasons.append(f'{klass}クラス（上位クラス）')

    if 'ハンデ' in name:
        score += 1
        reasons.append('ハンデ戦（第13章の高配当条件）')
    if '牝' in name and klass in SENIOR_CLASSES:
        score += 1
        reasons.append('牝馬限定重賞（第5章・第9章が働きやすい）')

    # --- 優先度を下げる材料 ---
    if klass in JUNIOR_CLASSES:
        score -= 2
        reasons.append(f'{klass}（戦績が薄く比較材料に乏しい）')

    if odds_view:
        favourite = odds_view.get('favourite')
        if favourite is not None and favourite < 1.5:
            score -= 3
            reasons.append(f'1番人気が{favourite}倍の圧倒的人気'
                           '（第8章「1倍台の圧倒的1番人気は見送り」）')
        spread = odds_view.get('spread')
        if spread is not None and spread <= 3.0 and (favourite or 0) >= 3.0:
            score += 2
            reasons.append(f'上位人気が割れている（1〜5番人気の差 {spread}倍）')

    return {'excluded': None, 'score': score, 'reasons': reasons, 'race_class': klass}


def odds_view(race_id, opener=None):
    """単勝オッズの概況。1次スクリーニングが見るのはここまで。"""
    import odds as odds_module
    try:
        table = odds_module.fetch(race_id, '単勝', opener=opener)
    except Exception as exc:
        logger.warning('%s の単勝オッズを取得できませんでした: %s', race_id, exc)
        return None

    # 取消馬の除外と人気の取り出しは odds.py が既に持っている。
    win = odds_module.win_odds_table(table.get('odds') or {})
    if not win:
        return None

    ordered = sorted(odds for odds, _ in win.values())
    top = ordered[:5]
    return {
        'status': table.get('status'),
        'official_datetime': table.get('official_datetime'),
        'favourite': ordered[0],
        # 1〜5番人気の開き。小さいほど「割れている」（第14章）。
        'spread': round(top[-1] - top[0], 1) if len(top) > 1 else None,
        'win_odds': {umaban: odds for umaban, (odds, _) in sorted(win.items())},
        'ninki': {umaban: ninki for umaban, (_, ninki) in sorted(win.items()) if ninki},
    }


def select_candidates(races, limit=DEFAULT_CANDIDATES):
    """点数の高い順に候補を選ぶ。最終的に2〜5に絞るのは2次選別（人の判断）。"""
    alive = [r for r in races if not r['screening']['excluded']]
    alive.sort(key=lambda r: (-r['screening']['score'], r['start_time'] or ''))
    return alive[:limit]


# ----------------------------------------------------------------------
# 候補レースの詳細
# ----------------------------------------------------------------------

def build_entries(race_id, opener=None, interval=HORSE_INTERVAL, sleep=time.sleep):
    """出走各馬の事実を集める。評価はしない。"""
    entries = form_module.fetch_entries(race_id, opener=opener)
    out = []
    for umaban in sorted(entries):
        entry = dict(entries[umaban])
        entry['umaban'] = umaban
        # 競走成績と血統は別ページに分かれている（2026-08-11に確認）。
        sleep(interval)
        try:
            results_page = form_module._fetch(
                form_module.RESULT_URL.format(horse_id=entry['horse_id']), opener=opener)
            entry['going_record'] = form_module.parse_going_record(results_page)
            entry['recent'] = form_module.parse_recent_runs(results_page)
            sleep(interval)
            entry['pedigree'] = form_module.fetch_pedigree(entry['horse_id'], opener=opener)
            entry['error'] = None
        except Exception as exc:
            # 1頭失敗しても残りは集める。欠けたことは残す。
            entry.update({'going_record': {}, 'recent': [], 'pedigree': {},
                          'error': str(exc)})
            logger.warning('%s の戦績を取得できませんでした: %s', entry.get('name'), exc)
        out.append(entry)
    return out


def build_card(day, limit=DEFAULT_CANDIDATES, opener=None, sleep=time.sleep,
               with_entries=True):
    import conditions as conditions_module

    races = fetch_race_list(day, opener=opener)
    logger.info('%s: %dレースを一覧しました', day, len(races))

    for race in races:
        sleep(REQUEST_INTERVAL)
        view = None
        if not (race.get('surface') == '障' or '新馬' in (race.get('name') or '')):
            view = odds_view(race['race_id'], opener=opener)
        race['odds'] = view
        race['screening'] = screen(race, view)

    candidates = select_candidates(races, limit)
    logger.info('1次スクリーニング通過: %s',
                ', '.join(f"{r['venue']}{r['race_no']}R {r['name']}" for r in candidates))

    for race in candidates:
        race['candidate'] = True
        try:
            race['conditions'] = conditions_module.fetch(race['race_id'])
        except Exception as exc:
            logger.warning('%s の馬場を取得できませんでした: %s', race['race_id'], exc)
            race['conditions'] = None
        if with_entries:
            race['entries'] = build_entries(race['race_id'], opener=opener, sleep=sleep)

    return {
        'date': day.isoformat(),
        'generated_at': bets.now_jst().isoformat(timespec='seconds'),
        'source': 'actions',
        'note': ('1次スクリーニングまでが機械の担当。2次選別（7ステップ）と印は'
                 '予想メソッド第14章に従って判断側が行う。'),
        'races': races,
    }


def save_card(card):
    os.makedirs(CARDS_DIR, exist_ok=True)
    path = os.path.join(CARDS_DIR, f"{card['date']}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(card, f, ensure_ascii=False, indent=1)
        f.write('\n')
    return path


# 前夜に走らせるので、日付を指定しなければ「次の開催日」を対象にする。
# 夕方以降なら翌日、そうでなければ当日。この1行の規則で全部の呼ばれ方を賄える。
#   21:07 金 → 翌日=土 ／ 01:07 土 → 当日=土
#   07:00 土（取り直し）→ 当日=土 ／ 21:07 土 → 翌日=日
# 分岐をワークフロー側の if に置くと、手動実行でその枝を通れず、
# 定時実行でしか動かないコードが本番の経路に残る。
EVENING_HOUR = 18


def target_day(now=None):
    now = now or bets.now_jst()
    day = now.date()
    return day + timedelta(days=1) if now.hour >= EVENING_HOUR else day


def card_age_hours(day, now=None):
    """保存済みカードが何時間前のものか。無ければ None。"""
    card = load_card(day)
    if not card or not card.get('generated_at'):
        return None
    try:
        made = datetime.fromisoformat(card['generated_at'])
    except ValueError:
        return None
    now = now or bets.now_jst()
    return (now - made).total_seconds() / 3600.0


def load_card(day):
    path = os.path.join(CARDS_DIR, f'{day.isoformat()}.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


# ----------------------------------------------------------------------
# 調査（推測で正規表現をいじる前に、生のページを見る）
# ----------------------------------------------------------------------

def probe_list(day):
    page = form_module._fetch(RACE_LIST_URL.format(ymd=day.strftime('%Y%m%d')))
    print(f'取得サイズ: {len(page)} 文字')
    for marker in ['RaceList_DataItem', 'race_id=', 'ItemTitle',
                   'Icon_GradeType', 'RaceList_Itemtime', 'ItemLong']:
        print(f'  {marker}: {page.count(marker)} 箇所')

    blocks = _blocks(page)
    print(f'\n--- 1レース目の生HTML（先頭1200文字）---')
    print(blocks[0][:1200] if blocks else '(切り分けできませんでした)')

    # グレードアイコンの意味は推測できない。実物のマークアップを見る。
    shown = 0
    for block in blocks:
        if 'Icon_GradeType' not in block:
            continue
        title = re.search(r'class="ItemTitle"[^>]*>\s*([^<]+?)\s*<', block)
        icons = re.findall(r'<span[^>]*Icon_GradeType[^>]*>.*?</span>', block, re.S)
        print(f'\n--- {title.group(1) if title else "?"} のアイコン ---')
        for icon in icons:
            print(' ', icon.strip()[:200])
        shown += 1
        if shown >= 6:
            break

    races = parse_race_list(page)
    print(f'\n--- パース結果 {len(races)}件（先頭5件）---')
    for race in races[:5]:
        print(json.dumps(race, ensure_ascii=False))

    missing = {}
    for key in ('name', 'start_time', 'surface', 'distance', 'field_size'):
        blank = [r['race_id'] for r in races if not r.get(key)]
        if blank:
            missing[key] = len(blank)
    print(f'\n読めなかった項目: {missing or "なし"}')


def probe_entries(race_id):
    """出馬表を読み、その先頭の馬について競走成績まで通しで確かめる。"""
    entries = form_module.fetch_entries(race_id)
    print(f'出馬表: {len(entries)}頭')
    for umaban in sorted(entries)[:5]:
        print(' ', umaban, json.dumps(entries[umaban], ensure_ascii=False))
    if not entries:
        print('出馬表を読めませんでした')
        return
    first = entries[sorted(entries)[0]]
    print(f"\n=== {first['name']}（{first['horse_id']}）の馬ページ ===")
    probe_horse(first['horse_id'])


def probe_horse_search(name):
    """馬名からnetkeibaのhorse_idを引けるか調べる。

    交流重賞に出走するJRA所属馬は、地方の公式データ（keiba.go.jp）には
    馬名しか無くhorse_idが載っていない。中央の出馬表（form_module.fetch_entries）
    はレースごとにしか引けないため、名前から検索する経路が要る。
    URLの形はまだ確認していないので、まずここで実物を見る
    （決定ログ「ページ構造は推測せず、Actions の probe で実物を見る」）。
    """
    import urllib.parse
    url = f'https://db.netkeiba.com/?pid=horse_list&word={urllib.parse.quote(name)}'
    page = form_module._fetch(url)
    print(f'取得サイズ: {len(page)} 文字')
    print(f'URL: {url}')
    for marker in ['/horse/', 'horse_list', 'No.', '該当', 'nowrap']:
        print(f'  {marker}: {page.count(marker)} 箇所')

    title = re.search(r'<title>(.*?)</title>', page, re.S)
    print(f'\ntitle: {title.group(1).strip() if title else "(なし)"}')

    canonical = re.search(r'<link[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)', page)
    print(f'canonical: {canonical.group(1) if canonical else "(なし)"}')

    og_url = re.search(r'<meta[^>]*property=["\']og:url["\'][^>]*content=["\']([^"\']+)', page)
    print(f'og:url: {og_url.group(1) if og_url else "(なし)"}')

    # 検索結果一覧ページによく出る目印（1頭に絞れず候補が並ぶ形かどうか）
    for marker in ['db_h_race_results', 'RaceKind_result', 'horse_result', '該当馬', '件が該当']:
        print(f'  {marker}: {page.count(marker)} 箇所')

    ids = re.findall(r'/horse/(\d{10})/?["\'\s]', page)
    uniq = list(dict.fromkeys(ids))
    print(f'\n見つかった horse_id 候補: {len(uniq)}件')
    for hid in uniq[:10]:
        idx = page.find(hid)
        start = max(0, idx - 200)
        print(f'\n--- {hid} の前後400文字 ---')
        print(page[start:start + 400])


def probe_horse(horse_id):
    """馬の3ページを順に見て、パーサーが実際に読めるか確かめる。"""
    pages = {}
    for label, url in [('トップ', form_module.HORSE_URL),
                       ('競走成績', form_module.RESULT_URL),
                       ('血統', form_module.PED_URL)]:
        page = form_module._fetch(url.format(horse_id=horse_id))
        pages[label] = page
        print(f'\n=== {label}: {url.format(horse_id=horse_id)} ({len(page)}文字) ===')
        for name in re.findall(r'<table[^>]*class="([^"]*)"', page):
            print('  table:', name)

    print('\n--- パース結果 ---')
    runs = form_module.parse_recent_runs(pages['競走成績'])
    print(f'近走 {len(runs)}件:')
    for run in runs[:3]:
        print(' ', json.dumps(run, ensure_ascii=False))
    print('馬場別:', json.dumps(
        form_module.parse_going_record(pages['競走成績']), ensure_ascii=False))
    print('血統:', json.dumps(
        form_module.parse_pedigree(pages['血統']), ensure_ascii=False))


# ----------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description='当日の出馬表データを集める')
    sub = parser.add_subparsers(dest='command')

    build = sub.add_parser('build', help='カードを作る')
    build.add_argument('--date',
                       help='対象日 (YYYY-MM-DD)。既定は次の開催日（夕方以降なら翌日）')
    build.add_argument('--limit', type=int, default=DEFAULT_CANDIDATES,
                       help=f'詳細を集める候補レース数（既定{DEFAULT_CANDIDATES}）')
    build.add_argument('--no-entries', action='store_true',
                       help='一覧とスクリーニングまでで止める（動作確認用）')
    build.add_argument('--skip-if-fresh', type=float, metavar='時間',
                       help='この時間以内に作ったカードがあれば何もしない。'
                            '定時実行を二重にかけても取得が二度走らないようにする')

    probe = sub.add_parser('probe', help='ページ構造を調べる')
    probe.add_argument('target', choices=['list', 'entries', 'horse', 'horse-search'])
    probe.add_argument('value',
                       help='list なら YYYY-MM-DD、entries なら race_id、'
                            'horse なら horse_id、horse-search なら馬名')

    args = parser.parse_args(argv)

    if args.command == 'probe':
        if args.target == 'list':
            probe_list(date.fromisoformat(args.value))
        elif args.target == 'entries':
            probe_entries(args.value)
        elif args.target == 'horse-search':
            probe_horse_search(args.value)
        else:
            probe_horse(args.value)
        return EXIT_OK

    day = date.fromisoformat(args.date) if getattr(args, 'date', None) else target_day()

    # 定時実行を二重にかけておくための逃げ道。1本目が済んでいれば2本目は何もしない。
    # 前夜に作れば遅延は無害なので、朝に間に合わないという事故を構造的に消せる。
    if args.skip_if_fresh:
        age = card_age_hours(day)
        if age is not None and age <= args.skip_if_fresh:
            logger.info('%s のカードは%.1f時間前に作成済みです。何もしません。', day, age)
            return EXIT_OK

    try:
        card = build_card(day, limit=args.limit, with_entries=not args.no_entries)
    except CardError as exc:
        logger.error('%s', exc)
        return EXIT_ERROR

    path = save_card(card)
    logger.info('カードを書き出しました: %s', path)

    candidates = [r for r in card['races'] if r.get('candidate')]
    print(f"## {day} のカード（全{len(card['races'])}レース / 候補{len(candidates)}）")
    print('')
    print('| 場 | R | レース | クラス | 点 | 判定 |')
    print('|---|---|---|---|---|---|')
    for race in card['races']:
        screening = race['screening']
        verdict = screening['excluded'] or ('候補' if race.get('candidate') else '—')
        print(f"| {race['venue']} | {race['race_no']} | {race['name']} "
              f"| {screening['race_class'] or ''} | {screening['score']} | {verdict} |")
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
