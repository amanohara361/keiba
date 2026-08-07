#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NAR公式CSV（地方競馬情報サイト データダウンロード機能）解析ツール
予想メソッド.md 第11章「実務手順（地方競馬・NAR公式CSVによる自動化）」の実装。

配置: E:\\Claude\\競馬予想\\data\\ に展開済みCSVを置く
  - YYYYMM_racelist.csv / YYYYMM_horselist.csv / YYYYMM_payback.csv
  - YYYYMM_0x_odds.csv
使用例:
  python nar_csv.py bias 20260726 川崎     # 1開催日のトラックバイアス判定
  python nar_csv.py race 20260726 川崎 11  # 1レースの脚質・ペース内訳
"""
import csv, re, sys, os, collections

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

# ばんえいはハロンタイム・上がり3Fが存在しないため対象外（予想メソッド第14章）
EXCLUDED_VENUES = {'帯広ば'}


# ---------- 基本ユーティリティ ----------

def load(name):
    """data/ 配下から YYYYMM_<name>.csv を全て読み込む（複数月に対応）"""
    rows = []
    for f in sorted(os.listdir(DATA_DIR)):
        if f.endswith(f'_{name}.csv'):
            with open(os.path.join(DATA_DIR, f), encoding='utf-8-sig') as fh:
                rows += list(csv.DictReader(fh))
    if not rows:
        raise SystemExit(f'{DATA_DIR} に *_{name}.csv が見つかりません')
    return rows


def rkey(r):
    return (r['競馬場'], r['競走年月日'], r['レース番号'])


def to_sec(t):
    """走破タイム 'MSSf' → 秒。例 '2043' → 124.3 / '1136' → 73.6"""
    t = (t or '').strip()
    if not t.isdigit():
        return None
    return int(t[:-3] or 0) * 60 + int(t[-3:-1]) + int(t[-1]) / 10


def parse_corner(v):
    """コーナー通過順を先頭からの馬番リストに変換。

    記法（実データで検証済み）:
      ','   通常の区切り（僅差）
      '(a,b)' 同位置・並走
      '-'   やや差がある
      '='   大きく離れている
    順序の解釈のみに使うため、区切り文字の種類は無視して数字を順に拾えばよい。
    """
    return [int(x) for x in re.findall(r'\d+', v or '')]


def leg_style(pos, n):
    """コーナー通過順位と出走頭数から脚質を判定"""
    if pos is None or not n:
        return '?'
    ratio = pos / n
    if pos == 1:
        return '逃'
    if ratio <= 0.35:
        return '先'
    if ratio <= 0.70:
        return '差'
    return '追'


# ---------- レース単位の解析 ----------

def build_races(date=None, venue=None):
    R = load('racelist')
    H = load('horselist')
    horses = collections.defaultdict(list)
    for h in H:
        horses[rkey(h)].append(h)

    out = []
    for r in R:
        if r['競馬場'] in EXCLUDED_VENUES:
            continue
        if date and r['競走年月日'] != date:
            continue
        if venue and r['競馬場'] != venue:
            continue
        hs = horses.get(rkey(r), [])
        fin = [h for h in hs if h['着順'].isdigit()]
        if not fin or not r['コーナー通過順1']:
            continue  # 未確定レース

        # 最終コーナー（存在する最後の通過順）を脚質判定に使う
        last = ''
        for i in range(8, 0, -1):
            if r[f'コーナー通過順{i}']:
                last = r[f'コーナー通過順{i}']
                break
        order = parse_corner(last)
        pos = {num: i + 1 for i, num in enumerate(order)}
        n = len(order)

        win = min(fin, key=lambda h: int(h['着順']))
        wt = to_sec(win['タイム'])
        ag3 = float(r['上がり3F']) if r['上がり3F'] else None
        dist = int(r['距離']) if r['距離'].isdigit() else None
        # 前半 = 走破タイム - 上がり3F。
        # ハロンタイムは南関4場＋園田にしか無く、かつ1本目が端数区間になるため、
        # 全場で成立するこの逆算方式を標準とする（けーばどっとこむの「前半(勝ち-上がり)」と同じ考え方）。
        first = round(wt - ag3, 1) if (wt is not None and ag3 is not None) else None
        first_dist = dist - 600 if dist else None
        # 前半の1ハロンあたり平均（距離が違うレース同士を比較するための正規化）
        first_pace = round(first / (first_dist / 200), 2) if first and first_dist and first_dist > 0 else None

        for h in fin:
            h['_pos'] = pos.get(int(h['馬番']))
            h['_leg'] = leg_style(h['_pos'], n)

        out.append({
            'key': rkey(r), '競馬場': r['競馬場'], '日付': r['競走年月日'], 'R': int(r['レース番号']),
            'レース名': r['レース名'], '距離': dist, '芝ダ': r['芝ダート区分'] or 'ダ',
            '馬場': r['馬場'], '天候': r['天候'], '頭数': n, '条件': r['条件'],
            '勝ちタイム': wt, '上がり3F': ag3, '前半': first, '前半距離': first_dist,
            '前半1F平均': first_pace, 'horses': sorted(fin, key=lambda x: int(x['着順'])),
        })
    return out


# ---------- 第11章の4ステップ ----------

def analyze_bias(date, venue):
    races = build_races(date, venue)
    if not races:
        raise SystemExit(f'{date} {venue} の確定レースが見つかりません')

    # 手順1: 同条件（同競馬場・同距離）の平均と比較してペースを判定
    allr = build_races()
    base = collections.defaultdict(list)
    for r in allr:
        if r['前半1F平均']:
            base[(r['競馬場'], r['距離'])].append(r['前半1F平均'])
    avg = {k: sum(v) / len(v) for k, v in base.items()}

    print(f'■ {date} {venue}  トラックバイアス判定（予想メソッド 第11章）')
    print(f'{"R":>3} {"距離":>5} {"馬場":<4} {"前半":>6} {"基準差":>7} {"ペース":<6} {"上3F":>5}  1〜3着の脚質      4〜8着の脚質')
    tally = collections.Counter()
    mid_tally = collections.Counter()
    pace_bucket = collections.defaultdict(list)   # ペース区分 -> 1〜3着の脚質リスト
    pace_races = collections.defaultdict(list)    # ペース区分 -> レース番号
    for r in sorted(races, key=lambda x: x['R']):
        a = avg.get((r['競馬場'], r['距離']))
        diff = round(r['前半1F平均'] - a, 2) if (a and r['前半1F平均']) else None
        # 前半が基準より速い（数値が小さい）＝ハイペース
        pace = '－'
        if diff is not None:
            pace = 'ハイ' if diff <= -0.15 else ('スロー' if diff >= 0.15 else '平均')
        top3 = [h['_leg'] for h in r['horses'][:3]]
        mid = [h['_leg'] for h in r['horses'][3:8]]
        tally.update(top3)
        mid_tally.update(mid)
        if pace in ('ハイ', '平均', 'スロー'):
            pace_bucket[pace] += top3
            pace_races[pace].append(r['R'])
        print(f"{r['R']:>3} {r['距離']:>5} {r['馬場']:<4} {r['前半'] or 0:>6.1f} "
              f"{(f'{diff:+.2f}' if diff is not None else '  -'):>7} {pace:<6} "
              f"{r['上がり3F'] or 0:>5.1f}  {'/'.join(top3):<16} {'/'.join(mid):<16}")

    print(f'\n手順2〜3（同日横比較）: 1〜3着の脚質分布 {dict(tally)}')
    print(f'手順4（着外4〜8着の位置取り）: {dict(mid_tally)}')

    front = tally['逃'] + tally['先']
    back = tally['差'] + tally['追']
    mfront = mid_tally['逃'] + mid_tally['先']
    mback = mid_tally['差'] + mid_tally['追']

    # 【重要】脚質の母集団は前後で50:50ではない。
    # leg_style の定義上、逃＝1頭・先＝〜35%・差＝35〜70%・追＝70%〜 となるため、
    # 出走馬全体では「前」が約35%、「後」が約65%を占める。素の数で前後を比べると
    # 必ず「後」が多くなり、誤った判定を招く。よって当日の全完走馬の脚質分布を
    # ベースラインとし、そこからの偏差（実測比率 ÷ 期待比率）で評価する。
    field = collections.Counter()
    for r in races:
        field.update(h['_leg'] for h in r['horses'])
    ffront = field['逃'] + field['先']
    fback = field['差'] + field['追']
    base_front = ffront / (ffront + fback) if (ffront + fback) else 0.35

    # さらに、地方競馬（ダート）は構造的に前有利であり（予想メソッド 第5章「砂かぶり」）、
    # 2026年7月の全1,030レースで計測した実測値は、母集団の前脚質31%に対し3着内は62%＝指数2.07。
    # つまり「指数1.0＝フラット」ではなく「指数2.07＝地方ダートの平常運転」が正しい基準。
    # そこで同競馬場の平常値を動的に算出し、そこからの乖離でバイアスを判定する。
    # なお 4〜8着は 1〜3着の裏返しであるため、1〜3着と同じ基準では必ず低く出る。
    # 着順帯ごとに別々の平常値を持たせる必要がある。
    venue = races[0]['競馬場']
    vt = vb = vm = vmb = vf = vfb = 0
    for r in allr:
        if r['競馬場'] != venue:
            continue
        for i, h in enumerate(r['horses']):
            isf = 1 if h['_leg'] in '逃先' else 0
            vf += isf; vfb += 1 - isf
            if i < 3:
                vt += isf; vb += 1 - isf
            elif i < 8:
                vm += isf; vmb += 1 - isf
    vbase = vf / (vf + vfb) if (vf + vfb) else 0.31
    norm = (vt / (vt + vb)) / vbase if (vt + vb) else 2.07          # 1〜3着の平常値
    norm_mid = (vm / (vm + vmb)) / vbase if (vm + vmb) else 1.0     # 4〜8着の平常値

    def idx(f, b):
        """前脚質の出現指数（母集団比率で正規化）。同競馬場の平常値 norm と比較して使う。"""
        tot = f + b
        return (f / tot) / base_front if tot and base_front else None

    def judge(i, n=None):
        """平常値からの乖離。>+15%で前有利、<-15%で差し有利"""
        if i is None:
            return None
        return i / (n if n is not None else norm)

    i_top = idx(front, back)
    i_mid = idx(mfront, mback)
    print(f'\n判定材料（母集団比率で正規化）:')
    print(f'  当日の全完走馬 前×後 = {ffront}×{fback}（前の母集団比率 {base_front*100:.0f}%）')
    vraces = sum(1 for r in allr if r['競馬場'] == venue)
    warn = '  ※サンプル少・参考値' if vraces < 60 else ''
    print(f'  {venue}の平常値（当データ全期間 {vraces}R）: 3着内 {norm:.2f} ／ 4〜8着 {norm_mid:.2f}{warn}')
    print(f'  　※基準は1.00ではありません（地方ダートは構造的に前有利）')
    print(f'  3着内   前×後 = {front}×{back}  → 指数 {i_top:.2f}（平常比 {judge(i_top)*100:.0f}%）')
    print(f'  4〜8着  前×後 = {mfront}×{mback}  → 指数 {i_mid:.2f}（平常比 {judge(i_mid, norm_mid)*100:.0f}%）')

    # 手順2の本質は「ペースから想定される脚質」と「実際の結果」のズレ。
    # ペースを見ずに脚質分布だけで判定すると、展開どおりの決着をバイアスと誤認する。
    print(f'\nペース別の3着内脚質（前脚質の出現指数。1.00＝母集団どおり）:')
    verdict = []
    for p in ['ハイ', '平均', 'スロー']:
        rs = pace_bucket[p]
        if not rs:
            continue
        f = sum(1 for x in rs if x in '逃先')
        b = sum(1 for x in rs if x in '差追')
        i = idx(f, b)
        j = judge(i)
        print(f'  {p:<4} 前{f:>3} × 後{b:>3}  指数 {i:.2f}（平常比 {j*100:.0f}%）  （{len(pace_races[p])}レース: {pace_races[p]}）')
        if len(pace_races[p]) < 2:
            continue
        i = j  # 以降は平常比で判定
        if p == 'ハイ' and i >= 1.15:
            verdict.append('★ハイペースなのに逃げ先行が上位＝強烈なイン前バイアスの可能性')
        elif p == 'スロー' and i <= 0.85:
            verdict.append('★スローペースなのに差し追込が上位＝強烈な外差しバイアスの可能性')
        elif p == 'スロー' and i >= 1.15:
            verdict.append('スローで前残り＝展開どおり（バイアスとは言えない）')
        elif p == 'ハイ' and i <= 0.85:
            verdict.append('ハイペースで差し決着＝展開どおり（バイアスとは言えない）')

    # 手順4: 4〜8着の脚質分布。3着内と同じ方向へ偏っていれば、展開ではなく馬場の影響が濃い。
    j_top, j_mid = judge(i_top), judge(i_mid, norm_mid)
    if j_mid is not None and j_top is not None:
        if j_mid >= 1.15 and j_top < 1.15:
            verdict.append('★4〜8着にも前々の馬が平常より多く残っている＝前有利バイアスの可能性。'
                           '3着内だけを見ると平常並みでも、手順4で初めて拾える偏り')
        elif j_mid <= 0.85 and j_top > 0.85:
            verdict.append('4〜8着に中団〜後方の馬が平常より多く押し寄せている＝'
                           '馬場が外伸びへ傾きつつある可能性（手順4）')

    if not verdict:
        verdict.append('顕著な矛盾なし。当日は脚質フラットと見るのが妥当')
    print('\n→ 判定:')
    for v in verdict:
        print(f'   ・{v}')
    print('\n※本判定は統計的な目安です。最終的な馬場判断は映像（第10章）と併せて行ってください。')
    print('※前半タイムは「勝ちタイム − 上がり3F」で算出（全場で成立する方式）。基準は同競馬場・同距離の当月平均。\n※地方ダートは構造的に前有利のため、指数は1.00ではなく各競馬場の平常値を基準に評価しています（第5章）。')


def show_race(date, venue, rno):
    races = [r for r in build_races(date, venue) if r['R'] == int(rno)]
    if not races:
        raise SystemExit('該当レースが見つかりません')
    r = races[0]
    print(f"■ {r['日付']} {r['競馬場']}{r['R']}R {r['レース名']}")
    print(f"  {r['芝ダ']}{r['距離']}m 天候{r['天候']} 馬場{r['馬場']} {r['頭数']}頭 / {r['条件']}")
    print(f"  勝ちタイム {r['勝ちタイム']:.1f}  前半{r['前半距離']}m {r['前半']:.1f}（1F平均{r['前半1F平均']}）  上がり3F {r['上がり3F']}")
    print(f"\n{'着':>3} {'馬番':>3} {'馬名':<14} {'脚質':<3} {'通過':>4} {'人気':>4} {'上3F':>5} {'馬体':>6} {'騎手':<8}")
    for h in r['horses']:
        print(f"{h['着順']:>3} {h['馬番']:>3} {h['馬名'][:14]:<14} {h['_leg']:<3} {h['_pos'] or 0:>4} "
              f"{h['人気'] or '-':>4} {h['上がり3F'] or '-':>5} "
              f"{(h['馬体重'] + (h['馬体重増減'] or '')):>6} {h['騎手名']:<8}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == 'bias':
        analyze_bias(sys.argv[2], sys.argv[3])
    elif cmd == 'race':
        show_race(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        print(__doc__)
