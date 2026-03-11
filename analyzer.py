class RaceAnalyzer:
    """各馬のデータを評価し、上位4頭を抽出するアナライザー"""
    
    def __init__(self):
        # 種牡馬の簡易的な評価（芝/ダートや長距離適性など、今回は一律にスコアを加算）
        # 実際にはここに種牡馬ごとの詳細なデータ辞書を持つ
        self.top_sires = ["ディープインパクト", "ロードカナロア", "エピファネイア", "キズナ", "ハーツクライ", "ドゥラメンテ", "モーリス", "ルーラーシップ"]
        # 人気騎手の簡易評価
        self.top_jockeys = ["ルメール", "川田将雅", "横山武史", "松山弘平", "坂井瑠星", "戸崎圭太", "武豊"]

    def evaluate_horse(self, horse_data, details_data):
        """
        馬の基本情報と詳細情報からスコアを算出する。
        """
        score = 0.0
        evaluation_reasons = []

        # 1. 血統評価 (最大 20点)
        sire = details_data.get('sire', '')
        if any(top_sire in sire for top_sire in self.top_sires):
            score += 15.0
            evaluation_reasons.append(f"好血統（父: {sire}） (+15)")
        else:
            score += 5.0
            evaluation_reasons.append(f"血統（父: {sire}） (+5)")

        # 2. 騎手評価 (最大 20点)
        jockey = horse_data.get('jockey', '')
        if any(top_jockey in jockey for top_jockey in self.top_jockeys):
            score += 20.0
            evaluation_reasons.append(f"トップジョッキー（{jockey}） (+20)")
        else:
            score += 10.0
            evaluation_reasons.append(f"騎手（{jockey}） (+10)")

        # 3. 戦績評価 (最大 40点)
        past_runs = details_data.get('past_runs', 0)
        wins = details_data.get('wins', 0)
        top2 = details_data.get('top2', 0)
        top3 = details_data.get('top3', 0)
        
        if past_runs > 0:
            win_rate = wins / past_runs
            top3_rate = top3 / past_runs
            
            # 勝率ボーナス (最大20点)
            win_score = min(20.0, win_rate * 50.0)
            score += win_score
            evaluation_reasons.append(f"勝率 {win_rate:.1%} (+{win_score:.1f})")
            
            # 複勝率ボーナス (最大20点)
            top3_score = min(20.0, top3_rate * 40.0)
            score += top3_score
            evaluation_reasons.append(f"複勝率 {top3_rate:.1%} (+{top3_score:.1f})")
        else:
            evaluation_reasons.append("戦績データなし (+0)")

        # 4. コース適性/経験評価 (最大 20点)
        # 今回の簡易版では、出走回数や経験をベースに加点
        if past_runs >= 10:
            score += 15.0
            evaluation_reasons.append("豊富なキャリア (+15)")
        elif past_runs >= 5:
            score += 10.0
            evaluation_reasons.append("中堅キャリア (+10)")
        else:
            score += 5.0
            evaluation_reasons.append("キャリア浅 (+5)")

        # 最終スコアの格納
        return {
            'horse': horse_data,
            'details': details_data,
            'total_score': round(score, 1),
            'reasons': evaluation_reasons
        }

    def get_top_picks(self, evaluated_horses, top_n=4):
        """評価済みリストから上位N頭を抽出する"""
        # スコアの降順でソート
        sorted_horses = sorted(evaluated_horses, key=lambda x: x['total_score'], reverse=True)
        return sorted_horses[:top_n]

if __name__ == "__main__":
    # テスト用
    analyzer = RaceAnalyzer()
    dummy_horse = {'name': 'テストホース', 'jockey': 'ルメール'}
    dummy_details = {'sire': 'ディープインパクト', 'past_runs': 10, 'wins': 3, 'top2': 5, 'top3': 7}
    result = analyzer.evaluate_horse(dummy_horse, dummy_details)
    print("評価結果:", result)
    print("抽出テスト:")
    picks = analyzer.get_top_picks([result])
    print(picks)
