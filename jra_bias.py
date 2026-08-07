#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
JRA（中央競馬）トラックバイアス判定ツール

nar_csv.py の bias 機能をJRA向けに移植したもの。
予想メソッド 第10章「トラックバイアスの見極め方」／第11章「ペースとトラックバイアスの切り分け手順」
を機械的に実行する。

■ NAR版との違い
  1. データ源：NARは主催者公式のCSV配布があるが、**JRAには公式の一括配布が存在しない**ため、
     netkeibaのレース結果ページ(db.netkeiba.com)から取得してローカルにJSONで蓄積する。
  2. 芝とダートを必ず分けて判定する（JRAは同日に両方あり、バイアスの性質がまったく異なる）。
  3. 前半タイムは netkeiba のラップ表にある「ペース (前半-後半)」の値を直接使う。
     NAR版は「勝ちタイム − 上がり3F」で逆算していたが、JRAは公式ラップが取れるため直接値の方が正確。
     取得できない場合のみ逆算にフォールバックする。

■ 使い方（バッチファイルからも起動できる）
  python jra_bias.py final                    # 直前チェック（バイアス＋実オッズ＋合成オッズ）※直前チェック.bat
  python jra_bias.py menu                     # 対話モードでバイアス判定 ※バイアス確認.bat
  python jra_bias.py check                    # 初回の動作確認 ※動作確認.bat
  python jra_bias.py fetch 20260726 札幌      # 当日12R分を取得してdata_jra\ に保存
  python jra_bias.py fetch 20260726 all       # 当日の開催全場を取得
  python jra_bias.py selftest                 # スポーツナビのページ構造が変わっていないか確認
  python jra_bias.py urls   20260801 202601010311 1 8   # 取得すべきスポーツナビURLを一覧表示
  python jra_bias.py ingest 20260801 札幌      # data_snav\ に保存した結果ページを取り込む
  python jra_bias.py bias  20260726 札幌      # トラックバイアス判定（芝・ダート別）
  python jra_bias.py bias  20260726 all       # 保存済みの全場をまとめて判定
  python jra_bias.py race  20260726 札幌 10   # 1レースの脚質・ペース内訳
  python jra_bias.py odds  202604020207 "単勝 1" "3連複 1-3-8"   # 実オッズと合成オッズ
  python jra_bias.py probe 20260726 札幌      # 列構成の確認（パース失敗時の調査用）

■ 動作条件（他のPCへ持ち出す場合）
  ・Python 3.x のみ。外部ライブラリのインストールは不要（標準ライブラリだけで動く）。
  ・jra_bias.py と .bat を同じフォルダに置けば、場所はどこでもよい（USBメモリでも可）。
  ・Claudeアプリは不要。data_jra\ も一緒に持っていくと過去の蓄積を引き継げる。
  ・Mac/Linuxでも .py はそのまま動く（.bat の代わりに python3 jra_bias.py final を実行）。

■ 注意
  ・netkeibaは主催者ではなく民間サイト。取得はサーバに負荷をかけない範囲に留めること
    （本スクリプトはレース間に1.5秒のウェイトを入れている）。
  ・HTML構造の変更で動かなくなる可能性がある。その場合は probe コマンドで列構成を確認する。
  ・当日分だけでは「平常値」の母数が足りない。開催を重ねてdata_jra\ に蓄積するほど判定精度が上がる。
  ・ダートは構造的に前有利のため、蓄積が少ないうちは「外伸び」と誤警報する。
    出力に「蓄積不足のため1.00を仮基準」と出ている間は、ダートの総合判定文は割り引いて読むこと。
