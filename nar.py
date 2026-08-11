#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""地方競馬（NAR）公式データの取得と解析。

**中央とは取得の制約が違う。ここが設計の分かれ目である。**

  中央（netkeiba）        地方（keiba.go.jp）
  Actions だけが届く      Claude のセッションからも届く（実測 200 / application/zip）
  HTML をパースする        公式CSVがそのまま降ってくる
  クラスはアイコン番号      `競走種類名称` に 普通/特別/準重賞/重賞 と書いてある

中央では「浜名湖特別（2勝クラス）がG2に化けて1次スクリーニングを素通りした」
事故が起きたが、地方は主催者自身が重賞と書いているので同じ間違いが起きない。
だから地方の1次スクリーニングは推測を一切含まない。

データの素性は 予想メソッド 第14章「フェーズ2の標準データ基盤」に条文がある。
CSV の各項目の意味・検証済みの注意点（ハロンタイムは南関東4場＋園田のみ、
文字コードは UTF-8 BOM 付き、帯広ばんえいは対象外）もそこに書かれている。

外部ライブラリは使わない。
"""

import csv
import io
import logging
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

BASE = 'https://www.keiba.go.jp/KeibaWeb/DataDownload/{kind}DataDownload'

# 当日ファイルは約2分ごとに更新され、中間オッズを含む。
# 月次ファイルは毎日午前2時頃の更新で、**当日＋2日先まで**入っている
# （2026-08-12 に取得した8月分は 08-01〜08-14 を収録していた）。
# だから「次の重賞はいつか」は月次から2日前に分かる。
DAILY = 'daily'
MONTHLY = 'monthly'

# ばんえいは距離200mの特殊競走で、ハロンタイム・上がり3F等が存在しない。
# 予想メソッド 第14章が明示的に対象外としている。
EXCLUDED_VENUES = {'帯広ば'}

# 競馬場コード。race_id を組み立てるためだけに使う。
# **netkeiba の場コードとは無関係**。あちらは NAR も 12 桁の race_id を持つが、
# こちらは公式CSVしか見ないので、CSV から一意に復元できる番号を自前で振る。
# 一度決めたら変えないこと（過去の買い目ファイルの race_id が引けなくなる）。
VENUE_CODES = {
    '門別': '30', '盛岡': '35', '水沢': '36',
    '浦和': '42', '船橋': '43', '大井': '44', '川崎': '45',
    '金沢': '46', '笠松': '47', '名古屋': '48',
    '園田': '50', '姫路': '51',
    '高知': '54', '佐賀': '55',
    '帯広ば': '65',
}
CODE_VENUES = {v: k for k, v in VENUE_CODES.items()}

# 南関東4場＋園田はハロンタイムが収録されており、第11章の手順を完全適用できる。
NANKAN = {'大井', '川崎', '浦和', '船橋'}
FURLONG_VENUES = NANKAN | {'園田'}

# 主催者が付けている競走種類。推測の余地がない。
GRADE_LEVELS = ['重賞', '準重賞', '特別', '普通']

# 公式CSVの賭式名 → 予想メソッド第13章で扱う券種名。
# 地方は「馬複」「３連複」と全角で書く。bets.BET_TYPES の綴りに揃える。
BET_TYPE_NAMES = {
    '単勝': '単勝',
    '複勝': '複勝',
    '馬複': '馬連',
    'ワイド': 'ワイド',
    '馬単': '馬単',
    '３連複': '3連複',
}

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')

TIMEOUT = 90


class NarError(RuntimeError):
    pass


# ----------------------------------------------------------------------
# 取得
# ----------------------------------------------------------------------

def _url(kind, type_, day=None):
    url = BASE.format(kind=kind) + f'?type={type_}'
    if type_ == MONTHLY:
        if day is None:
            raise NarError('月次ファイルには対象月が要ります')
        url += f'&k_year={day.year}&k_month={day.month}'
    return url


def fetch_zip(kind, type_=DAILY, day=None, opener=None):
    """公式のZIPを取ってくる。戻り値は bytes。

    サーバに負荷をかけないよう、呼ぶ側で回数を絞ること
    （月次なら1日1回、当日ファイルでも数分に1回まで。第14章の注意事項）。
    """
    url = _url(kind, type_, day)
    request = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        open_url = opener or urllib.request.urlopen
        with open_url(request, timeout=TIMEOUT) as response:
            data = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise NarError(f'{url} を取得できませんでした: {exc}')
    if not data[:2] == b'PK':
        raise NarError(f'{url} がZIPを返しませんでした（{len(data)}バイト）')
    return data


def read_zip(data):
    """ZIP の中の CSV を全部読む。戻り値は {ファイル名: [行の辞書]}。

    文字コードは UTF-8 BOM 付き（第14章で検証済み）なので utf-8-sig で開く。
    """
    out = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if not name.lower().endswith('.csv'):
                    continue
                text = zf.read(name).decode('utf-8-sig', errors='replace')
                out[name] = list(csv.DictReader(io.StringIO(text)))
    except zipfile.BadZipFile as exc:
        # メンテナンス中のHTMLが200で返ることがある。ZIPの例外のまま
        # 上に投げると、呼ぶ側が「取得の失敗」として扱えない。
        raise NarError(f'ZIPとして開けませんでした（{len(data)}バイト）: {exc}')
    if not out:
        raise NarError('ZIPの中にCSVがありませんでした')
    return out


def _pick(tables, suffix):
    """ファイル名の末尾で選ぶ。月次のオッズは3分割されているので連結する。"""
    rows = []
    for name in sorted(tables):
        if name.lower().endswith(f'{suffix}.csv'):
            rows += tables[name]
    return rows


def race_data(type_=DAILY, day=None, opener=None):
    """レース情報（レース一覧・出馬表・払戻金）を取る。"""
    tables = read_zip(fetch_zip('Race', type_, day, opener))
    return {
        'racelist': _pick(tables, 'racelist'),
        'horselist': _pick(tables, 'horselist'),
        'payback': _pick(tables, 'payback'),
    }


def odds_data(type_=DAILY, day=None, opener=None):
    """オッズ情報を取る。当日ファイルは約2分ごとに更新される。"""
    return _pick(read_zip(fetch_zip('Odds', type_, day, opener)), 'odds')


# ----------------------------------------------------------------------
# race_id
# ----------------------------------------------------------------------

def race_id(venue, ymd, race_no):
    """公式CSVの3項目から12桁の race_id を作る。

    形式は YYYYMMDD + 場コード2桁 + R2桁。
    bets.py の契約（12桁の数字）を満たし、かつ CSV から完全に復元できる。

    **中央の race_id（netkeiba 形式）と桁数が同じで見分けが付かない**ので、
    買い目ファイル側は org='nar' を必ず持つこと。数字だけで判別しようとしない。
    """
    code = VENUE_CODES.get(venue)
    if code is None:
        raise NarError(f'知らない競馬場です: {venue}')
    return f'{ymd}{code}{int(race_no):02d}'


def split_race_id(rid):
    """race_id を (YYYYMMDD, 競馬場, R) に戻す。"""
    rid = str(rid)
    if len(rid) != 12 or not rid.isdigit():
        raise NarError(f'race_id は12桁の数字です: {rid}')
    venue = CODE_VENUES.get(rid[8:10])
    if venue is None:
        raise NarError(f'知らない場コードです: {rid[8:10]}')
    return rid[:8], venue, int(rid[10:12])


# ----------------------------------------------------------------------
# レース一覧
# ----------------------------------------------------------------------

def _int(value, default=None):
    value = (value or '').strip()
    return int(value) if value.isdigit() else default


def start_time(raw):
    """発走時刻 '1845' → '18:45'。空なら None。

    地方はナイターがあり、門別は20:40、大井は21時台まで発走がある。
    中央の感覚で「昼のうちに終わる」と考えると、直前検算の時刻を外す。
    """
    raw = (raw or '').strip()
    if not raw.isdigit() or len(raw) not in (3, 4):
        return None
    raw = raw.zfill(4)
    return f'{raw[:2]}:{raw[2:]}'


def parse_races(rows, day=None):
    """レース一覧CSVを、扱いやすい辞書のリストにする。

    day を渡すとその日だけに絞る（月次ファイルから1日を取り出す用）。
    ばんえいは第14章の定めにより最初から落とす。
    """
    ymd = day.strftime('%Y%m%d') if day else None
    races = []
    seen = set()
    for row in rows:
        venue = (row.get('競馬場') or '').strip()
        if venue in EXCLUDED_VENUES or venue not in VENUE_CODES:
            continue
        date_raw = (row.get('競走年月日') or '').strip()
        if ymd and date_raw != ymd:
            continue
        no = _int(row.get('レース番号'))
        if no is None:
            continue
        rid = race_id(venue, date_raw, no)
        # 同じレースを二度数えない。当日ファイルと月次ファイルは範囲が重なる。
        if rid in seen:
            continue
        seen.add(rid)
        races.append({
            'race_id': rid,
            'org': 'nar',
            'date': date_raw,
            'venue': venue,
            'race_no': no,
            'name': (row.get('レース名') or '').strip(),
            'grade': (row.get('競走種類名称') or '').strip(),
            'start_time': start_time(row.get('発走時刻')),
            'surface': (row.get('芝ダート区分') or '').strip(),
            'turn': (row.get('回り') or '').strip(),
            'distance': _int(row.get('距離')),
            'field_size': _int(row.get('頭数')),
            'condition': (row.get('条件') or '').strip(),
            'weather': (row.get('天候') or '').strip(),
            'going': (row.get('馬場') or '').strip(),
            'prize_1st': _int(row.get('1着賞金(円)')),
            'has_furlong_times': venue in FURLONG_VENUES,
        })
    races.sort(key=lambda r: (r['date'], r['start_time'] or '99:99', r['venue']))
    return races


def graded(races, levels=('重賞', '準重賞')):
    """重賞・準重賞だけを残す。**これが地方の1次スクリーニングの全て。**

    予想メソッド 第14章は「地方重賞は引き続き必ず確認対象」と定めている。
    主催者が `競走種類名称` に書いているので、こちらで基準を作る必要がない。
    特別戦まで広げるなら地方向けの採点基準を新設することになるが、
    それは重賞が回り始めてからの話（2026-08-12 の判断）。
    """
    return [r for r in races if r['grade'] in levels]


# ----------------------------------------------------------------------
# 出馬表
# ----------------------------------------------------------------------

def _record(value):
    """'3-1-0-9' → {'1着':3, '2着':1, '3着':0, '着外':9, 'starts':13}"""
    parts = (value or '').strip().split('-')
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return None
    win, place, show, out = (int(p) for p in parts)
    return {'1着': win, '2着': place, '3着': show, '着外': out,
            'starts': win + place + show + out}


def parse_entries(rows, rid):
    """出馬表CSVから1レース分を取り出す。

    **第12章の分析シートのテンプレート項目がほぼこれだけで埋まる**
    （血統3代・負担重量・馬体重増減・当競馬場成績・当距離成績・
    ダート左右別成績まで揃う）。中央のように1頭ずつページを叩く必要がない。

    馬体重は発走の直前まで空のことがある。着順・タイム・上がり3F・人気は
    確定後に埋まる（第14章の注意事項）。空を「0」と読み替えないこと。
    """
    ymd, venue, no = split_race_id(rid)
    entries = []
    for row in rows:
        if (row.get('競馬場') or '').strip() != venue:
            continue
        if (row.get('競走年月日') or '').strip() != ymd:
            continue
        if _int(row.get('レース番号')) != no:
            continue
        umaban = _int(row.get('馬番'))
        if umaban is None:
            continue
        entries.append({
            'umaban': umaban,
            'wakuban': _int(row.get('枠番')),
            'name': (row.get('馬名') or '').strip(),
            'sex': (row.get('性') or '').strip(),
            'age': _int(row.get('齢')),
            # 生年月日は近走の突き合わせに使う。地方には同名馬がいるので
            # 馬名だけを鍵にすると別の馬の成績を貼ってしまう。
            'birth': (row.get('生年月日') or '').strip(),
            'jockey': (row.get('騎手名') or '').strip(),
            'jockey_belong': (row.get('騎手所属') or '').strip(),
            'weight_carried': (row.get('負担重量') or '').strip(),
            'trainer': (row.get('調教師') or '').strip(),
            'trainer_belong': (row.get('調教師所属') or '').strip(),
            # 交流重賞には中央馬が出てくる。所属が 'JRA' と書かれているので
            # 判別できる（2026-08-11 クラスターカップJpnIII で実データ確認）。
            # **この馬の近走は NAR のCSVに1走も無い。**「0走＝休み明け」と
            # 読むと真逆の結論になるので、必ずここを見てから解釈すること。
            'belongs_jra': (row.get('調教師所属') or '').strip() == 'JRA',
            'horse_weight': _int(row.get('馬体重')),
            'horse_weight_diff': (row.get('馬体重増減') or '').strip() or None,
            'sire': (row.get('父馬名') or '').strip(),
            'dam': (row.get('母馬名') or '').strip(),
            'broodmare_sire': (row.get('母父馬名') or '').strip(),
            'record_all': _record(row.get('全成績')),
            'record_dirt_left': _record(row.get('ダート左成績')),
            'record_dirt_right': _record(row.get('ダート右成績')),
            'record_venue': _record(row.get('当競馬場成績')),
            'record_distance': _record(row.get('うち当距離成績')),
            'best_time': (row.get('最高タイム') or '').strip() or None,
            'best_time_firm': (row.get('最高タイム良馬場') or '').strip() or None,
        })
    entries.sort(key=lambda e: e['umaban'])
    return entries


# ----------------------------------------------------------------------
# オッズ
# ----------------------------------------------------------------------

def _key(numbers, bet_type):
    """馬番の組をキーにする。馬単だけ着順が意味を持つので並べ替えない。"""
    numbers = [int(n) for n in numbers]
    if bet_type != '馬単':
        numbers = sorted(numbers)
    return tuple(numbers)


def parse_odds(rows, rid):
    """オッズCSVから1レース分を券種別に取り出す。

    戻り値は {'単勝': {(7,): 4.2, ...}, '馬連': {(3,7): 11.4, ...}, ...}。
    人気は単勝だけ別に返す（第13章の単勝レンジ判定に人気は要らないが、
    メールの本文に出すと状況が分かる）。
    """
    ymd, venue, no = split_race_id(rid)
    tables = {}
    ninki = {}
    for row in rows:
        if (row.get('競馬場') or '').strip() != venue:
            continue
        if (row.get('競走年月日') or '').strip() != ymd:
            continue
        if _int(row.get('レース番号')) != no:
            continue
        bet_type = BET_TYPE_NAMES.get((row.get('賭式') or '').strip())
        if bet_type is None:
            continue
        numbers = [_int(row.get(f'番号{i}')) for i in (1, 2, 3)]
        numbers = [n for n in numbers if n is not None]
        if not numbers:
            continue
        raw = (row.get('オッズ') or '').strip().replace(',', '')
        try:
            value = float(raw)
        except ValueError:
            continue
        tables.setdefault(bet_type, {})[_key(numbers, bet_type)] = value
        if bet_type == '単勝':
            ninki[numbers[0]] = _int(row.get('人気'))
    return tables, ninki


def odds_for(tables, numbers, bet_type):
    """買い目1点の実オッズ。見つからなければ None。

    複勝は下限〜上限の幅を持つが、ここでは下限（`オッズ` 列）を返す。
    **期待値を低く見積もる側に倒す**ので、規律の判定が甘くならない。
    """
    return tables.get(bet_type, {}).get(_key(numbers, bet_type))


def win_odds_table(tables, ninki):
    """単勝オッズ表を {馬番: (オッズ, 人気)} にする。odds.py と同じ形。"""
    return {n[0]: (o, ninki.get(n[0]))
            for n, o in (tables.get('単勝') or {}).items()}


def collect(race, fetcher=None):
    """1レースの買い目に必要なオッズをまとめる。**odds.py の collect と同じ形を返す。**

    check.py は org を見てどちらを呼ぶか決めるだけでよく、
    その先の第13章の判定は中央と地方で完全に同じコードが通る。

    公式CSVは1回のダウンロードで全場・全レース・全券種が入るので、
    中央のように券種ごとに叩き分ける必要がない。
    """
    fetcher = fetcher or (lambda: odds_data(DAILY))
    errors = []
    try:
        rows = fetcher()
    except NarError as exc:
        errors.append(str(exc))
        rows = []

    tables, ninki = parse_odds(rows, race.race_id) if rows else ({}, {})
    bet_odds = [odds_for(tables, bet.horses, bet.type) for bet in race.bets]

    # 当日ファイルは発売開始まで中身が空（ヘッダだけ）で降ってくる。
    # それを「取得できた・0件だった」と静かに扱わない（決定ログ「取得できなかった
    # ことを静かに0件と出さない」）。status で呼び出し側に伝える。
    status = 'middle' if tables else 'yoso'
    if errors:
        status = None

    return bet_odds, win_odds_table(tables, ninki), {
        'per_type': {t: {'status': status, 'count': len(v)}
                     for t, v in tables.items()},
        'errors': errors,
        'status': status,
        'official_datetime': datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S'),
    }


# ----------------------------------------------------------------------
# 確定結果（週次レビューの入力）
# ----------------------------------------------------------------------

# 払戻金CSVの列の並び。同じ券種でも組の数が違う（複勝3・ワイド3・他1）ので、
# 「何番目の組か」を表す接尾辞のパターンごとに書き下す。
# 桁数を推測せず、実物の列名（2026-08 の月次ファイル）をそのまま持つ。
PAYOUT_COLUMNS = [
    ('単勝',   ['単勝組番'], '単勝払戻金（円）'),
    ('複勝',   ['複勝組番1'], '複勝払戻金1（円）'),
    ('複勝',   ['複勝組番2'], '複勝払戻金2（円）'),
    ('複勝',   ['複勝組番3'], '複勝払戻金3（円）'),
    ('馬連',   ['馬複組番1', '馬複組番2'], '馬複払戻金（円）'),
    ('馬単',   ['馬単組番1', '馬単組番2'], '馬単払戻金（円）'),
    ('ワイド', ['ワイド組番1馬番1', 'ワイド組番1馬番2'], 'ワイド払戻金1（円）'),
    ('ワイド', ['ワイド組番2馬番1', 'ワイド組番2馬番2'], 'ワイド払戻金2（円）'),
    ('ワイド', ['ワイド組番3馬番1', 'ワイド組番3馬番2'], 'ワイド払戻金3（円）'),
    ('3連複', ['３連複組番馬番1', '３連複組番馬番2', '３連複組番馬番3'], '３連複払戻金（円）'),
    ('3連単', ['３連単組番馬番1', '３連単組番馬番2', '３連単組番馬番3'], '３連単払戻金（円）'),
]


def parse_payouts(rows, rid):
    """払戻金CSVから1レース分を results.py と同じ形にする。

    **同着はレースにつき複数行で表される**（2026-08 の実データで確認。
    帯広8/10-9R と高知8/1-10R が該当）。先頭行だけを見ると片方を落とすので、
    その race_id の行を全部たたんで組を集める。
    """
    ymd, venue, no = split_race_id(rid)
    payouts = {}
    for row in rows:
        if (row.get('競馬場') or '').strip() != venue:
            continue
        if (row.get('競走年月日') or '').strip() != ymd:
            continue
        if _int(row.get('レース番号')) != no:
            continue
        for bet_type, number_cols, yen_col in PAYOUT_COLUMNS:
            numbers = [_int(row.get(c)) for c in number_cols]
            yen = _int(row.get(yen_col))
            if yen is None or any(n is None for n in numbers):
                continue
            entry = {'combination': numbers, 'yen': yen}
            bucket = payouts.setdefault(bet_type, [])
            if entry not in bucket:
                bucket.append(entry)
    return payouts


def parse_finishing_order(rows, rid):
    """出馬表CSVの確定後の着順を results.py と同じ形にする。

    着順が数字でない行（中止・除外・取消）は落とす。0 で埋めない。
    """
    order = []
    ymd, venue, no = split_race_id(rid)
    for row in rows:
        if (row.get('競馬場') or '').strip() != venue:
            continue
        if (row.get('競走年月日') or '').strip() != ymd:
            continue
        if _int(row.get('レース番号')) != no:
            continue
        rank = _int(row.get('着順'))
        umaban = _int(row.get('馬番'))
        if rank is None or umaban is None:
            continue
        item = {'rank': rank, 'umaban': umaban,
                'name': (row.get('馬名') or '').strip()}
        ninki = _int(row.get('人気'))
        if ninki is not None:
            item['ninki'] = ninki
        order.append(item)
    order.sort(key=lambda h: h['rank'])
    return order


def results_for(rid, day=None, fetcher=None):
    """1レースの確定結果。まだ出ていなければ None。results.fetch と同じ形。

    月次ファイルを見る（当日ファイルは日付が変わると入れ替わるため、
    翌週の週次レビューからは引けない）。
    """
    ymd, _, _ = split_race_id(rid)
    month = day or datetime.strptime(ymd, '%Y%m%d').date()
    data = (fetcher or race_data)(MONTHLY, month)
    order = parse_finishing_order(data['horselist'], rid)
    if not order:
        return None
    return {
        'race_id': rid,
        'finishing_order': order,
        'payouts': parse_payouts(data['payback'], rid),
    }


# ----------------------------------------------------------------------

def today_jst():
    return datetime.now(JST).date()


def main(argv=None):
    """動作確認用。`python nar.py calendar` で直近の重賞予定が出る。"""
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    parser = argparse.ArgumentParser(description='地方競馬の公式データを見る')
    sub = parser.add_subparsers(dest='command', required=True)

    cal = sub.add_parser('calendar', help='直近の重賞・準重賞の予定')
    cal.add_argument('--month', help='YYYY-MM。既定は今月')
    cal.add_argument('--all', action='store_true', help='全レースを出す')

    day_p = sub.add_parser('today', help='当日ファイルの中身')
    day_p.add_argument('--all', action='store_true')

    od = sub.add_parser('odds', help='1レースのオッズ')
    od.add_argument('race_id')

    args = parser.parse_args(argv)

    if args.command == 'odds':
        tables, ninki = parse_odds(odds_data(DAILY), args.race_id)
        if not tables:
            print('オッズはまだ出ていません（発売前）')
            return 0
        for bet_type in sorted(tables):
            print(f'-- {bet_type} {len(tables[bet_type])}点')
        print(json.dumps({str(k): v for k, v in win_odds_table(tables, ninki).items()},
                         ensure_ascii=False))
        return 0

    if args.command == 'today':
        races = parse_races(race_data(DAILY)['racelist'])
    else:
        month = (datetime.strptime(args.month, '%Y-%m').date()
                 if args.month else today_jst())
        races = parse_races(race_data(MONTHLY, month)['racelist'])

    if not getattr(args, 'all', False):
        races = graded(races)

    print(f'| 日付 | 場 | R | 発走 | 種別 | レース名 | 距離 |')
    print('|---|---|---|---|---|---|---|')
    for r in races:
        print(f"| {r['date']} | {r['venue']} | {r['race_no']} | {r['start_time'] or ''} "
              f"| {r['grade']} | {r['name']} | {r['distance']} |")
    print(f'\n{len(races)}件')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
