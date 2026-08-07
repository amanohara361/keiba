"""出走馬を評価して本命候補と穴候補を選ぶアナライザー。

スコアの重みは WEIGHTS にまとめてある。予想メソッドを更新するときは
ここの数値と METHOD_VERSION を変更する。data/accuracy.md にはバージョン
ごとの的中率・回収率が残るので、変更が効いたかどうかを後から検証できる。
"""

# メソッドを変更したら必ず上げる。結果の集計はこのバージョン単位で行う。
METHOD_VERSION = 'v2'

WEIGHTS = {
    'top_sire': 15.0,
    'other_sire': 5.0,
    'top_jockey': 20.0,
    'other_jockey': 10.0,
    'win_rate_factor': 50.0,   # 勝率 × この値（上限 win_rate_cap）
    'win_rate_cap': 20.0,
    'top3_rate_factor': 40.0,  # 複勝率 × この値（上限 top3_rate_cap）
    'top3_rate_cap': 20.0,
    'recent_form': 10.0,       # 直近5走の成績によるボーナス
    'career_veteran': 15.0,    # 10戦以上
    'career_middle': 10.0,     # 5戦以上
    'career_light': 5.0,
}

TOP_SIRES = [
    'ディープインパクト', 'ロードカナロア', 'エピファネイア', 'キズナ',
    'ハーツクライ', 'ドゥラメンテ', 'モーリス', 'ルーラーシップ',
    'キタサンブラック', 'シルバーステート', 'レイデオロ',
]

TOP_JOCKEYS = [
    'ルメール', '川田将雅', '横山武史', '松山弘平', '坂井瑠星',
    '戸崎圭太', '武豊', 'モレイラ', '岩田望来', '鮫島克駿',
]

# 「妙味あり」と判定する、人気順位とスコア順位の差
VALUE_GAP_THRESHOLD = 3
# 穴馬候補とみなす単勝オッズの下限
LONGSHOT_MIN_ODDS = 10.0


class RaceAnalyzer:
    def __init__(self, weights=None):
        self.weights = {**WEIGHTS, **(weights or {})}
        self.method_version = METHOD_VERSION

    def evaluate_horse(self, horse, details):
        """1頭ぶんのスコアと、その内訳の説明を返す。"""
        w = self.weights
        score = 0.0
        reasons = []

        # --- 血統 ---
        sire = details.get('sire') or '不明'
        if any(top in sire for top in TOP_SIRES):
            score += w['top_sire']
            reasons.append(f"好血統（父: {sire}） (+{w['top_sire']:.0f})")
        else:
            score += w['other_sire']
            reasons.append(f"血統（父: {sire}） (+{w['other_sire']:.0f})")

        # --- 騎手 ---
        jockey = horse.get('jockey') or '不明'
        if any(top in jockey for top in TOP_JOCKEYS):
            score += w['top_jockey']
            reasons.append(f"トップジョッキー（{jockey}） (+{w['top_jockey']:.0f})")
        else:
            score += w['other_jockey']
            reasons.append(f"騎手（{jockey}） (+{w['other_jockey']:.0f})")

        # --- 通算成績 ---
        past_runs = details.get('past_runs', 0)
        if past_runs > 0:
            win_rate = details.get('wins', 0) / past_runs
            top3_rate = details.get('top3', 0) / past_runs

            win_score = min(w['win_rate_cap'], win_rate * w['win_rate_factor'])
            score += win_score
            reasons.append(f'勝率 {win_rate:.1%} (+{win_score:.1f})')

            top3_score = min(w['top3_rate_cap'], top3_rate * w['top3_rate_factor'])
            score += top3_score
            reasons.append(f'複勝率 {top3_rate:.1%} (+{top3_score:.1f})')
        else:
            reasons.append('戦績データなし (+0)')

        # --- 直近の調子 ---
        recent = [r for r in details.get('recent_ranks', []) if r is not None]
        if recent:
            recent_top3 = sum(1 for r in recent if r <= 3) / len(recent)
            form_score = recent_top3 * w['recent_form']
            score += form_score
            reasons.append(
                f"直近{len(recent)}走で3着内 {sum(1 for r in recent if r <= 3)}回 (+{form_score:.1f})"
            )

        # --- キャリア ---
        if past_runs >= 10:
            score += w['career_veteran']
            reasons.append(f"豊富なキャリア (+{w['career_veteran']:.0f})")
        elif past_runs >= 5:
            score += w['career_middle']
            reasons.append(f"中堅キャリア (+{w['career_middle']:.0f})")
        else:
            score += w['career_light']
            reasons.append(f"キャリア浅 (+{w['career_light']:.0f})")

        return {
            'horse': horse,
            'details': details,
            'total_score': round(score, 1),
            'reasons': reasons,
        }

    def analyze_race(self, evaluated_horses, top_n=4):
        """評価済みの全頭から本命上位と穴候補を選ぶ。

        人気（=市場の評価）より自分のスコアが高い馬を「妙味あり」とし、
        その中でオッズが付いている馬を穴候補として別枠で出す。
        """
        ranked = sorted(evaluated_horses, key=lambda e: e['total_score'], reverse=True)

        for score_rank, entry in enumerate(ranked, 1):
            entry['score_rank'] = score_rank
            ninki = entry['horse'].get('ninki')
            # 人気が取れていない時点（前日など）は妙味判定をしない
            entry['value_gap'] = (ninki - score_rank) if ninki else None
            entry['is_value'] = bool(
                entry['value_gap'] is not None and entry['value_gap'] >= VALUE_GAP_THRESHOLD
            )

        # ◎ には別途単勝を買うので、穴候補からは除く（同じ馬に二重に賭けないため）
        longshots = [
            e for e in ranked[1:]
            if e['is_value'] and (e['horse'].get('odds') or 0) >= LONGSHOT_MIN_ODDS
        ]

        return {
            'method_version': self.method_version,
            'top_picks': ranked[:top_n],
            'longshot': longshots[0] if longshots else None,
            'field_size': len(ranked),
        }

    # 旧インタフェース。main.py 以外から呼ばれていた場合に備えて残してある。
    def get_top_picks(self, evaluated_horses, top_n=4):
        return self.analyze_race(evaluated_horses, top_n)['top_picks']