"""

import sys, os, re, json, time, html, collections
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data_jra')

# netkeibaのrace_id用 場コード
VENUES = {
    '札幌': '01', '函館': '02', '福島': '03', '新潟': '04', '東京': '05',
    '中山': '06', '中京': '07', '京都': '08', '阪神': '09', '小倉': '10',
}
CODE2VENUE = {v: k for k, v in VENUES.items()}

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'


# ---------------- HTML取得・パース ----------------

# Claudeのサンドボックスは全通信が localhost:3128 のプロキシを経由し、
# 許可リストにないホストへの CONNECT を 403 で即座に拒否する。
# db.netkeiba.com も race.netkeiba.com も sports.yahoo.co.jp も許可されていない
# （通るのは pypi/npm/github 等のみ。2026-08-02 に実測で確認）。
# netkeiba側の拒否ではなくこちらの通信ポリシーによる遮断なので、リトライしても無駄。
BLOCKED_HINT = """
   ┌─────────────────────────────────────────────────────────
   │ これはサンドボックスの通信ポリシーによる遮断です（netkeiba側の拒否ではありません）。
   │ 全通信が localhost:3128 のプロキシを経由し、許可リストにないホストへの
   │ CONNECT が 403 で拒否されます。リトライしても状況は変わりません。
   │
   │ Claudeから実行している場合は、スポーツナビ経由の代替経路を使ってください:
   │   1. python jra_bias.py urls {date} <race_id> 1 12
   │   2. 表示されたURLを web_fetch で取得し、Write で data_snav\\ に保存
   │      （ページ全文は不要。次の4ブロックだけで ingest は通る:
   │        レース条件行 / ## 競走成績 / ## 通過タイム（ラップタイム） / ## コーナー通過順位）
   │   3. python jra_bias.py ingest {date} <場名>
   │   4. python jra_bias.py bias   {date} <場名>
   │
   │ Windows側で実行している場合は、この経路制限はないため
   │ バイアス確認.bat（fetch経由）がそのまま使えます。
   └─────────────────────────────────────────────────────────"""


def _is_blocked_by_proxy(e):
    """プロキシの許可リストによる遮断かどうかを判定する"""
    s = str(e)
    return ('Tunnel connection failed' in s or '403' in s
            or 'Forbidden' in s or 'Connection refused' in s)


def fetch_html(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=30) as res:
        raw = res.read()
    # db.netkeiba.com は EUC-JP。まれにUTF-8が混ざるため両対応にする。
    for enc in ('euc_jp', 'utf-8'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('euc_jp', errors='replace')


def strip_tags(s):
    s = re.sub(r'<[^>]+>', '', s)
    return html.unescape(s).replace('\xa0', ' ').strip()


def parse_rows(table_html):
    """テーブルHTMLを [[セル文字列, ...], ...] に変換。空セルも保持する。"""
    rows = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.S):
        cells = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', tr, re.S)
        if cells:
            rows.append([strip_tags(c) for c in cells])
    return rows


def find_result_table(page):
    """結果テーブル（race_table_01）を返す。クラス名が変わっても拾えるよう二段構えにする。"""
    m = re.search(r'<table[^>]*class="[^"]*race_table_01[^"]*"[^>]*>(.*?)</table>', page, re.S)
    if m:
        return m.group(1)
    # フォールバック: 「着 順」を含む最初のテーブル
    for t in re.findall(r'<table[^>]*>(.*?)</table>', page, re.S):
        if re.search(r'着\s*順', strip_tags(t)):
            return t
    return None


def col_index(header):
    """ヘッダー行から列位置を特定する。列順の変更に耐えるための仕組み。"""
    idx = {}
    for i, h in enumerate(header):
        k = re.sub(r'\s+', '', h)
        if k.startswith('着順'):   idx.setdefault('着順', i)
        elif k.startswith('枠番'): idx.setdefault('枠番', i)
        elif k.startswith('馬番'): idx.setdefault('馬番', i)
        elif k == '馬名':          idx.setdefault('馬名', i)
        elif k == '性齢':          idx.setdefault('性齢', i)
        elif k == '斤量':          idx.setdefault('斤量', i)
        elif k == '騎手':          idx.setdefault('騎手', i)
        elif k == 'タイム':        idx.setdefault('タイム', i)
        elif k == '着差':          idx.setdefault('着差', i)
        elif k == '通過':          idx.setdefault('通過', i)
        elif k in ('上り', '上がり'): idx.setdefault('上り', i)
        elif k == '単勝':          idx.setdefault('単勝', i)
        elif k == '人気':          idx.setdefault('人気', i)
        elif k.startswith('馬体重'): idx.setdefault('馬体重', i)
    return idx


def to_sec(t):
    """'1:27.9' → 87.9 秒"""
    t = (t or '').strip()
    m = re.match(r'^(?:(\d+):)?(\d+)\.(\d)$', t)
    if not m:
        return None
    mi, se, de = m.group(1), m.group(2), m.group(3)
    return (int(mi) * 60 if mi else 0) + int(se) + int(de) / 10


def parse_race(page, race_id):
    """1レース分のHTMLを辞書に変換。パースできなければ None。"""
    tb = find_result_table(page)
    if not tb:
        return None
    rows = parse_rows(tb)
    if len(rows) < 2:
        return None
    idx = col_index(rows[0])
    if '着順' not in idx or '通過' not in idx:
        return None

    # レース条件（例: 芝右1500m / 天候 : 曇 / 芝 : 良 / 発走 : 14:50）
    head = strip_tags(re.search(r'<div class="data_intro">(.*?)</div>', page, re.S).group(1)) \
        if re.search(r'<div class="data_intro">(.*?)</div>', page, re.S) else strip_tags(page[:4000])
    mcond = re.search(r'(芝|ダ|ダート|障)\s*(左|右|直線)?\s*(?:外|内)?\s*(\d{3,4})m', head)
    surface = dist = None
    if mcond:
        s = mcond.group(1)
        surface = '芝' if s == '芝' else ('障' if s == '障' else 'ダ')
        dist = int(mcond.group(3))
    mgoing = re.search(r'(?:芝|ダート)\s*:\s*(良|稍重|重|不良)', head)
    going = mgoing.group(1) if mgoing else None
    # h1はページ先頭のnetkeibaロゴにも使われているため、中身のある最初のものを採る
    name = ''
    for m in re.finditer(r'<h1[^>]*>(.*?)</h1>', page, re.S):
        t = strip_tags(m.group(1))
        if t and t.lower() != 'netkeiba':
            name = t
            break
    mno = re.search(r'(\d+)\s*R', head)
    rno = int(mno.group(1)) if mno else int(race_id[-2:])

    # ラップ表の「ペース」行から前半・後半3Fを取る（例: ... (30.4-34.1)）
    first3 = last3 = None
    mp = re.search(r'\((\d+\.\d)-(\d+\.\d)\)', page)
    if mp:
        first3, last3 = float(mp.group(1)), float(mp.group(2))
    mlap = re.search(r'ラップ\s*</th>\s*<td[^>]*>(.*?)</td>', page, re.S)
    laps = []
    if mlap:
        laps = [float(x) for x in re.findall(r'\d+\.\d', strip_tags(mlap.group(1)))]

    horses = []
    for r in rows[1:]:
        if len(r) <= max(idx.values()):
            continue
        fin = r[idx['着順']].strip()
        corner = r[idx['通過']].strip()
        if not fin.isdigit() or not corner:
            continue  # 中止・除外・取消などは母集団から除く
        passes = [int(x) for x in re.findall(r'\d+', corner)]
        horses.append({
            '着順': int(fin),
            '枠番': r[idx['枠番']] if '枠番' in idx else '',
            '馬番': int(re.sub(r'\D', '', r[idx['馬番']]) or 0) if '馬番' in idx else 0,
            '馬名': r[idx['馬名']] if '馬名' in idx else '',
            '斤量': r[idx['斤量']] if '斤量' in idx else '',
            '騎手': r[idx['騎手']] if '騎手' in idx else '',
            'タイム': r[idx['タイム']] if 'タイム' in idx else '',
            '通過': corner,
            '_passes': passes,
            '上り': r[idx['上り']] if '上り' in idx else '',
            '単勝': r[idx['単勝']] if '単勝' in idx else '',
            '人気': r[idx['人気']] if '人気' in idx else '',
            '馬体重': r[idx['馬体重']] if '馬体重' in idx else '',
        })
    if not horses:
        return None
    horses.sort(key=lambda h: h['着順'])

    win = horses[0]
    wt = to_sec(win['タイム'])
    ag3 = None
    try:
        ag3 = float(win['上り'])
    except (TypeError, ValueError):
        pass
    # ペース行が取れなければ NAR版と同じ「勝ちタイム − 上がり3F」で逆算する
    if first3 is None and wt and ag3:
        first3 = round(wt - ag3, 1)

    return {
        'race_id': race_id, 'R': rno, 'レース名': name,
        '馬場': surface, '距離': dist, '馬場状態': going,
        '頭数': len(horses), '勝ちタイム': wt, '上がり3F': ag3,
        '前半3F': first3, '後半3F': last3, 'ラップ': laps,
        'horses': horses,
    }


# ---------------- 脚質判定（NAR版と同一ロジック） ----------------

def leg_style(pos, n):
    """最終コーナー通過順位と出走頭数から脚質を判定"""
    if pos is None or not n:
        return '?'
    if pos == 1:
        return '逃'
    ratio = pos / n
    if ratio <= 0.35:
        return '先'
    if ratio <= 0.70:
        return '差'
    return '追'


def attach_legs(race):
    n = race['頭数']
    for h in race['horses']:
        last = h['_passes'][-1] if h['_passes'] else None
        h['_leg'] = leg_style(last, n)
    return race


# ---------------- 取得 ----------------

def race_ids_of_day(date):
    """その日の開催レース一覧から race_id を取得する。
    「回」「日」を総当たりすると最大72回もアクセスすることになるため、
    一覧ページ1回で済ませる。サーバ負荷の観点でもこちらが正しい。
    """
    return race_list_status(date)[0]


def race_list_status(date):
    """(race_idの辞書, その日に開催のあった場名リスト) を返す。

    db.netkeiba.com の一覧ページは、**当日中は個別レースへのリンク（12桁のrace_id）を
    出さず、場ごとの「成績」「払戻金一覧」へのリンクだけを並べる**。
    個別レースが並ぶのはnetkeiba側の取り込みが終わった翌日以降。
    そのため当日に叩くと race_id が0件になり、「開催がない」と誤認しやすい。
    開催の有無そのものは race/sum/{場コード}/{date}/ の有無で判別できるので、
    「開催はあるが未反映」と「そもそも開催なし」を区別できるようにする。
    （2026-08-02、当日18時台に db の 20260802 ページで実地確認）
    """
    page = fetch_html(f'https://db.netkeiba.com/race/list/{date}/')
    ids = sorted(set(re.findall(r'/race/(\d{12})/', page)))
    out = collections.defaultdict(list)
    for rid in ids:
        v = CODE2VENUE.get(rid[4:6])
        if v:
            out[v].append(rid)
    held = []
    for code in sorted(set(re.findall(r'/race/(?:sum|pay)/(\d{2})/' + re.escape(date), page))):
        v = CODE2VENUE.get(code)
        if v and v not in held:
            held.append(v)
    return out, held


NOT_IMPORTED_HINT = """  !! {date} は開催はありましたが（{venues}）、netkeiba側の取り込みがまだ終わっておらず
     個別レースの一覧が出ていません。**当日中はこの状態が普通です。**
     翌日以降に同じ日付で実行すれば取得できます。
     当日のバイアスをいま知りたい場合は、スポーツナビ経由の代替経路を使ってください:
       python jra_bias.py urls {date} <race_id> 1 12  →  web_fetchで取得してdata_snav\\へ保存
       → python jra_bias.py ingest {date} <場名>  →  python jra_bias.py bias {date} <場名>"""


def do_fetch(date, venue):
    os.makedirs(DATA, exist_ok=True)
    try:
        byvenue, held = race_list_status(date)
    except Exception as e:
        print(f'!! レース一覧の取得に失敗しました: {e}')
        if _is_blocked_by_proxy(e):
            print(BLOCKED_HINT.format(date=date))
        return
    if not byvenue:
        if held:
            print(NOT_IMPORTED_HINT.format(date=date, venues='・'.join(held)))
        else:
            print(f'!! {date} に開催が見つかりませんでした（一覧ページに race_id がありません）')
        return

    targets = list(byvenue.keys()) if venue == 'all' else [venue]
    for v in targets:
        ids = byvenue.get(v)
        if not ids:
            print(f'!! {v}: {date} の開催がありません（この日の開催場: {"・".join(byvenue.keys())}）\n')
            continue
        got = []
        miss = 0
        for rid in sorted(ids):       # R昇順（＝時系列順）で処理する
            try:
                p = fetch_html(f'https://db.netkeiba.com/race/{rid}/')
                rc = parse_race(p, rid)
            except Exception as e:
                print(f'   {rid} 取得失敗: {e}')
                rc = None
            if rc:
                rc['競馬場'] = v
                rc['日付'] = date
                got.append(rc)
                miss = 0
                print(f'   {v}{rc["R"]:>2}R {rc["馬場"] or "?"}{rc["距離"] or "?"}m '
                      f'{rc["馬場状態"] or "?"} {rc["頭数"]}頭  {rc["レース名"][:20]}')
            else:
                miss += 1
                # レースは時系列順に並ぶため、2つ続けて結果が無ければ以降は未実施とみなして打ち切る。
                # 開催途中（14:30の直前タスク等）に実行したとき、無駄なアクセスを避けるための処理。
                if miss >= 2 and got:
                    print(f'   （{v}: 以降は未実施と判断して打ち切り。{len(got)}レース取得）')
                    break
            time.sleep(1.5)       # サーバへの配慮
        if got:
            got.sort(key=lambda r: r['R'])
            path = os.path.join(DATA, f'{date}_{v}.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(got, f, ensure_ascii=False, indent=1)
            print(f'=> {path} に {len(got)}レースを保存\n')
        else:
            print(f'!! {v}: レースを1件も取得できませんでした。probe で列構成を確認してください\n')


def load_day(date, venue):
    path = os.path.join(DATA, f'{date}_{venue}.json')
    if not os.path.exists(path):
        print(f'!! {path} がありません。先に fetch を実行してください:')
        print(f'   python jra_bias.py fetch {date} {venue}')
        sys.exit(1)
    with open(path, encoding='utf-8') as f:
        return [attach_legs(r) for r in json.load(f)]


def load_all(venue=None, surface=None):
    """蓄積済みの全データ。平常値（ベースライン）の算出に使う。"""
    out = []
    if not os.path.isdir(DATA):
        return out
    for fn in sorted(os.listdir(DATA)):
        if not fn.endswith('.json'):
            continue
        with open(os.path.join(DATA, fn), encoding='utf-8') as f:
            for r in json.load(f):
                if venue and r.get('競馬場') != venue:
                    continue
                if surface and r.get('馬場') != surface:
                    continue
                out.append(attach_legs(r))
    return out


# ---------------- バイアス判定 ----------------

def pace_of(race, ref):
    """同競馬場・同馬場種別・同距離の平均前半3Fと比べてハイ/平均/スローを判定"""
    f = race.get('前半3F')
    key = (race['競馬場'], race['馬場'], race['距離'])
    base = ref.get(key)
    if f is None or base is None:
        return '不明', None
    d = f - base
    if d <= -0.5:
        return 'ハイ', d
    if d >= 0.5:
        return 'スロー', d
    return '平均', d


def analyze_bias(date, venue):
    # venue='all' なら、その日に保存済みの全場をまとめて判定する
    if venue == 'all':
        found = False
        if os.path.isdir(DATA):
            for fn in sorted(os.listdir(DATA)):
                m = re.match(rf'{date}_(.+)\.json$', fn)
                if m:
                    found = True
                    analyze_bias(date, m.group(1))
        if not found:
            print(f'!! {date} の保存データがありません。先に fetch を実行してください。')
        return

    races = [r for r in load_day(date, venue) if r['馬場'] in ('芝', 'ダ')]
    if not races:
        print('解析対象のレースがありません（芝・ダートの平地のみが対象です）')
        return
    races.sort(key=lambda r: r['R'])

    print(f'\n===== {date} {venue} トラックバイアス判定 =====')
    print('※予想メソッド 第10章・第11章の手順を機械的に適用したものです。')

    for surface in ('芝', 'ダ'):
        rs = [r for r in races if r['馬場'] == surface]
        if not rs:
            continue
        print(f'\n---------- 【{surface}】{len(rs)}レース ----------')

        # 基準タイム（同競馬場・同馬場・同距離の蓄積平均）
        allr = load_all(venue=venue, surface=surface)
        acc = collections.defaultdict(list)
        for r in allr:
            if r.get('前半3F'):
                acc[(r['競馬場'], r['馬場'], r['距離'])].append(r['前半3F'])
        ref = {k: sum(v) / len(v) for k, v in acc.items()}

        print(f'{"R":>3} {"距離":>5} {"状態":<4} {"前半3F":>7} {"基準差":>7} {"ペース":<5} {"上3F":>5}  '
              f'{"1〜3着の脚質":<14} {"4〜8着の脚質"}')
        tally = collections.Counter()
        pace_bucket = collections.defaultdict(list)
        pace_races = collections.defaultdict(list)
        front = back = mfront = mback = 0

        for r in rs:
            p, d = pace_of(r, ref)
            top3 = [h['_leg'] for h in r['horses'][:3]]
            mid = [h['_leg'] for h in r['horses'][3:8]]
            tally.update(top3)
            pace_bucket[p].extend(top3)
            pace_races[p].append(r['R'])
            front += sum(1 for x in top3 if x in '逃先')
            back += sum(1 for x in top3 if x in '差追')
            mfront += sum(1 for x in mid if x in '逃先')
            mback += sum(1 for x in mid if x in '差追')
            print(f'{r["R"]:>3} {r["距離"]:>5} {r["馬場状態"] or "?":<4} '
                  f'{r["前半3F"] or 0:>7.1f} {(f"{d:+.1f}" if d is not None else "-"):>7} '
                  f'{p:<5} {r["上がり3F"] or 0:>5.1f}  {"/".join(top3):<14} {"/".join(mid)}')

        # 母集団比率でのベースライン。leg_styleの定義上、素の前後比較は必ず「後」に偏るため必須の補正。
        field = collections.Counter()
        for r in rs:
            field.update(h['_leg'] for h in r['horses'])
        ffront = field['逃'] + field['先']
        fback = field['差'] + field['追']
        base_front = ffront / (ffront + fback) if (ffront + fback) else 0.35

        # 同競馬場・同馬場種別の平常値。蓄積が少ないうちは 1.00（フラット）を仮基準とする。
        vt = vb = vm = vmb = vf = vfb = 0
        for r in allr:
            for i, h in enumerate(r['horses']):
                isf = 1 if h.get('_leg', '?') in '逃先' else 0
                vf += isf; vfb += 1 - isf
                if i < 3:
                    vt += isf; vb += 1 - isf
                elif i < 8:
                    vm += isf; vmb += 1 - isf
        vbase = vf / (vf + vfb) if (vf + vfb) else base_front
        enough = (vt + vb) >= 300     # 100レース程度は欲しい
        norm = (vt / (vt + vb)) / vbase if (vt + vb) and enough else 1.00
        norm_mid = (vm / (vm + vmb)) / vbase if (vm + vmb) and enough else 1.00

        def idx(f, b):
            tot = f + b
            return (f / tot) / base_front if tot and base_front else None

        def judge(i, n=None):
            if i is None:
                return None
            return i / (n if n is not None else norm)

        i_top, i_mid = idx(front, back), idx(mfront, mback)
        print(f'\n判定材料（母集団比率で正規化）:')
        print(f'  当日{surface}の全完走馬 前×後 = {ffront}×{fback}（前の母集団比率 {base_front*100:.0f}%）')
        if enough:
            print(f'  {venue}{surface}の平常値（蓄積{len(allr)}レース）: 3着内 {norm:.2f} ／ 4〜8着 {norm_mid:.2f}')
        else:
            print(f'  {venue}{surface}の平常値: **蓄積不足のため 1.00（フラット）を仮基準**'
                  f'（現在{len(allr)}レース。100レース程度貯まると精度が上がります）')
        if i_top is not None:
            print(f'  3着内   前×後 = {front}×{back}  → 指数 {i_top:.2f}（平常比 {judge(i_top)*100:.0f}%）')
        if i_mid is not None:
            print(f'  4〜8着  前×後 = {mfront}×{mback}  → 指数 {i_mid:.2f}（平常比 {judge(i_mid, norm_mid)*100:.0f}%）')

        # 手順2: ペースから想定される脚質と、実際の結果とのズレを見る
        print(f'\nペース別の3着内脚質（前脚質の出現指数。1.00＝母集団どおり）:')
        verdict = []
        for p in ('ハイ', '平均', 'スロー'):
            arr = pace_bucket[p]
            if not arr:
                continue
            f = sum(1 for x in arr if x in '逃先')
            b = sum(1 for x in arr if x in '差追')
            i = idx(f, b)
            j = judge(i)
            print(f'  {p:<4} 前{f:>3} × 後{b:>3}  指数 {i:.2f}（平常比 {j*100:.0f}%）'
                  f'  （{len(pace_races[p])}レース: {pace_races[p]}）')
            if len(pace_races[p]) < 2:
                continue
            if p == 'ハイ' and j >= 1.15:
                verdict.append('★ハイペースなのに逃げ先行が上位＝強烈なイン前バイアスの可能性')
            elif p == 'スロー' and j <= 0.85:
                verdict.append('★スローペースなのに差し追込が上位＝強烈な外差しバイアスの可能性')
            elif p == 'スロー' and j >= 1.15:
                verdict.append('スローで前残り＝展開どおり（バイアスとは言えない）')
            elif p == 'ハイ' and j <= 0.85:
                verdict.append('ハイペースで差し決着＝展開どおり（バイアスとは言えない）')

        # 手順4: 4〜8着の分布で裏を取る
        j_top, j_mid = judge(i_top), judge(i_mid, norm_mid)
        if j_mid is not None and j_top is not None:
            if j_mid >= 1.15 and j_top < 1.15:
                verdict.append('★3着内は差し優勢に見えるが、4〜8着を前々の馬が占めている＝'
                               '実は前有利バイアスが働いていた可能性（手順4）')
            elif j_mid <= 0.85 and j_top > 0.85:
                verdict.append('3着内は前残りに見えるが、中団〜後方の馬が4〜8着に押し寄せている＝'
                               '馬場が外伸びへ傾きつつある可能性（手順4）')

        if not verdict:
            verdict.append('顕著な矛盾なし。当日の' + surface + 'は脚質フラットと見るのが妥当')
        print('\n→ 判定:')
        for v in verdict:
            print(f'   ・{v}')

    print('\n※本判定は統計的な目安です。最終的な馬場判断は映像（第10章）と併せて行ってください。')
    print('※前半3Fはnetkeibaのラップ表の値（取れない場合は「勝ちタイム − 上がり3F」で逆算）。')
    print('※芝はコース替わり（A→B→Cコース）で傾向が変わるため、開催週をまたぐ比較には注意してください。')


def show_race(date, venue, rno):
    races = load_day(date, venue)
    r = next((x for x in races if x['R'] == int(rno)), None)
    if not r:
        print(f'{venue}{rno}R が見つかりません')
        return
    print(f'\n{date} {venue}{r["R"]}R {r["レース名"]}  {r["馬場"]}{r["距離"]}m {r["馬場状態"]} {r["頭数"]}頭')
    if r['ラップ']:
        print(f'  ラップ: {" - ".join(f"{x:.1f}" for x in r["ラップ"])}')
    print(f'  勝ちタイム {r["勝ちタイム"]}  前半3F {r["前半3F"]}  上がり3F {r["上がり3F"]}')
    print(f'\n{"着":>3} {"馬番":>4} {"馬名":<16} {"脚質":<4} {"通過":<10} {"人気":>4} {"上り":>5} {"単勝":>7} {"馬体重":>10} {"騎手"}')
    for h in r['horses']:
        print(f'{h["着順"]:>3} {h["馬番"]:>4} {h["馬名"]:<16} {h["_leg"]:<4} {h["通過"]:<10} '
              f'{h["人気"] or "-":>4} {h["上り"] or "-":>5} {h["単勝"] or "-":>7} '
              f'{h["馬体重"] or "-":>10} {h["騎手"]}')


def probe(date, venue):
    """パースに失敗したとき、実際の列構成を確認するための調査コマンド。"""
    try:
        byvenue = race_ids_of_day(date)
    except Exception as e:
        print(f'レース一覧の取得に失敗: {e}')
        return
    ids = byvenue.get(venue)
    if not ids:
        print(f'{date} の {venue} 開催が見つかりません（開催場: {"・".join(byvenue.keys()) or "なし"}）')
        return
    rid = ids[-1]                      # メイン近くのレースで確認する
    page = fetch_html(f'https://db.netkeiba.com/race/{rid}/')
    print(f'race_id={rid}')
    tb = find_result_table(page)
    if not tb:
        print('  結果テーブルが見つかりません（HTML構造が変わった可能性）')
        return
    rows = parse_rows(tb)
    print('  --- ヘッダー行 ---')
    for i, c in enumerate(rows[0]):
        print(f'   [{i:>2}] {c!r}')
    if len(rows) > 1:
        print('  --- 1着の行 ---')
        for i, c in enumerate(rows[1]):
            print(f'   [{i:>2}] {c!r}')
    print(f'  --- 認識した列位置 ---\n   {col_index(rows[0])}')
    r = parse_race(page, rid)
    print(f'  --- parse_race の結果 ---\n   '
          f'{ {k: v for k, v in r.items() if k != "horses"} if r else "パース失敗" }')

# ---------------- スポーツナビ経由の取り込み（サンドボックス用の代替経路） ----------------
# 背景: Claudeの実行環境はプロキシのホワイトリスト方式で db.netkeiba.com に到達できないため、
#       do_fetch() が使えない。そこで Claude が web_fetch で取得したスポーツナビの結果ページを
#       SNAV\ にテキスト保存し、本関数群で parse_race() と同じ形の辞書に変換する。
#       Windows側で バイアス確認.bat を回す従来の経路は一切変更していない。

SNAV = os.path.join(ROOT, 'data_snav')

# スポーツナビの短縮race_idは netkeiba の12桁から先頭 '20' を除いた10桁
SNAV_URL = 'https://sports.yahoo.co.jp/keiba/race/result/{short}'


def snav_url(race_id):
    return SNAV_URL.format(short=str(race_id)[2:])


def _cell(text):
    """markdownセルからリンク記法・強調・改行タグを除いた素のテキスト"""
    t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text or '')
    t = t.replace('<br>', '\n').replace('**', '')
    return t.strip()


def _md_rows(block):
    """markdownテーブルの本文行を列リストにして返す（区切り行は捨てる）"""
    rows = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith('|'):
            continue
        cells = [c for c in line.strip('|').split('|')]
        if all(re.fullmatch(r'\s*:?-{2,}:?\s*', c) for c in cells):
            continue          # |---|---| の区切り行
        rows.append([_cell(c) for c in cells])
    return rows


def _section(page, title):
    """'## タイトル' から次の '## ' 直前までを切り出す"""
    m = re.search(r'^##\s+' + re.escape(title) + r'\s*$(.*?)(?=^##\s|\Z)',
                  page, re.S | re.M)
    return m.group(1) if m else ''


def parse_corner_order(s, expected=None):
    """JRA式のコーナー通過順位文字列 → {馬番: 順位}

    記号の意味:
      ()  併走。**括弧内の馬は同順位**になり、次の馬は括弧内の頭数だけ順位が飛ぶ。
      *   その時点の先頭馬。括弧の中にある場合、その馬だけが単独で先頭順位を取り、
          残りの併走馬は1つ下の順位で並ぶ。
      , - =  馬間の距離を表すだけで順位には影響しない。

    例 '(1,6,*9)(7,8)(2,4)(5,3)' → {9:1, 1:2, 6:2, 7:4, 8:4, 2:6, 4:6, 5:8, 3:8}

    この規則は netkeiba の各馬別通過順位と完全一致することを
    2026-07-26 札幌12R の4コーナー分すべてで実測照合済み。
    """
    order = {}
    pos = 1
    for grp, single in re.findall(r'\(([^)]*)\)|(\\?\*?\d+)', s or ''):
        body = grp if grp else single
        nums, starred = [], None
        for m in re.finditer(r'(\\?\*)?\s*(\d+)', body):
            n = int(m.group(2))
            if expected and n > expected:
                continue
            nums.append(n)
            if m.group(1):
                starred = n
        if not nums:
            continue
        if starred is not None and len(nums) > 1:
            order.setdefault(starred, pos)
            for n in nums:
                if n != starred:
                    order.setdefault(n, pos + 1)
        else:
            for n in nums:
                order.setdefault(n, pos)
        pos += len(nums)
    return order


def parse_snav(page, race_id):
    """スポーツナビの結果ページ（web_fetchのmarkdown出力）を parse_race() 互換の辞書へ"""
    # ---- レース条件 ----
    mcond = re.search(r'(芝|ダート|障害)・?(?:右|左|直線)?\s*(\d{3,4})m', page)
    if not mcond:
        return None
    surface = {'芝': '芝', 'ダート': 'ダ', '障害': '障'}[mcond.group(1)]
    dist = int(mcond.group(2))

    mgo = re.search(r'馬場：\s*\n+\s*(良|稍重|重|不良)', page)
    going = mgo.group(1) if mgo else None

    mno = re.search(r'^(\d{1,2})R\s*$', page, re.M)
    rno = int(mno.group(1)) if mno else int(str(race_id)[-2:])

    # レース名は条件行の直前にある '## 見出し'
    name = ''
    for m in re.finditer(r'^##\s+(.+?)\s*$', page, re.M):
        if m.end() < mcond.start():
            name = m.group(1)
    name = re.sub(r'\s+', '', name)
    mcls = re.search(r'(新馬|未勝利|1勝|2勝|3勝|オープン|１勝|２勝|３勝)クラス', page)
    if mcls and name and 'クラス' not in name:
        cls = mcls.group(1).translate(str.maketrans('１２３', '123'))
        if not re.search(r'[(（]', name):
            name = f'{name}({cls})'

    # ---- 競走成績 ----
    rows = _md_rows(_section(page, '競走成績'))
    if len(rows) < 2:
        return None
    horses = []
    for r in rows[1:]:
        if len(r) < 8:
            continue
        fin = r[0].strip()
        if not fin.isdigit():
            continue          # 中止・除外・取消は母集団から除く（netkeiba版と同じ）
        # 過去日は '03-04-06-04\\n34.8'、当日は '34.8' のみ（通過順位は月曜更新のため）
        c5 = [x.strip() for x in r[5].split('\n') if x.strip()]
        psg, agari = ('-'.join(str(int(x)) for x in re.findall(r'\\d+', c5[0])), c5[1]) \
            if len(c5) >= 2 else ('', c5[0] if c5 else '')
        nm, _, prof = r[3].partition('\n')
        pr = prof.split('/')
        jk, _, kin = r[6].partition('\n')
        odds = re.search(r'\(([\d.]+)\)', r[7])
        pop = r[7].split('\n')[0].strip()
        horses.append({
            '着順': int(fin),
            '枠番': r[1].strip(),
            '馬番': int(re.sub(r'\D', '', r[2]) or 0),
            '馬名': nm.strip(),
            '斤量': re.sub(r'\.0$', '', kin.strip()),
            '騎手': jk.replace(' ', '').replace('　', '').strip(),
            'タイム': r[4].split('\n')[0].strip(),
            '通過': psg,
            '_passes': [int(x) for x in re.findall(r'\\d+', psg)],
            '上り': agari,
            '単勝': odds.group(1) if odds else '',
            '人気': pop if pop.isdigit() else '',
            '馬体重': pr[1].strip() if len(pr) > 1 else '',
        })
    if not horses:
        return None
    horses.sort(key=lambda h: h['着順'])
    n = len(horses)

    # ---- コーナー通過順位を各馬の通過順位へ展開 ----
    # スポーツナビは当日中は各馬別の通過順位を載せない（月曜更新）ため、
    # レース単位のコーナー通過順位表から復元する。脚質判定は最終コーナーだけを見るので
    # 4コーナー（障害・短距離では最終行）が取れれば実用上は足りる。
    corners = []
    for r in _md_rows(_section(page, 'コーナー通過順位'))[1:]:
        if len(r) >= 2 and 'コーナー' in r[0]:
            corners.append(parse_corner_order(r[1], expected=max(h['馬番'] for h in horses)))
    if corners and not any(h['_passes'] for h in horses):
        for h in horses:
            seq = [c.get(h['馬番']) for c in corners]
            seq = [p for p in seq if p]
            h['_passes'] = seq
            h['通過'] = '-'.join(str(p) for p in seq)

    # ---- ラップ ----
    laps, cum = [], {}
    for r in _md_rows(_section(page, '通過タイム（ラップタイム）')):
        labels = [x for x in r if re.fullmatch(r'\d+m', x)]
        if labels:
            head = r
            continue
        for lab, val in zip(head, r):
            m = re.match(r'([\d:.]+)\((\d+\.\d)\)', val)
            if m and re.fullmatch(r'\d+m', lab):
                cum[int(lab[:-1])] = to_sec(m.group(1)) or float(m.group(1))
                laps.append((int(lab[:-1]), float(m.group(2))))
    laps.sort()
    lap_vals = [v for _, v in laps]

    win = horses[0]
    wt = to_sec(win['タイム'])
    try:
        ag3 = float(win['上り'])
    except (TypeError, ValueError):
        ag3 = None

    first3 = cum.get(600)
    if first3 is None and len(lap_vals) >= 3:
        first3 = round(sum(lap_vals[:3]), 1)
    if first3 is None and wt and ag3:
        first3 = round(wt - ag3, 1)          # netkeiba版と同じ逆算フォールバック
    last3 = None
    if len(lap_vals) >= 3:
        last3 = round(sum(lap_vals[-3:]), 1)
    elif wt is not None and dist and (dist - 600) in cum:
        last3 = round(wt - cum[dist - 600], 1)

    return {
        'race_id': str(race_id), 'R': rno, 'レース名': name,
        '馬場': surface, '距離': dist, '馬場状態': going,
        '頭数': n, '勝ちタイム': wt, '上がり3F': ag3,
        '前半3F': first3, '後半3F': last3, 'ラップ': lap_vals,
        'horses': horses, '_source': 'snav',
    }


TESTS = os.path.join(ROOT, 'tests')

# 期待値は netkeiba の同一レースの実データ、および公式確定成績と突き合わせて確定させたもの。
SELFTEST_CASES = [
    # (ファイル, race_id, 期待するレース情報, 期待する各馬 {馬番: (通過, 脚質)})
    ('fixture_past_20260726_202601010212.txt', '202601010212',
     {'レース名': '厚岸特別(1勝)', '馬場': '芝', '距離': 2000, '馬場状態': '良', '頭数': 9,
      '勝ちタイム': 120.2, '上がり3F': 34.8, '前半3F': 35.9, '後半3F': 35.2},
     {4: ('3-4-6-4', '差'), 6: ('2-2-2-2', '先'), 1: ('1-1-2-1', '逃'),
      9: ('9-9-1-3', '先'), 7: ('3-4-4-9', '追')}),
    ('fixture_sameday_20260801_202601010311.txt', '202601010311',
     {'レース名': 'STV賞(3勝)', '馬場': '芝', '距離': 2000, '馬場状態': '稍重', '頭数': 16,
      '勝ちタイム': 120.2, '上がり3F': 35.3, '前半3F': 35.4, '後半3F': 36.1},
     {10: ('11-11-10-7', '差'), 12: ('14-14-14-13', '追'),
      1: ('1-1-1-1', '逃'), 9: ('5-5-4-3', '先')}),
]


def selftest():
    """スポーツナビのページ構造が変わっていないかを確認する回帰テスト。

    スポーツナビは当日中は各馬別の通過順位を載せず（月曜午後に更新）、
    当日はコーナー通過順位表からの復元経路、過去日は各馬別の列を読む経路と、
    通る道が変わる。両方を1本ずつ固定データで検証している。
    """
    ng = 0
    for fn, rid, want, hwant in SELFTEST_CASES:
        path = os.path.join(TESTS, fn)
        if not os.path.exists(path):
            print(f'!! テストデータがありません: {path}')
            ng += 1
            continue
        with open(path, encoding='utf-8') as f:
            rc = parse_snav(f.read(), rid)
        if not rc:
            print(f'NG {fn}: 解析できませんでした')
            ng += 1
            continue
        attach_legs(rc)
        for k, v in want.items():
            if rc.get(k) != v:
                print(f'NG {fn}: {k} = {rc.get(k)!r}（期待 {v!r}）')
                ng += 1
        byno = {h['馬番']: h for h in rc['horses']}
        for no, (psg, leg) in hwant.items():
            h = byno.get(no)
            if not h:
                print(f'NG {fn}: 馬番{no} が見つかりません')
                ng += 1
            elif (h['通過'], h['_leg']) != (psg, leg):
                print(f'NG {fn}: 馬番{no} 通過={h["通過"]!r}/{h["_leg"]}（期待 {psg!r}/{leg}）')
                ng += 1
        print(f'   {fn}  {rc["レース名"]} {rc["頭数"]}頭 … 照合完了')
    if ng:
        print(f'\n!! {ng}件の不一致。スポーツナビのページ構造が変わった可能性があります。')
        print('   parse_snav() を確認してください。')
        return 1
    print('\n=> OK。スポーツナビのページ構造は想定どおりです。')
    return 0


def do_ingest(date, venue, srcdir=None, outdir=None):
    """SNAV\\{date}_{race_id}.txt を読み、data_jra\\{date}_{場}.json を作る／更新する"""
    src = srcdir or SNAV
    out = outdir or DATA
    if not os.path.isdir(src):
        print(f'!! {src} がありません。先にスポーツナビの結果ページを保存してください。')
        return
    files = sorted(f for f in os.listdir(src)
                   if f.startswith(date + '_') and f.endswith('.txt'))
    if not files:
        print(f'!! {src} に {date}_*.txt がありません。')
        return

    byvenue = collections.defaultdict(list)
    for fn in files:
        rid = fn[len(date) + 1:-4]
        v = CODE2VENUE.get(rid[4:6])
        if not v or (venue != 'all' and v != venue):
            continue
        with open(os.path.join(src, fn), encoding='utf-8') as f:
            page = f.read()
        try:
            rc = parse_snav(page, rid)
        except Exception as e:
            print(f'   {rid} 解析失敗: {e}')
            rc = None
        if not rc:
            print(f'   {rid} 解析失敗（結果未確定のページの可能性があります）')
            continue
        rc['競馬場'] = v
        rc['日付'] = date
        byvenue[v].append(rc)
        print(f'   {v}{rc["R"]:>2}R {rc["馬場"] or "?"}{rc["距離"] or "?"}m '
              f'{rc["馬場状態"] or "?"} {rc["頭数"]}頭  {rc["レース名"][:20]}')

    os.makedirs(out, exist_ok=True)
    for v, got in byvenue.items():
        path = os.path.join(out, f'{date}_{v}.json')
        merged = {}
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                for r in json.load(f):
                    merged[str(r['race_id'])] = r      # 既存（netkeiba取得分）を優先して残す
        for r in got:
            merged.setdefault(r['race_id'], r)
        rows = sorted(merged.values(), key=lambda r: r['R'])
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print(f'=> {path} に {len(rows)}レース（今回取り込み {len(got)}）\n')


def do_urls(date, base_race_id, rfrom=1, rto=12):
    """web_fetchすべきスポーツナビURLと保存先ファイル名を一覧出力する。
    base_race_id は同一開催のいずれかのレースID（年+場+回+日+R の12桁）。
    Claudeはこの一覧のURLを web_fetch し、本文をそのまま保存先パスに書き出す。
    """
    base = str(base_race_id)[:10]
    v = CODE2VENUE.get(base[4:6], '?')
    print(f'# {date} {v}  R{rfrom}〜R{rto}')
    for r in range(int(rfrom), int(rto) + 1):
        rid = f'{base}{r:02d}'
        print(f'{snav_url(rid)}\t{os.path.join("data_snav", f"{date}_{rid}.txt")}')




ODDS_API = 'https://race.netkeiba.com/api/api_get_jra_odds.html?type={t}&locale=ja&race_id={r}&action=update'
TYPE_NAMES = {'1': '単勝', '4': '馬連', '5': 'ワイド', '6': '馬単', '7': '3連複'}


def get_odds(race_id, t='1'):
    """netkeiba公式オッズAPIから実オッズを取得する。
    action=update を使うこと（init はキャッシュされた古い値を返す）。
    """
    import json as _json
    url = ODDS_API.format(t=t, r=race_id)
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=30) as res:
        d = _json.loads(res.read().decode('utf-8'))
    return d


def key_of(nums, t):
    """馬番のリストをAPIのキー形式に変換する。例 [1,3,8] -> '010308'"""
    ns = sorted(int(x) for x in nums) if t in ('4', '5', '7') else [int(x) for x in nums]
    return ''.join(f'{n:02d}' for n in ns)


def show_odds(race_id, bets=None):
    """単勝オッズ一覧と、指定された買い目の実オッズ・合成オッズを表示する。
    bets: [('7', [1,3,8]), ('1', [1])] のような (type, 馬番リスト) のリスト
    """
    try:
        d = get_odds(race_id, '1')
    except Exception as e:
        print(f'  !! オッズ取得に失敗しました: {e}')
        return
    st = d.get('status')
    dt = d.get('data', {}).get('official_datetime', '?')
    label = {'yoso': '予想オッズ(発売前)', 'middle': '発売中の途中経過', 'result': '確定オッズ'}.get(st, st)
    print(f'\n  [{race_id}]  {label}  取得時刻 {dt}')
    tan = d.get('data', {}).get('odds', {}).get('1', {})
    if not tan:
        print('  !! オッズがありません（race_idが違う可能性があります）')
        return
    rows = sorted(tan.items(), key=lambda kv: int(kv[1][2]) if str(kv[1][2]).isdigit() else 9999)
    print('   人気  馬番   単勝')
    for num, v in rows:
        o, _, ninki = v[0], v[1], v[2]
        if str(o) == '-3.0':
            print(f'   {"取消":>4}  {int(num):>3}      -   ← 出走取消・除外')
        else:
            print(f'   {int(ninki):>4}  {int(num):>3}  {float(o):>6.1f}')

    if not bets:
        return
    print('\n  買い目の実オッズ:')
    vals = []
    for t, nums in bets:
        try:
            dd = get_odds(race_id, t)
            od = dd.get('data', {}).get('odds', {}).get(t, {})
            k = key_of(nums, t)
            v = od.get(k)
            name = TYPE_NAMES.get(t, t)
            combo = '-'.join(str(int(x)) for x in nums)
            if v is None:
                print(f'   {name} {combo}: 見つかりません（キー {k}）')
                continue
            o = float(str(v[0]).replace(',', ''))
            print(f'   {name} {combo}: {o:.1f}倍（{v[2]}番人気）')
            vals.append(o)
        except Exception as e:
            print(f'   {t} の取得に失敗: {e}')
    if len(vals) >= 1:
        comp = 1 / sum(1 / o for o in vals)
        print(f'\n   合成オッズ = 1 ÷ ({" + ".join(f"1/{o:.1f}" for o in vals)}) = **{comp:.2f}倍**')
        if comp < 3.0:
            print('   → 【警告】3.0倍未満です。第13章の規律では買い目を削る必要があります。')
        else:
            print('   → 3.0倍以上。第13章の基準をクリアしています。')
            print(f'      期待値の目安: 主観的中率20%なら {comp*0.20:.2f} ／ 25%なら {comp*0.25:.2f}'
                  f'（1.2を超えているか確認してください）')


def parse_bet(s):
    """'3連複 1-3-8' や '単勝 9' を (type, [馬番]) に変換する"""
    s = s.strip()
    m = re.match(r'^(単勝|馬連|ワイド|馬単|3連複|３連複)\s*[:：]?\s*(.+)$', s)
    if not m:
        return None
    name = m.group(1).replace('３', '3')
    t = {v: k for k, v in TYPE_NAMES.items()}.get(name)
    nums = re.findall(r'\d+', m.group(2))
    if not t or not nums:
        return None
    return (t, [int(x) for x in nums])


def final_check():
    """直前チェック（Claudeが動かないときの手動代替）。
    当日のバイアスを実測し、続けて指定レースの実オッズと合成オッズを確認する。
    """
    print()
    print('  ============================================')
    print('   直前チェック（当日バイアス＋実オッズ）')
    print('  ============================================')
    date = ask_date()
    if not date:
        return
    print()
    print('  [1] 当日ここまでのトラックバイアスを実測します...')
    print('      （終了しているレースだけを取得します）')
    print()
    try:
        do_fetch(date, 'all')
        analyze_bias(date, 'all')
    except Exception as e:
        print(f'  !! バイアス実測に失敗しました: {e}')
        print('     オッズ確認は続行できます。')

    print()
    print('  ============================================')
    print('   [2] 買い目の実オッズを確認します')
    print('  ============================================')
    print('  race_id は予想ログのシート冒頭、またはGmailの下書きに記載されています。')
    print('  形式: 年(4) + 場コード(2) + 回(2) + 日(2) + R(2)')
    print('  場コード: 札幌01 函館02 福島03 新潟04 東京05 中山06 中京07 京都08 阪神09 小倉10')
    while True:
        print()
        rid = input('  race_idを入力（空Enterで終了）: ').strip()
        if not rid:
            break
        if not re.fullmatch(r'\d{12}', rid):
            print('  !! 12桁の数字で入力してください（例 202604020207）')
            continue
        bets = []
        print('  買い目を1行ずつ入力してEnter（例「単勝 1」「3連複 1-3-8」／空Enterで確定）')
        while True:
            s = input('    買い目: ').strip()
            if not s:
                break
            b = parse_bet(s)
            if b:
                bets.append(b)
            else:
                print('    !! 読み取れません。「単勝 1」「3連複 1-3-8」のように入力してください')
        show_odds(rid, bets)
    print('\n  終了しました。')


def ask_date(prompt='  日付を8桁で入力してEnter（例 20260726／空Enterで今日）: '):
    import datetime
    s = input(prompt).strip()
    if not s:
        s = datetime.date.today().strftime('%Y%m%d')
        print(f'  → 今日（{s}）で実行します')
    if not re.fullmatch(r'\d{8}', s):
        print('  !! 8桁の数字で入力してください（例 20260726）')
        return None
    return s


def menu():
    """バッチファイルから呼ばれる対話モード。コマンドを覚えなくても使えるようにする。"""
    print()
    print('  ============================================')
    print('   JRA トラックバイアス確認')
    print('  ============================================')
    print()
    date = ask_date()
    if not date:
        return
    print()
    print(f'  [1/2] {date} のレース結果を取得します...')
    print('        1レースずつ1.5秒あけて取得するため、3場開催だと2分ほどかかります。')
    print()
    do_fetch(date, 'all')
    print()
    print('  [2/2] トラックバイアスを判定します...')
    print()
    analyze_bias(date, 'all')
    print()
    print('  ============================================')
    print('   完了しました。上にスクロールすると全部読めます。')
    print('  ============================================')


def check():
    """初回の動作確認。データは保存せず、ページを正しく読めるかだけ見る。"""
    print()
    print('  ============================================')
    print('   jra_bias.py 動作確認')
    print('  ============================================')
    print()
    print(f'  Python {sys.version.split()[0]}')
    print()
    date = ask_date('  確認したい日付を8桁で入力してEnter（例 20260726／空Enterで今日）: ')
    if not date:
        return
    print()
    try:
        byvenue, held = race_list_status(date)
    except Exception as e:
        print(f'  !! netkeibaへの接続に失敗しました: {e}')
        if _is_blocked_by_proxy(e):
            print(BLOCKED_HINT.format(date=date))
        else:
            print('     インターネット接続を確認してください。')
        return
    if not byvenue:
        if held:
            print(NOT_IMPORTED_HINT.format(date=date, venues='・'.join(held)))
        else:
            print(f'  !! {date} は開催がないようです。別の日付で試してください。')
        return
    print(f'  {date} の開催: {"・".join(byvenue.keys())}')
    v = list(byvenue.keys())[0]
    print(f'  {v} で読み取りテストをします...')
    print()
    probe(date, v)
    print()
    print('  ============================================')
    print('   「認識した列位置」に 着順・通過・上り などが')
    print('   並んでいれば成功です。この画面をClaudeに貼り付けてください。')
    print('  ============================================')


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == 'menu':
        menu()
    elif cmd == 'check':
        check()
    elif cmd == 'final':
        final_check()
    elif cmd == 'odds' and len(sys.argv) >= 3:
        bets = [b for b in (parse_bet(a) for a in sys.argv[3:]) if b]
        show_odds(sys.argv[2], bets)
    elif cmd == 'fetch' and len(sys.argv) >= 4:
        do_fetch(sys.argv[2], sys.argv[3])
    elif cmd == 'selftest':
        sys.exit(selftest())
    elif cmd == 'ingest' and len(sys.argv) >= 4:
        do_ingest(sys.argv[2], sys.argv[3])
    elif cmd == 'urls' and len(sys.argv) >= 4:
        do_urls(sys.argv[2], sys.argv[3], *(sys.argv[4:6] or []))
    elif cmd == 'bias' and len(sys.argv) >= 4:
        analyze_bias(sys.argv[2], sys.argv[3])
    elif cmd == 'race' and len(sys.argv) >= 5:
        show_race(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == 'probe' and len(sys.argv) >= 4:
        probe(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
