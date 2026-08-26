#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""印(marks)＋中穴候補(partners)＋実オッズから、買い目を機械的に組み立てる。

印を打つ判断には一切関与しない。ここが決めるのは「印が決まったあと、
どう買うか」だけ（第13章）。

## 2026-08-26 の改修（実測29件の検証にもとづく）

`data/bets` と `data/checks` に残る 2026-08-08〜08-26 の買い目29件（結果判明28件）を
実オッズと着順で突き合わせたところ、次が判明した。

  | 指標 | 値 |
  |---|---|
  | 主観的中率の平均（申告値） | 28.9% |
  | 市場推定的中率の平均 | 13.6% |
  | 実際の的中率 | 7.1%（2/28） |
  | 表示されていた期待値の平均 | 1.95 |
  | 実測の的中率で再計算した期待値 | 0.56 |

二項検定でも 28.9% は棄却される（28件中2件以下になる確率 0.53%）。市場推定 13.6%
は棄却されない。**市場の方が実測に近く、期待値の判定は機能していなかった。**
勝負度Aに至っては19件すべて不的中（0/19、主観が正しければ0件になる確率0.16%）。
期待値0.56は控除率（馬連・ワイド0.775／3連複0.75）すら下回る。

原因は3つあり、いずれも本モジュールの実装にあった。

**(1) 券種が変わっても同じ的中率を使っていた。**
    hit_rate = race.subjective_hit_rate   # レース単位で1つの数字
    ev = composite * hit_rate             # 全stagesで共通
ワイドは馬連より的中率が高いのに同じ値で評価されるため、オッズの高い馬連が
必ず有利に出る。実測でも29件中20件が馬連だった。

**(2) 上から順に試して最初に規律を満たした案を採っていた。**
最初の2段が馬連だったので、構造上ほぼ必ず馬連で決まった。同じレースで馬連の
期待値1.25、ワイドの期待値1.60でも馬連が選ばれる。「規律を満たす最初の案」で
あって「最良の案」ではなかった。

**(3) 3連複が候補に無かった。**
第13章「相手の広げ方」は3連複の組み方を明示的に規定しているのに実装に存在
しなかった。馬連は1着2着を当てなければ0円で、3着に来ても0円である。検証ノートの
「印から2頭以上が3着内なのに0円」6鞍連続・買い目構成ミス率48%は、3着を拾える
券種が選ばれない設計から出た症状だった。

### 直した内容

- 馬番ごとの主観勝率（`RaceBets.win_probabilities`）を Harville モデルに通し、
  **券種ごとに**的中率を算出する。
- 全候補を評価してから、規律（合成3.0倍・期待値1.2）を満たすもののうち
  **的中率が最大**のものを選ぶ。期待値最大にしないのは、主観勝率が過大な状態では
  少点数・高オッズの案ほど期待値が膨らみ、見積り誤差を最も増幅するため（下記参照）。
- 3連複を候補に加える（セット全体が印馬だけにならない構成のみ）。

### 触っていないもの（方針変更にあたるため定期レビューの承認まで変えない）

合成オッズ3.0倍・期待値1.2の基準値（第13章）、期待値1.5以上を勝負度Aとする線
（2026-08-14 ユーザー承認）、承認待ちの「主観勝率の乖離上限1.3倍」案。

### win_probabilities が無い買い目について

