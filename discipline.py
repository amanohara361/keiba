#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""予想メソッド 第13章「買い目の規律」の機械適用。

検証ノートによれば、運用4週間で ◎の複勝率は62.5%（直近2週は7鞍中4鞍が1着）に
達している一方、全体の回収率は48.4%にとどまり、「◎が3着以内なのに買い目が
全不的中」が5件発生している。検証ノート自身の結論は

    外れの原因は馬選びではなく買い目設計に偏在している

であり、その買い目設計は算術と規則で書ける。本モジュールがその部分を担う。
印を打つ判断には一切関与しない。

各チェックが対応する実際の損失:
  matched_marks   … 2026-08-02 クイーンS（○を外し▲を残して回収0円。馬連7-11=1,120円）
  win_odds_range  … 2026-08-02 アイビスSD（確定3.8倍でレンジ外・期待値1.10）
  marked_only     … 2026-07-25 関ケ原S、2026-07-26 関屋記念、2026-07-19 函館2歳S
  lone_partner    … 2026-07-26 東海S（相手を○1頭に固定し2着抜けで全滅）
  already_started … 2026-08-01（◎2鞍とも1着だが配信が発走後で購入不可）
"""

from bets import JUNIOR_MARKS, SENIOR_MARKS

# 第13章の基準値。ここは検証で緩和が承認されるまで変更しない。
MIN_COMPOSITE_ODDS = 3.0
MIN_EXPECTED_VALUE = 1.2

# 第8章の単勝推奨レンジ（中心ゾーン）
WIN_ODDS_MIN = 4.0
WIN_ODDS_MAX = 9.9

# 重大度
BLOCK = 'BLOCK'   # 発注しない（メソッドが「発注しない」「組み直す」と定めるもの）
WARN = 'WARN'     # 要検討（メソッドが「原則避ける」「検討する」と定めるもの）
INFO = 'INFO'


class Finding:
    def __init__(self, severity, code, message, remedy=''):
        self.severity = severity
        self.code = code
        self.message = message
        self.remedy = remedy

    def __repr__(self):
        return f'[{self.severity}] {self.message}'

    def to_dict(self):
        return {
            'severity': self.severity,
            'code': self.code,
            'message': self.message,
            'remedy': self.remedy,
        }


def composite_odds(odds_list):
    """合成オッズ = 1 ÷ Σ(1/各買い目のオッズ)。

    第13章の定義どおり。各点が同額であることを前提にした概算である。
    オッズが取れなかった点（None）は計算から除く。
    """
    usable = [o for o in odds_list if o and o > 0]
    if not usable:
        return None
    return 1.0 / sum(1.0 / o for o in usable)


# ----------------------------------------------------------------------
# 個別のチェック
# ----------------------------------------------------------------------

def check_already_started(race, now, day):
    """発走時刻を過ぎたレースの買い目は出さない（CLAUDE.md ステップ0）。

    2026-08-14〜、発走済みのときは race.confidence の有無で2通りに分ける。
    **None（直前検算が一度も評価していない）なら真の見逃し**（2026-08-01の
    事故と同じ形）としてBLOCKする。**A/B/C（評価済み。Cで見送った場合を
    含む）なら、直近の検算結果がそのまま最終形**であり、この実行が単に
    間に合わなかっただけの無害な再確認なので、BLOCKにしない。
    「発注を止めました」という言い回しは、本当に何も届かなかった前者にだけ使う。
    """
    start = race.start_datetime(day)
    if start is None:
        return Finding(
            WARN, 'no_start_time',
            f'{race.name}: 発走時刻が買い目ファイルに入っていません',
            '発走済みかどうか判定できません。start_time を入れてください。',
        )
    if now >= start:
        if race.confidence is None:
            return Finding(
                BLOCK, 'already_started_unevaluated',
                f'{race.name}: 発走までに一度も評価できませんでした'
                f'（発走 {race.start_time} / 現在 {now:%H:%M}）',
                '買い目が一度も確定しないまま発走しました。'
                '直前検算の実行間隔・遅延を確認してください。',
            )
        return Finding(
            INFO, 'already_started',
            f'{race.name}: 既に発走しています（発走 {race.start_time} / 現在 {now:%H:%M}）。'
            f'買い目は直近の検算結果のとおりです（勝負度{race.confidence}）。',
        )
    return None


def check_composite_odds(race, value):
    """合成オッズ3.0倍未満なら買い目を削る（第13章）。"""
    if value is None:
        return Finding(
            WARN, 'no_odds',
            f'{race.name}: 実オッズが取得できず合成オッズを計算できません',
            '「暫定」として扱い、点数を1段階絞ってください（第13章 実オッズ優先の原則）。',
        )
    if value < MIN_COMPOSITE_ODDS:
        return Finding(
            BLOCK, 'composite_below_minimum',
            f'{race.name}: 合成オッズ {value:.2f}倍 < {MIN_COMPOSITE_ODDS}倍',
            '点数を減らす・券種を絞る・重複した保険を外す。当たっても負ける構造です。',
        )
    return Finding(
        INFO, 'composite_ok',
        f'{race.name}: 合成オッズ {value:.2f}倍（基準 {MIN_COMPOSITE_ODDS}倍をクリア）',
    )


def check_expected_value(race, value):
    """合成オッズ × 主観的中率 > 1.2 を満たさなければ組み直す（第13章）。"""
    rate = race.subjective_hit_rate
    if value is None or rate is None:
        return Finding(
            WARN, 'no_expected_value',
            f'{race.name}: 期待値を計算できません'
            f'（合成オッズ{"あり" if value else "なし"} / 主観的中率{"あり" if rate else "なし"}）',
            '主観的中率を買い目ファイルに入れてください。',
        )
    expected = value * rate
    if expected < MIN_EXPECTED_VALUE:
        return Finding(
            BLOCK, 'expected_value_below_minimum',
            f'{race.name}: 期待値 {expected:.2f} < {MIN_EXPECTED_VALUE}'
            f'（合成 {value:.2f}倍 × 主観的中率 {rate:.0%}）',
            '買い目セットを組み直してください。'
            'なお期待値が印と食い違うときは、まず主観的中率の見積りを疑うこと（第13章）。',
        )
    return Finding(
        INFO, 'expected_value_ok',
        f'{race.name}: 期待値 {expected:.2f}（合成 {value:.2f}倍 × 主観的中率 {rate:.0%}）',
    )


def check_matched_marks(race):
    """第13章F案：上位印（◎○）が相手から外れ、下位印（▲△）が残る買い目は発注しない。

    2026-08-02 クイーンSの再発防止。印3頭が1〜3着を独占しながら、
    ○11を「期待値1.01で基準未達」として棄却し▲2（8着）を残したため回収0円になった。
    """
    in_bets = race.horses_in_bets

    missing_senior = [
        (mark, umaban)
        for mark in SENIOR_MARKS
        for umaban in race.horses_for(mark)
        if umaban not in in_bets
    ]
    present_junior = [
        (mark, umaban)
        for mark in JUNIOR_MARKS
        for umaban in race.horses_for(mark)
        if umaban in in_bets
    ]

    if missing_senior and present_junior:
        missing = '・'.join(f'{m}{n}番' for m, n in missing_senior)
        present = '・'.join(f'{m}{n}番' for m, n in present_junior)
        return Finding(
            BLOCK, 'mark_contradiction',
            f'{race.name}: 上位印 {missing} が買い目に無いのに、下位印 {present} が入っています',
            '数値基準は買い目を削る道具であって、印の序列を上書きする根拠ではありません。'
            '(1)相手を◎○に絞り点数を減らす (2)◎が堅く2着が絞れないならワイドにする '
            '(3)どれも無理なら見送る（勝負度Cへ降格）。',
        )
    return None


def check_marked_only_trifecta(race):
    """印馬だけで組む3連複は無印馬1頭で全滅する（第13章 相手の広げ方）。"""
    findings = []
    for bet in race.bets:
        if bet.type != '3連複':
            continue
        if all(h in race.marked_horses for h in bet.horses):
            findings.append(Finding(
                WARN, 'marked_only_trifecta',
                f'{race.name}: 3連複 {bet.combination} が印馬だけで組まれています',
                '印馬が3着以内を独占する前提に依存します。'
                '◎（＋1頭）を軸に、相手へ印を打たなかった中穴を最低1頭含めてください。',
            ))
    return findings


def check_lone_partner(race):
    """相手1頭固定の馬連・馬単は2着抜けで全滅する（第13章）。

    2026-07-26 東海Sの再発防止。
    """
    honmei = set(race.horses_for('◎'))
    partners = set()
    has_pair_bet = False
    has_hedge = False

    for bet in race.bets:
        if bet.type in ('ワイド', '複勝') and (honmei & set(bet.horses)):
            # 第13章が指示する保険（◎絡みのワイド・複勝）が既にある
            has_hedge = True
            continue
        if bet.type not in ('馬連', '馬単'):
            continue
        has_pair_bet = True
        partners.update(h for h in bet.horses if h not in honmei)

    if has_pair_bet and not has_hedge and len(partners) == 1:
        only = partners.pop()
        return Finding(
            WARN, 'lone_partner',
            f'{race.name}: 馬連・馬単の相手が {only}番の1頭だけです',
            '2着抜けで全滅する構造です。◎絡みのワイドまたは複勝を保険に置けないか検討してください。',
        )
    return None


def check_win_odds_range(race, win_table):
    """単勝は4.0〜9.9倍を中心ゾーンとする（第8章）。

    2026-08-02 アイビスSDの再発防止。朝4.3倍だった◎が確定3.8倍まで売れ、
    レンジ下限を割って期待値1.10になっていた。
    """
    findings = []
    for bet in race.bets:
        if bet.type != '単勝':
            continue
        number = bet.horses[0]
        entry = win_table.get(number)
        if not entry:
            continue
        odds, _ = entry
        if odds < WIN_ODDS_MIN:
            findings.append(Finding(
                WARN, 'win_odds_below_range',
                f'{race.name}: 単勝{number}番が {odds:.1f}倍で推奨レンジ'
                f'（{WIN_ODDS_MIN}〜{WIN_ODDS_MAX}倍）の下限割れ',
                '売れてレンジ外に出た馬は単勝対象から外すのが原則です。'
                '条件好転の根拠が強い場合のみ3倍台も許容されます（第8章）。',
            ))
        elif odds > WIN_ODDS_MAX:
            findings.append(Finding(
                WARN, 'win_odds_above_range',
                f'{race.name}: 単勝{number}番が {odds:.1f}倍で推奨レンジ'
                f'（{WIN_ODDS_MIN}〜{WIN_ODDS_MAX}倍）の上限超え',
                '勝ち切る確率が一段落ちる帯です。10倍台前半までは許容範囲（第8章）。',
            ))
    return findings


def check_going(race, conditions):
    """馬場が渋っていたら知らせる。**どの馬を上げ下げするかは判断しない。**

    朝の予想は馬場状態が未発表のまま組まれることが多い。渋っていれば
    脚質と血統の前提が変わるが、その評価はメソッド第5章・第10章に沿って
    人が行う。ここは「前提が変わった」ことを見落とさせないための通知に留める。
    """
    import conditions as conditions_module

    if not conditions or not conditions.get('going'):
        return Finding(
            INFO, 'going_unknown',
            f'{race.name}: 馬場状態がまだ発表されていません',
        )
    if conditions_module.is_heavy(conditions):
        surface = conditions.get('surface') or ''
        return Finding(
            WARN, 'heavy_going',
            f"{race.name}: 馬場が「{conditions['going']}」です（{conditions_module.label(conditions)}）",
            'ダートは道悪で高速化し前有利が強まり、芝は時計がかかりパワー型が浮きます（第5章）。'
            '◎と相手の脚質・血統がこの馬場に合っているか確認してください（第10章）。',
        )
    return Finding(
        INFO, 'going_ok',
        f"{race.name}: {conditions_module.label(conditions)}",
    )


def check_going_experience(race, conditions, forms):
    """馬場が渋ったとき、印を打った馬にその馬場の実績があるかを照会する。

    第12章の分析シートは「馬場適性（良・稍重・重・不良）」を必須項目に挙げており、
    第6章も「適性不明な馬は軸にしない」としている。実績の有無は事実の照会なので
    機械が引く。**上げ下げの判断はしない。**
    """
    import form as form_module

    going = (conditions or {}).get('going')
    if not going or going not in form_module.GOING_LEVELS[1:] or not forms:
        return []

    findings = []
    for mark in SENIOR_MARKS:
        for umaban in race.horses_for(mark):
            entry = forms.get(umaban) or {}
            record = entry.get('record') or {}
            if not record:
                continue
            if not form_module.has_experience(record, going):
                findings.append(Finding(
                    WARN, 'no_going_experience',
                    f'{race.name}: {mark}{umaban}番 {entry.get("name", "")} は'
                    f'「{going}」での出走がありません'
                    f'（{form_module.summarise(record, going)}）',
                    '当該馬場の適性が未知数です。'
                    '軸に据えてよいかを第6章・第12章に照らして確認してください。',
                ))
    return findings


def check_confidence(race):
    """勝負度Cは印だけ記録して購入しない（第13章）。"""
    if race.confidence == 'C' and race.bets:
        return Finding(
            BLOCK, 'confidence_c_has_bets',
            f'{race.name}: 勝負度Cなのに買い目が {len(race.bets)}点あります',
            '勝負度Cは印を記録するのみで購入は非推奨です。買い目は記録しません。',
        )
    return None


# ----------------------------------------------------------------------
# まとめて適用
# ----------------------------------------------------------------------

class RaceVerdict:
    def __init__(self, race, findings, composite, bet_odds, win_table, meta,
                 conditions=None, forms=None):
        self.race = race
        self.findings = findings
        self.composite = composite
        self.bet_odds = bet_odds
        self.win_table = win_table
        self.meta = meta
        self.conditions = conditions or {}
        self.forms = forms or {}
        # bet_builder の説明文（その回だけの一時的な情報）。discipline.py は
        # bet_builder を知らないので、呼び出し側（check.py）がここに直接
        # 代入する。race.note には書かない（2026-08-15、note が無限に
        # 積み重なる不具合の修正）。既定 None は「bet_builder を経由して
        # いない」（発走済み・データ欠損など）ことを表す。
        self.bet_note = None

    @property
    def blocked(self):
        return any(f.severity == BLOCK for f in self.findings)

    @property
    def blocks(self):
        return [f for f in self.findings if f.severity == BLOCK]

    @property
    def warnings(self):
        return [f for f in self.findings if f.severity == WARN]

    @property
    def expected_value(self):
        if self.composite is None or self.race.subjective_hit_rate is None:
            return None
        return self.composite * self.race.subjective_hit_rate

    def to_dict(self):
        return {
            'race_id': self.race.race_id,
            'name': self.race.name,
            'blocked': self.blocked,
            'composite_odds': round(self.composite, 2) if self.composite else None,
            'expected_value': round(self.expected_value, 2) if self.expected_value else None,
            'bet_odds': [
                {'bet': str(bet), 'odds': odds}
                for bet, odds in zip(self.race.bets, self.bet_odds)
            ],
            'findings': [f.to_dict() for f in self.findings],
            'conditions': self.conditions,
            'odds_meta': self.meta,
        }


def review_race(race, bet_odds, win_table, meta, now, day, conditions=None,
                forms=None):
    """1レースに第13章の規律を全部あてる。"""
    findings = []

    started = check_already_started(race, now, day)
    if started:
        findings.append(started)
        if started.code != 'no_start_time':
            # 発走済みなら以降の検算に意味がない（BLOCK＝未評価のまま発走／
            # INFO＝評価済みで直近の検算結果が最終形、のどちらでも同じ）。
            # bet_odds・win_table はここでは取得していない（check.py が
            # 発走済みレースはオッズを取りに行かない）ので、ここから先の
            # 合成オッズ・期待値チェックにかけると「オッズが取れない」という
            # 別の（誤った）警告になってしまう。
            return RaceVerdict(race, findings, None, bet_odds, win_table, meta,
                               conditions, forms)

    # 馬場は買い目の有無に関わらず知らせる（朝は未発表のことが多いため）
    findings.append(check_going(race, conditions))
    findings.extend(check_going_experience(race, conditions, forms))

    confidence = check_confidence(race)
    if confidence:
        findings.append(confidence)

    if not race.bets:
        findings.append(Finding(
            INFO, 'no_bets', f'{race.name}: 買い目なし（勝負度{race.confidence or "未評価"}）'))
        return RaceVerdict(race, findings, None, bet_odds, win_table, meta,
                           conditions, forms)

    composite = composite_odds(bet_odds)
    findings.append(check_composite_odds(race, composite))
    findings.append(check_expected_value(race, composite))

    contradiction = check_matched_marks(race)
    if contradiction:
        findings.append(contradiction)

    findings.extend(check_marked_only_trifecta(race))

    lone = check_lone_partner(race)
    if lone:
        findings.append(lone)

    findings.extend(check_win_odds_range(race, win_table))

    return RaceVerdict(race, findings, composite, bet_odds, win_table, meta,
                       conditions, forms)
