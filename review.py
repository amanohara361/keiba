#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""週次レビュー（GitHub Actions から月曜に実行される）。

**数字の下ごしらえだけをする。文章は書かない。**

検証ノートの反省文は人（と Claude）が書くもので、機械が代わりに書くと
「それらしいが根拠のない総括」が毎週積み上がる。ここがやるのは、
検証ノートを書くときに手で数えていた部分だけ——着順との突き合わせ、
的中／不的中の分類、回収率、印の成績、しきい値カウンタ——である。

手で数えるのをやめる理由は実際にズレたからで、検証ノートには
「当初『6件』と記載していたが列挙は5レースで…正しくは5件」という
自己修正が残っている。数え間違いは反省の前提を壊す。

いちばん見たい数字は最後に置いた **仮想成績 vs 規律適用後** である。
「規律（第13章）を守ると本当に収支が良くなるのか」を毎週測り続ける。
守った結果として取り逃した上振れも同時に数え、10件たまったら基準値を
見直す——という運用（予想メソッド 第13章）を機械側で支える。

外部ライブラリは使わない。
"""

import argparse
import json
import logging
import os
import statistics
import sys
from datetime import date, timedelta

import bets
import nar as nar_module
import results as results_module

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))
REVIEW_DIR = os.path.join(ROOT, 'data', 'review')
RESULTS_DIR = os.path.join(ROOT, 'data', 'results')

# レースの分類。第13章の定義をそのまま使う。
HIT = '的中'
BET_MISS = '買い目構成ミス'      # 印は当たっていたのに買い目が拾えなかった
PREDICT_MISS = '予想ミス'        # 印そのものが外れた
SKIPPED = '見送り'               # 印だけ記録し購入しなかった（勝負度C など）
UNSETTLED = '結果未確定'

# しきい値。ここを超えたらメソッド側の見直しを検討する（第13章）。
COUNTER_THRESHOLD = 10

# (見出し, summarize() のキー, 到達時にすること)。render() とメールの両方が
# ここを見る。**片方だけ書き換えて対応が崩れる、という事故を構造的に防ぐ。**
THRESHOLD_COUNTERS = [
    ('減点・原則で切った馬の好走（3着以内の無印馬）', 'unmarked_good_runs',
     '原則が厳しすぎないかを検討する'),
    ('規律遵守により逃した上振れ（止めたが的中していた）', 'missed_upside',
     '基準値（合成3.0倍・期待値1.2）が厳しすぎないかを検討する'),
    ('◎一極集中で全滅（◎着外・他の印は3着以内）', 'honmei_wipeout',
     '**規律未実装の候補**。軸の分散を規律に足すかを検討する'),
]

# 終了コードの意味は check.py と揃える。
EXIT_OK = 0
EXIT_ERROR = 1             # 知らせられなかった（送信失敗・設定不備）
EXIT_NEEDS_ATTENTION = 3   # 結果が1件も取れなかった等、要確認だが通知はできた


# ----------------------------------------------------------------------
# 入力をそろえる
# ----------------------------------------------------------------------

def load_final_checks(day):
    """その日の検算記録から、レースごとの **最後の** 判定を取り出す。

    data/checks/YYYY-MM-DD.json は1日に何度も追記される（朝・12時・直前）。
    発注するかどうかを決めたのは最後の判定なので、それだけを採る。
    """
    path = os.path.join(bets.CHECKS_DIR, f'{day.isoformat()}.json')
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        history = json.load(f)

    latest = {}
    for entry in history:
        for race in entry.get('races', []):
            latest[str(race.get('race_id'))] = race
    return latest


def cached_result(race_id):
    path = os.path.join(RESULTS_DIR, f'{race_id}.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save_result(result):
    """確定結果はレースごとに保存する。

    着順は二度と変わらないので、一度取れば十分。取り直さないことで、
    レビューを何度流し直しても netkeiba への負荷が増えず、
    テストもネットワーク無しで実データを使える。
    """
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"{result['race_id']}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return path


def get_result(race_id, fetcher=None, use_cache=True, org='jra'):
    """確定結果を返す。まだ出ていなければ None。

    取得元は主催者で決まる。中央は netkeiba の結果ページ、地方は
    公式CSVの払戻金・出馬表。**着順と払戻の形は同じ**なので、
    その先の精算（results.settle）も集計も分岐しない。

    org は race_id からは判別できない（どちらも12桁）。買い目ファイルの
    org をそのまま渡すこと。
    """
    if use_cache:
        hit = cached_result(race_id)
        if hit:
            return hit
    if org == 'nar':
        result = nar_module.results_for(race_id)
    else:
        result = results_module.fetch(race_id, fetcher=fetcher)
    if result:
        save_result(result)
    return result


# ----------------------------------------------------------------------
# 1レースの評価
# ----------------------------------------------------------------------

def top_finishers(result, n=3):
    return [h for h in result['finishing_order'] if h['rank'] <= n]


def classify(race, settlement, result):
    """第13章の定義でレースを分類する。

    買い目構成ミス … 買い目は全外れだが、◎が3着以内 または 印が2頭以上3着以内。
                      「馬は見えていたのに買い方で落とした」局面。
    予想ミス       … それ以外の不的中。印そのものが違った。

    この2つを混ぜると反省の向きを間違える。買い方の問題を馬の見方の
    問題として反省しても、翌週また同じ買い方で落とす。
    """
    if not race.bets:
        return SKIPPED
    if settlement['hit']:
        return HIT

    top3 = top_finishers(result)
    honmei = race.horses_for('◎')
    honmei_in_top3 = any(h['umaban'] in honmei for h in top3)
    marked_in_top3 = sum(1 for h in top3 if h['umaban'] in race.marked_horses)

    if honmei_in_top3 or marked_in_top3 >= 2:
        return BET_MISS
    return PREDICT_MISS


def unmarked_good_runs(race, result):
    """減点・原則で切った馬が好走した件数（3着以内の無印馬）。

    第13章のカウンタその1。10件たまったら「原則そのものが厳しすぎないか」を
    検討する。個別のレースで悔しがるのではなく、件数で判断するための仕組み。
    """
    return [h for h in top_finishers(result) if race.mark_of(h['umaban']) is None]


def honmei_only_wipeout(race, settlement, result):
    """◎を軸に一極集中し、◎が飛んで全滅したか。

    2026-08-08のエルムSがこれだった。◎2が着外に沈み、○11が2着・△3が1着
    だったのに、買い目が全て◎絡みだったので何も残らなかった。
    現行の規律（lone_partner）はこの形を検知できない——相手は広げてあり、
    軸が1頭に集中していることを見ていないため。

    **単勝1点買いは数えない。** 買い目が1点しかないレースに
    「軸を分散すれば拾えた」は言いがかりで、第8章はむしろ
    「思考の集中と資金の持ち」を理由に単勝1点を選んでいる。
    軸の置き方を問えるのは、複数点に分けるという選択があった場合だけ。
    最初この条件を入れておらず、2026-08-09のUHB賞とレパードS
    （どちらも単勝1点）まで数えて3件と出た。本当は1件である。

    検証ノートの改訂候補として件数だけ数えておく。規律に足すかどうかは
    人が決めることなので、ここでは判定も警告もしない。
    """
    if len(race.bets) < 2 or settlement['hit']:
        return False
    honmei = set(race.horses_for('◎'))
    if not honmei:
        return False
    # 全ての買い目が◎絡み
    if not all(honmei & set(bet.horses) for bet in race.bets):
        return False
    top3 = {h['umaban'] for h in top_finishers(result)}
    if honmei & top3:
        return False          # ◎は走っている（それは買い目構成の問題）
    # ◎以外の印が3着以内に来ていた＝軸を変えれば拾えた
    others = race.marked_horses - honmei
    return bool(others & top3)


def review_race(race, result, check):
    """1レース分の評価をまとめる。結果が未確定なら分類だけ空にする。"""
    entry = {
        'race_id': race.race_id,
        'name': race.name,
        'venue': race.venue,
        'confidence': race.confidence,
        'marks': race.marks,
        'blocked': bool(check.get('blocked')) if check else False,
        'block_reasons': [
            f['message'] for f in (check or {}).get('findings', [])
            if f.get('severity') == 'BLOCK'
        ],
        'staked': 0,
        'returned': 0,
        'bets': [],
    }

    if not result:
        entry['category'] = UNSETTLED
        return entry

    settlement = results_module.settle(race, result)
    entry.update({
        'staked': settlement['staked'],
        'returned': settlement['returned'],
        'bets': settlement['bets'],
        'category': classify(race, settlement, result),
        'top3': [
            {
                'rank': h['rank'],
                'umaban': h['umaban'],
                'name': h.get('name', ''),
                'odds': h.get('odds'),
                'ninki': h.get('ninki'),
                'mark': race.mark_of(h['umaban']),
            }
            for h in top_finishers(result)
        ],
        'unmarked_good_runs': [h['umaban'] for h in unmarked_good_runs(race, result)],
        'honmei_wipeout': honmei_only_wipeout(race, settlement, result),
    })

    # 印の成績。◎が1着だったか、3着以内だったか。
    honmei = race.horses_for('◎')
    if honmei:
        ranks = {h['umaban']: h['rank'] for h in result['finishing_order']}
        best = min((ranks[h] for h in honmei if h in ranks), default=None)
        entry['honmei_rank'] = best

    return entry


# ----------------------------------------------------------------------
# 配当の偏り
# ----------------------------------------------------------------------

def payout_stats(rows):
    """的中したレースの配当分布。少数の高配当に引っ張られて回収率を
    過大評価していないかを見るための診断。**判定はしない、数字を出すだけ。**

    回収率は「上位N件の配当を除いても、賭け金は変えずに」計算する
    （その馬券を買ったこと自体は事実なので、賭け金は減らさない）。
    的中がN件に満たない場合は、あるだけ除く（0件ならROIは0%になる。
    それも正しい数字なので隠さない）。
    """
    hits = sorted((e['returned'] for e in rows if e['category'] == HIT), reverse=True)
    if not hits:
        return None
    staked = sum(e['staked'] for e in rows)
    returned = sum(hits)

    def roi_excluding(n):
        if not staked:
            return None
        excluded = sum(hits[:min(n, len(hits))])
        return (returned - excluded) / staked

    return {
        'count': len(hits),
        'median': statistics.median(hits),
        'min': hits[-1],
        'max': hits[0],
        'roi_excl_top1': roi_excluding(1),
        'roi_excl_top3': roi_excluding(3),
    }


# ----------------------------------------------------------------------
# 集計
# ----------------------------------------------------------------------

def summarize(entries):
    """レース評価の一覧から KPI を出す。

    収支は2通り出す。
      仮想成績   … 買い目をそのまま全部買った場合
      規律適用後 … 規律が止めた（BLOCK）レースを除いた場合
    この差が「規律を守る価値」そのものである。差がマイナスに振れ続ける
    なら基準値が厳しすぎるということなので、そのときは基準を見直す。
    """
    settled = [e for e in entries if e['category'] != UNSETTLED]
    purchased = [e for e in settled if e['staked'] > 0]
    kept = [e for e in purchased if not e['blocked']]
    blocked = [e for e in purchased if e['blocked']]

    def totals(rows):
        staked = sum(e['staked'] for e in rows)
        returned = sum(e['returned'] for e in rows)
        return {
            'races': len(rows),
            'staked': staked,
            'returned': returned,
            'profit': returned - staked,
            'roi': (returned / staked) if staked else None,
            'hits': sum(1 for e in rows if e['category'] == HIT),
        }

    honmei_ranked = [e['honmei_rank'] for e in settled if e.get('honmei_rank')]

    return {
        'races': len(entries),
        'settled': len(settled),
        'unsettled': sum(1 for e in entries if e['category'] == UNSETTLED),
        'skipped': sum(1 for e in settled if e['category'] == SKIPPED),
        'virtual': totals(purchased),
        'disciplined': totals(kept),
        'blocked': totals(blocked),
        # 規律適用後（実際に運用した場合）の的中に対する診断。
        # 「回収率（規律適用後）」が少数の高配当に引っ張られていないかを見る。
        'payout_stats': payout_stats(kept),
        'bet_miss': sum(1 for e in settled if e['category'] == BET_MISS),
        'predict_miss': sum(1 for e in settled if e['category'] == PREDICT_MISS),
        'honmei_races': len(honmei_ranked),
        'honmei_win': sum(1 for r in honmei_ranked if r == 1),
        'honmei_place': sum(1 for r in honmei_ranked if r <= 3),
        # しきい値カウンタ
        'unmarked_good_runs': sum(len(e.get('unmarked_good_runs', [])) for e in settled),
        'missed_upside': sum(1 for e in blocked if e['returned'] > e['staked']),
        'honmei_wipeout': sum(1 for e in settled if e.get('honmei_wipeout')),
    }


def collect(days, fetcher=None, use_cache=True):
    """指定した日付のレースを評価して、日ごとにまとめる。"""
    collected = []
    for day in days:
        sheet = bets.load_sheet(day)
        if not sheet:
            continue
        checks = load_final_checks(day)
        entries = []
        for race in sheet.races:
            try:
                result = get_result(race.race_id, fetcher=fetcher,
                                    use_cache=use_cache, org=race.org)
            except (results_module.ResultsError, nar_module.NarError) as exc:
                logger.warning('%s: %s', race.race_id, exc)
                result = None
            entries.append(review_race(race, result, checks.get(race.race_id)))
        collected.append({'date': day, 'source': sheet.source, 'entries': entries})
    return collected


def all_bet_days():
    """買い目ファイルがある日を古い順に返す（通算の集計に使う）。"""
    if not os.path.isdir(bets.BETS_DIR):
        return []
    days = []
    for name in os.listdir(bets.BETS_DIR):
        stem, ext = os.path.splitext(name)
        if ext != '.json':
            continue
        try:
            days.append(date.fromisoformat(stem))
        except ValueError:
            continue
    return sorted(days)


# ----------------------------------------------------------------------
# 出力
# ----------------------------------------------------------------------

def _yen(n):
    return f'{n:,}円'

def _pct(num, den):
    return f'{num}/{den}（{num / den * 100:.0f}%）' if den else '—'

def _roi(t):
    return f"{t['roi'] * 100:.0f}%" if t['roi'] is not None else '—'


def _payout_roi(pct):
    return f'{pct * 100:.0f}%' if pct is not None else '—'


# (見出し, payout_stats() の戻り値 -> 表示文字列)。None（的中0件）は "—"。
# render() の表と render_mail() の両方がここから作る。
PAYOUT_STAT_ROWS = [
    ('的中', lambda s: f"{s['count']}件" if s else '0件'),
    ('配当の中央値', lambda s: _yen(round(s['median'])) if s else '—'),
    ('配当（最小〜最大）', lambda s: f"{_yen(s['min'])}〜{_yen(s['max'])}" if s else '—'),
    ('回収率（上位1件除く）', lambda s: _payout_roi(s['roi_excl_top1']) if s else '—'),
    ('回収率（上位3件除く）', lambda s: _payout_roi(s['roi_excl_top3']) if s else '—'),
]


def _payout_stats_line(label, stats):
    if not stats:
        return f'{label}: 的中0件'
    return (f"{label}: 的中{stats['count']}件 "
            f"中央値{_yen(round(stats['median']))} "
            f"{_yen(stats['min'])}〜{_yen(stats['max'])} / "
            f"上位1件除く{_payout_roi(stats['roi_excl_top1'])} "
            f"上位3件除く{_payout_roi(stats['roi_excl_top3'])}")


def _totals_line(label, t):
    return (f"- {label}：{t['races']}レース 的中{t['hits']} "
            f"投資{_yen(t['staked'])} 払戻{_yen(t['returned'])} "
            f"収支{t['profit']:+,}円 回収率{_roi(t)}")


def render(collected, week_summary, total_summary, period):
    lines = []
    start, end = period
    lines.append(f'# 週次レビュー {start.isoformat()} 〜 {end.isoformat()}')
    lines.append('')
    lines.append('機械が数えた部分のみ。反省と改訂は人が書いて 検証ノート.md へ移す。')
    lines.append('')

    # 数字が空なのか、取りに行けなかったのかを取り違えないようにする。
    # 「0件でした」と静かに出すのがいちばん危ない出力なので必ず前に出す。
    if week_summary['unsettled']:
        lines.append(f"> **注意：{week_summary['unsettled']}レースの結果を取得できていない。**"
                     ' 集計はその分だけ欠けている。')
        lines.append('> netkeiba の構造変更か通信の失敗を疑い、実行ログを確認すること。')
        lines.append('')

    # --- いちばん見たい数字を先頭に置く ---
    lines.append('## 規律は収支に効いたか')
    lines.append('')
    lines.append(_totals_line('仮想成績（買い目どおり全部買った場合）', week_summary['virtual']))
    lines.append(_totals_line('規律適用後（BLOCKを除いた実運用）', week_summary['disciplined']))
    diff = week_summary['disciplined']['profit'] - week_summary['virtual']['profit']
    lines.append(f'- **差引：{diff:+,}円**（プラスなら規律が損を防いだ、マイナスなら上振れを逃した）')
    if week_summary['blocked']['races']:
        lines.append(_totals_line('  うち止めた分', week_summary['blocked']))
    lines.append('')

    # --- 配当の偏り ---
    # 「回収率（規律適用後）」が少数の高配当に引っ張られていないかを、
    # すぐ上の数字に対する注記として置く。判定はしない、数字だけ。
    lines.append('### 配当の偏り（規律適用後の的中に対して）')
    lines.append('')
    lines.append('| 指標 | 今週 | 通算 |')
    lines.append('|---|---|---|')
    for label, fn in PAYOUT_STAT_ROWS:
        lines.append(f'| {label} | {fn(week_summary["payout_stats"])} '
                     f'| {fn(total_summary["payout_stats"])} |')
    lines.append('')

    # --- レース別 ---
    lines.append('## レース別')
    lines.append('')
    lines.append('| 日付 | レース | 勝負度 | 判定 | 収支 | 1〜3着（印） |')
    lines.append('|---|---|---|---|---|---|')
    for day in collected:
        for e in day['entries']:
            top3 = ' / '.join(
                f"{h['rank']}着 {h['umaban']}{h['mark'] or '無'}"
                for h in e.get('top3', [])
            ) or '—'
            verdict = e['category'] + ('（発注停止）' if e['blocked'] else '')
            profit = f"{e['returned'] - e['staked']:+,}円" if e['staked'] else '—'
            lines.append(
                f"| {day['date'].isoformat()} | {e['name']} | {e['confidence'] or '未評価'} "
                f"| {verdict} | {profit} | {top3} |")
    lines.append('')

    # --- KPI ---
    lines.append('## KPI')
    lines.append('')
    lines.append('| 指標 | 今週 | 通算 |')
    lines.append('|---|---|---|')
    rows = [
        ('◎勝率', lambda s: _pct(s['honmei_win'], s['honmei_races'])),
        ('◎複勝率', lambda s: _pct(s['honmei_place'], s['honmei_races'])),
        ('回収率（規律適用後）', lambda s: _roi(s['disciplined'])),
        ('的中', lambda s: f"{s['disciplined']['hits']}/{s['disciplined']['races']}"),
        ('買い目構成ミス', lambda s: str(s['bet_miss'])),
        ('予想ミス', lambda s: str(s['predict_miss'])),
        ('見送り', lambda s: str(s['skipped'])),
    ]
    for label, fn in rows:
        lines.append(f'| {label} | {fn(week_summary)} | {fn(total_summary)} |')
    lines.append('')

    # --- しきい値カウンタ ---
    lines.append(f'## カウンタ（通算{COUNTER_THRESHOLD}件でメソッド見直しを検討）')
    lines.append('')
    for label, key, note in THRESHOLD_COUNTERS:
        count = total_summary[key]
        flag = ' ← **しきい値到達**' if count >= COUNTER_THRESHOLD else ''
        lines.append(f'- {label}：今週{week_summary[key]}件／通算{count}件{flag}')
        lines.append(f'  - 到達時にすること：{note}')
    lines.append('')

    # --- 人が書く欄 ---
    lines.append('## ここから先は人が書く')
    lines.append('')
    lines.append('- 買い目構成ミスがあった場合、どの規律なら防げたか')
    lines.append('- 予想ミスの原因（血統／展開／馬場／調教のどれを読み違えたか）')
    lines.append('- 検証ノート.md への転記（基準値は10件たまるまで変えない）')
    lines.append('')
    return '\n'.join(lines)


def _mail_race_lines(collected):
    """メールに入れるレース別の明細。

    **数字だけの要約では何が起きたのか分からない**（2026-08-24、
    「買い目構成ミス4」とだけ書かれても、どのレースでどの印が走って
    どう取りこぼしたのかが読めない、という指摘）。レース名・印・
    1〜3着に来た馬とその印・収支を1行ずつ並べ、メールだけで
    「馬は見えていたのに買い方で落とした」かどうかが分かるようにする。
    """
    lines = []
    for day in collected:
        for e in day['entries']:
            if e['category'] == UNSETTLED:
                continue
            marks = ' '.join(f"{m['mark']}{m['umaban']}" for m in e['marks']) or '印なし'
            top3 = ' / '.join(
                f"{h['rank']}着{h['umaban']}{h['mark'] or '無'}"
                for h in e.get('top3', [])
            ) or '着順不明'
            profit = f"{e['returned'] - e['staked']:+,}円" if e['staked'] else '見送り'
            head = f"[{e['category']}] {e['name']}"
            if e['blocked']:
                head += '（発注停止）'
            lines.append(f'  {head}　{profit}')
            lines.append(f'    印 {marks}　→　{top3}')
    return lines


def render_mail(week_summary, period, path, total_summary=None, collected=None):
    """メール本文。**しきい値に達したカウンタがあれば、ここで一言で言い切る。**

    通算の件数や「10件」という基準そのものは、レビューの .md ファイルには
    出ているがメールには出ていなかった。メールしか見ない運用だと、
    しきい値を超えても誰も気づかない。数字を読んで判断するのではなく、
    「見直しどき」かどうかだけを機械が言い切る形にする。

    **レース別の明細もメールに入れる**（2026-08-24 の指摘）。要約の数字だけでは
    「買い目構成ミス4」が何のことか分からず、詳細ファイルを開かないと
    判断できなかった。メール単体で意味が通るようにする。
    """
    start, end = period
    v, d = week_summary['virtual'], week_summary['disciplined']
    diff = d['profit'] - v['profit']
    lines = [
        f'週次レビュー {start.isoformat()} 〜 {end.isoformat()}',
        '',
    ]

    # **未確定があるうちは暫定値だと最初に言い切る。** 2026-08-24 は結果が
    # 12件未取得のまま「5R 収支-1,300円」というメールが出た。後で再取得した
    # 実際の数字は16R・-5,400円で、4倍以上ずれていた。数字の大小より先に
    # 「これは確定値ではない」と分かる必要がある。
    if week_summary['unsettled']:
        lines += [
            f"⚠ これは暫定値です（{week_summary['unsettled']}レースの結果が未取得）。",
            f"　 確定した集計は火曜 09:17 の再取得メール（件名に「再取得」）を見てください。",
            '',
        ]

    lines.append(f"対象 {week_summary['settled']}レース"
                 f"（うち購入 {v['races']}／見送り {week_summary['skipped']}"
                 f"／未確定 {week_summary['unsettled']}）")
    lines.append('')

    if total_summary:
        reached = [(label, note) for label, key, note in THRESHOLD_COUNTERS
                   if total_summary[key] >= COUNTER_THRESHOLD]
        if reached:
            lines.append(f'■ メソッド見直しの検討どきです（通算{COUNTER_THRESHOLD}件が目安）')
            for label, note in reached:
                # 何件たまっていて、到達したら何をするのかまで書く。
                # 見出しだけでは「で、どうすれば」が分からない。
                key = next(k for lbl, k, _ in THRESHOLD_COUNTERS if lbl == label)
                lines.append(f'  - {label}：通算{total_summary[key]}件')
                lines.append(f'    → {note.replace("**", "")}')
            lines.append('  （検証ノート.md へ転記して判断してください）')
            lines.append('')

    lines += [
        '── 収支 ' + '─' * 30,
        f"仮想成績   {v['races']}R 的中{v['hits']} 収支{v['profit']:+,}円 回収率{_roi(v)}"
        '　… 買い目どおり全部買った場合',
        f"規律適用後 {d['races']}R 的中{d['hits']} 収支{d['profit']:+,}円 回収率{_roi(d)}"
        '　… BLOCKを除いた実運用',
        f'差引 {diff:+,}円'
        '　… プラスなら規律が損を防いだ、マイナスなら上振れを逃した',
        '',
        _payout_stats_line('配当(規律適用後) 今週', week_summary['payout_stats']),
    ]
    if total_summary:
        lines.append(_payout_stats_line('配当(規律適用後) 通算', total_summary['payout_stats']))

    lines += [
        '',
        '── 印の精度 ' + '─' * 26,
        f"◎勝率 {_pct(week_summary['honmei_win'], week_summary['honmei_races'])}"
        f" / ◎複勝率 {_pct(week_summary['honmei_place'], week_summary['honmei_races'])}",
        f"買い目構成ミス {week_summary['bet_miss']}件"
        '　… 印は当たっていたのに買い方で取りこぼした',
        f"予想ミス {week_summary['predict_miss']}件"
        '　… 印そのものが外れた',
    ]

    if collected:
        race_lines = _mail_race_lines(collected)
        if race_lines:
            lines += ['', '── レース別 ' + '─' * 26] + race_lines

    lines += ['', f'詳細: {path}']
    return '\n'.join(lines)


def write_review(text, day):
    os.makedirs(REVIEW_DIR, exist_ok=True)
    path = os.path.join(REVIEW_DIR, f'{day.isoformat()}.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


# ----------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description='週次レビューを作る')
    parser.add_argument('--days', type=int, default=7, help='さかのぼる日数（既定7）')
    parser.add_argument('--end', help='集計の終わり（YYYY-MM-DD、既定は今日）')
    parser.add_argument('--mail', action='store_true', help='結果をメールする')
    parser.add_argument('--no-cache', action='store_true',
                        help='保存済みの結果を使わず取り直す')
    parser.add_argument('--subject-suffix', default='',
                        help='件名に付け足す文字列（例：再取得回だと分かるように）')
    args = parser.parse_args(argv)

    end = date.fromisoformat(args.end) if args.end else bets.now_jst().date()
    start = end - timedelta(days=args.days - 1)
    week_days = [start + timedelta(days=i) for i in range(args.days)]

    use_cache = not args.no_cache
    week = collect(week_days, use_cache=use_cache)
    if not week:
        logger.info('%s〜%s に買い目ファイルがありません', start, end)

    # 通算はこれまでの全日を対象にする。結果は保存済みなので取り直しは起きない。
    history_days = [d for d in all_bet_days() if d not in set(week_days)]
    history = collect(history_days, use_cache=True) + week

    week_entries = [e for day in week for e in day['entries']]
    all_entries = [e for day in history for e in day['entries']]

    week_summary = summarize(week_entries)
    total_summary = summarize(all_entries)

    text = render(week, week_summary, total_summary, (start, end))
    path = write_review(text, end)
    logger.info('レビューを書き出しました: %s', path)
    print(text)

    if args.mail:
        from mailer import Mailer
        mailer = Mailer()
        if not mailer.is_configured():
            logger.error('メールの認証情報が未設定です')
            return EXIT_ERROR
        subject = f'週次レビュー {start.isoformat()}〜{end.isoformat()}{args.subject_suffix}'
        rel = os.path.relpath(path, ROOT)
        body = render_mail(week_summary, (start, end), rel,
                           total_summary=total_summary, collected=week)
        if not mailer.send(subject, body):
            logger.error('メール送信に失敗しました')
            return EXIT_ERROR
        logger.info('メールを送信しました')

    # 対象レースはあるのに1件も確定結果が取れていない＝集計ではなく取得が壊れている。
    # 「今週は0件でした」という静かな出力で見過ごさないよう、色を変えて知らせる。
    if week_entries and week_summary['settled'] == 0:
        logger.warning('結果を1件も取得できませんでした。netkeiba への到達を確認してください。')
        return EXIT_NEEDS_ATTENTION

    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