市場勝率だけで評価する（主観補正なし）。期待値は控除率前後にしかならないため、
実質的にすべて見送りになる。**これは意図した挙動である。** 旧 subjective_hit_rate
へフォールバックすると、上記の検証で棄却された数字をそのまま使うことになる。
朝タスクは win_probabilities を出すこと。
"""

import itertools

import discipline
from bets import Bet

STRONG_EXPECTED_VALUE = 1.5  # これ以上なら勝負度A（2026-08-14 ユーザー承認）

# 券種ごとの払戻率。主観と市場の乖離を見るための検算に使う。
PAYOUT_RATE = {'単勝': 0.80, '複勝': 0.80, '馬連': 0.775,
               'ワイド': 0.775, '馬単': 0.75, '3連複': 0.75}

# 警告のしきい値。採否は変えない（基準値の追加は方針変更にあたるため）。
LOW_HIT_RATE = 0.05
WIDE_DIVERGENCE = 1.8

# 朝タスクが win_probabilities を書かなかったレースに付ける印。
# **規律を満たさない見送りと、入力が欠けている見送りは別物である。**
# 2026-08-26、地方の朝タスクが win_probabilities を書かずに push し、
# 直前検算は「基準を満たさないので見送り」という規律どおりの文言だけを出した。
# 人にもレポートにも「入力が欠けている」と伝わらず、正常な見送りに見えた。
# check.py はこの文字列でレポートの先頭に警告を立てる。
MISSING_INPUT = '【入力欠落】'


# ----------------------------------------------------------------------
# 勝率モデル
# ----------------------------------------------------------------------

def market_win_probabilities(win_odds):
    """単勝オッズ {馬番: オッズ} から市場推定勝率を出す（控除率を除いて正規化）。"""
    raw = {k: 1.0 / v for k, v in win_odds.items() if v and v > 0}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()} if total > 0 else {}


def apply_subjective(market, overrides):
    """市場勝率に主観の上書きを適用し、残りの馬で辻褄を合わせる。"""
    if not overrides:
        return dict(market)
    known = {k: v for k, v in overrides.items() if k in market}
    used = sum(known.values())
    if used >= 1.0:
        return dict(market)
    out = dict(market)
    rest = [k for k in market if k not in known]
    rest_total = sum(market[k] for k in rest)
    out.update(known)
    if rest_total > 0:
        for k in rest:
            out[k] = market[k] * (1.0 - used) / rest_total
    return out


def p_trio(p, trio):
    """3頭が上位3着を占める確率（Harville）。"""
    total = 0.0
    for a, b, c in itertools.permutations(trio):
        da, db = 1.0 - p[a], 1.0 - p[a] - p[b]
        if da > 0 and db > 0:
            total += p[a] * p[b] / da * p[c] / db
    return total


def p_quinella(p, pair):
    """2頭が1着2着を占める確率（順不同）。"""
    a, b = pair
    out = 0.0
    if 1.0 - p[a] > 0:
        out += p[a] * p[b] / (1.0 - p[a])
    if 1.0 - p[b] > 0:
        out += p[b] * p[a] / (1.0 - p[b])
    return out


def p_wide(p, pair):
    """2頭がともに3着以内に入る確率。"""
    a, b = pair
    return sum(p_trio(p, (a, b, c)) for c in p if c not in pair)


def p_wide_group(p, axis, subset):
    """軸＋相手複数頭のワイド流し全体の的中率（軸と、相手のうち少なくとも
    1頭が、ともに3着以内）。

    相手ごとの p_wide を単純合算すると、相手が2頭以上同時に3着以内へ
    来るケースを二重・三重に数えてしまう（2026-08-26、実測とモンテカルロ
    検算で確認：軸+相手3頭流しで単純合算1.04〔確率が1を超える〕・
    モンテカルロ真値0.71、約1.47倍の過大評価だった）。軸以外の全馬から、
    相手を1頭以上含む3頭の組を漏れなく数え上げて正しく求める。
    相手が1頭のときは p_wide と完全に一致する（後方互換）。
    """
    others = [h for h in p if h != axis]
    total = 0.0
    for x, y in itertools.combinations(others, 2):
        if x in subset or y in subset:
            total += p_trio(p, (axis, x, y))
    return total


# ----------------------------------------------------------------------
# 候補
# ----------------------------------------------------------------------

class Candidate:
    def __init__(self, bet_type, combos, odds, hit_rate, note=''):
        self.bet_type = bet_type
        self.combos = combos
        self.odds = odds
        self.hit_rate = hit_rate
        self.note = note
        self.composite = discipline.composite_odds(odds)
        self.ev = (self.composite * hit_rate) if self.composite else None
        self.points = len(combos)

    @property
    def market_rate(self):
        if not self.composite:
            return None
        return PAYOUT_RATE.get(self.bet_type, 0.775) / self.composite

    def clears(self):
        return (self.composite is not None
                and self.composite >= discipline.MIN_COMPOSITE_ODDS
                and self.ev is not None
                and self.ev >= discipline.MIN_EXPECTED_VALUE)

    def warnings(self):
        """採否は変えないが、人間が見るべき点を挙げる。"""
        out = []
        if self.hit_rate is not None and self.hit_rate < LOW_HIT_RATE:
            out.append(f'的中率{self.hit_rate * 100:.2f}%と低く、'
                       f'期待値は高配当1点に依存しています')
        mr = self.market_rate
        if mr and self.hit_rate and self.hit_rate / mr >= WIDE_DIVERGENCE:
            out.append(f'主観的中率が市場推定の{self.hit_rate / mr:.1f}倍です。'
                       f'この見積りが外れると期待値の根拠も崩れます')
        return out

    def label(self):
        body = ' / '.join('-'.join(str(n) for n in c) for c in self.combos)
        return f'{self.bet_type} {body}'

    def to_bets(self, stake=100):
        return [Bet(self.bet_type, c, stake) for c in self.combos]


def _build_candidates(axis, pool, marked, p, lookup):
    out = []

    def priced(bet_type, combos):
        vals = [lookup(bet_type, c) for c in combos]
        # 相手の誰か1頭でもオッズが引けなければこの案は使わない。
        # 一部だけで組むと、引けなかった馬（それが◎○かもしれない）を無言で
        # 相手から落とすことになる（実オッズ優先の原則・第13章）。
        return None if any(v is None or v <= 0 for v in vals) else vals

    o = priced('単勝', [[axis]])
    if o:
        out.append(Candidate('単勝', [[axis]], o, p[axis], '◎の単勝1点'))

    for n in range(len(pool), 0, -1):
        subset = pool[:n]
        combos = [[axis, q] for q in subset]

        # 馬連は特定の1組しか的中しない（同時に2組が当たることはない）ため、
        # 相手ごとの確率をそのまま合算してよい。
        vals = priced('馬連', combos)
        if vals:
            rate = sum(p_quinella(p, (axis, q)) for q in subset)
            out.append(Candidate('馬連', combos, vals, rate, f'◎{axis}軸-相手{n}頭'))

        # ワイドは相手が2頭以上同時に3着以内へ来ると複数組が同時に的中しうる
        # ため、単純合算ではなく p_wide_group で正しい的中率を求める。
        vals = priced('ワイド', combos)
        if vals:
            rate = p_wide_group(p, axis, subset)
            out.append(Candidate('ワイド', combos, vals, rate, f'◎{axis}軸-相手{n}頭'))

    # 第13章：セット全体が印馬だけになる3連複は作らない。原文の理由が
    # 「無印馬が1頭でも絡んだ時点で全点が消滅する」なので、判定はセット単位。
    def has_dark(combos):
        return any(any(n not in marked for n in c) for c in combos)

    for pair in itertools.combinations(pool, 2):
        combo = sorted([axis, *pair])
        if not has_dark([combo]):
            continue
        vals = priced('3連複', [combo])
        if vals:
            out.append(Candidate('3連複', [combo], vals, p_trio(p, combo),
                                 f'◎{axis}軸-{pair[0]}/{pair[1]}'))

    if len(pool) >= 3:
        second, rest = pool[0], pool[1:]
        for n in range(len(rest), 1, -1):
            combos = [sorted([axis, second, q]) for q in rest[:n]]
            if not has_dark(combos):
                continue
            vals = priced('3連複', combos)
            if vals:
                rate = sum(p_trio(p, c) for c in combos)
                out.append(Candidate('3連複', combos, vals, rate,
                                     f'◎{axis}＋{second}の2頭軸-相手{n}頭'))
    return out


def build_bets(race, lookup, win_odds=None, stake=100):
    """1レース分の買い目を組み立てる。

    race:     marks・partners・win_probabilities を持つ RaceBets。
              race.bets は使わない（呼び出し前の値に関わらず組み直す）。
    lookup:   (bet_type, [umaban, ...]) -> オッズ or None。
    win_odds: {馬番: 単勝オッズ}。Harville に渡す勝率分布を作るのに使う。
    戻り値:   (勝負度, [Bet, ...], 説明文)
    """
    axis_horses = race.horses_for('◎')
    if not axis_horses:
        return 'C', [], '◎が無いため買い目を組めません'
    axis = axis_horses[0]

    pool = race.partner_pool
    if not pool:
        return 'C', [], '相手候補（印馬・中穴候補）が無いため買い目を組めません'

    market = market_win_probabilities(win_odds or {})
    if not market or axis not in market:
        return 'C', [], ('単勝オッズが揃わず勝率を推定できません'
                         '（要・時間を置いて再検算）')

    # **軸の単勝オッズに上限をかける（2026-08-26 ユーザー承認）。**
    # 第8章の推奨レンジ（4.0〜9.9倍）は discipline.py に定数として
    # あったが、`bet.type != '単勝'` で弾かれるため単勝券にしか効かず、
    # 軸馬の資格には一切適用されていなかった。
    #
    # 実データ45レースの検算で、この穴が実害を出していると分かった。
    #   ◎の単勝  〜3.9倍   9件 ◎複勝率55.6%  うち購入56%
    #   ◎の単勝 4.0〜9.9倍 15件 ◎複勝率66.7%  うち購入53%
    #   ◎の単勝 10.0倍〜    5件 ◎複勝率 0.0%  うち購入100%
    # 10倍超の◎は1頭も3着に入らなかったのに、その全部が購入されていた。
    # 合成オッズ3.0倍の下限が「◎が強い＝オッズが短いレース」を弾き、
    # 人気薄の◎を軸にしたレースばかり通すという逆選択が起きていたため。
    # 実際、見送りになった18鞍のうち14鞍で◎が3着以内に来ている。
    #
    # ここで入れるのは**上限だけ**。下限（3.9倍以下を弾く）は基準値の
    # 変更にあたり、決定ログの「観察期間30レース」の縛りに触れるため、
    # 保留中の検討事項として分けてある（docs/決定ログ.md）。
    axis_odds = win_odds.get(axis) if win_odds else None
    if axis_odds and axis_odds > discipline.WIN_ODDS_MAX:
        return 'C', [], (
            f'◎{axis}の単勝が{axis_odds:.1f}倍で第8章の推奨レンジ上限'
            f'（{discipline.WIN_ODDS_MAX}倍）を超えています。'
            f'勝ち切る確率が一段落ちる帯なので軸に据えません。見送り（勝負度C）。')

    # 出走表に無い馬番の指定は無視する（取消・入力ミス）。全部無効なら
    # 「指定なし」と同じ扱いにして、説明文でもそう伝える。
    overrides = {k: v for k, v in race.win_probabilities.items() if k in market}
    p = apply_subjective(market, overrides)
    pool = [q for q in pool if q in p]
    if not pool:
        return 'C', [], '相手候補の単勝オッズが取得できません'

    candidates = _build_candidates(axis, pool, set(race.marked_horses), p, lookup)
    if not candidates:
        return 'C', [], '実オッズが揃わず買い目を組めませんでした（要・再検算）'

    ok = [c for c in candidates if c.clears()]
    if not ok:
        if not overrides:
            # 規律の話にしない。**朝タスクの入力が欠けている**と言い切る。
            return 'C', [], (
                f'{MISSING_INPUT}このレースには win_probabilities（馬番ごとの'
                '主観勝率）がありません。市場勝率だけで評価したため、期待値が'
                '控除率を超えず見送り（勝負度C）になりました。**これは規律の'
                '判断ではなく、朝タスクの書き漏らしです。**書き方は'
                ' docs/朝タスク手順.md「主観勝率の書き方」。')
        return 'C', [], (f'第13章の基準（合成オッズ{discipline.MIN_COMPOSITE_ODDS}倍・'
                         f'期待値{discipline.MIN_EXPECTED_VALUE}）を満たす買い目を'
                         f'組めませんでした。見送り（勝負度C）。')

    # **規律を満たす候補のうち、期待値ではなく的中率が最大のものを選ぶ。**
    #
    # 期待値最大化は「主観勝率が正確なら」正しい。しかし2026-08-26の実測検証で
    # 主観勝率は市場の2.13倍に膨らんでいたと判明している。過大な主観勝率のもとでは、
    # 点数が少なくオッズが高い買い目ほど期待値が大きく膨らむため、期待値最大化は
    # 見積り誤差を最も増幅する選び方になる。実際、期待値で選ぶと「相手1頭の馬連」が
    # 頻繁に選ばれ、第13章が「2着抜けで全滅する構造」と警告する形に寄っていた。
    #
    # 期待値1.2以上という規律を満たすことを最低条件とし、そのうえで最も堅い
    # （的中率が高い）案を採る。合成オッズ3.0倍の下限が点数の増やしすぎを抑える。
    best = max(ok, key=lambda c: (c.hit_rate, c.ev))
    confidence = 'A' if best.ev >= STRONG_EXPECTED_VALUE else 'B'
    note = (f'{best.label()}（合成{best.composite:.2f}倍・'
            f'主観的中率{best.hit_rate * 100:.2f}%・期待値{best.ev:.2f}）')
    if not overrides:
        note = (f'{MISSING_INPUT}win_probabilities が無く市場勝率のみで評価しています'
                f'（朝タスクの書き漏らし）。' + note)
    for w in best.warnings():
        note += f' ※{w}'

    # **算出した的中率を race に書き戻す。** discipline は期待値を
    # race.subjective_hit_rate から独立に再計算するため、書き戻さないと
    # bet_builder の表示（券種別の的中率）と discipline の表示（申告値）が
    # 食い違う。書き戻せば両者が一致し、さらに data/bets に保存される値も
    # 「実際に使った的中率」になるので、後日の検証で申告値と実測を突き合わせ
    # られるようになる（2026-08-26 の検証はこれが無くて苦労した）。
    race.subjective_hit_rate = round(best.hit_rate, 4)
    return confidence, best.to_bets(stake), note
