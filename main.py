import logging
import time
from scraper import NetkeibaScraper
from analyzer import RaceAnalyzer
from mailer import ResultsMailer

# ログの設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    logger.info("=== 週末重賞予想処理を開始します ===")
    
    # モジュールの初期化
    scraper = NetkeibaScraper()
    analyzer = RaceAnalyzer()
    mailer = ResultsMailer()
    
    # 1. 今週末の重賞レース一覧を取得
    logger.info("今週末の重賞レース一覧を取得中...")
    upcoming_races = scraper.get_this_weekend_races()
    
    if not upcoming_races:
        logger.warning("対象となる重賞レースが見つかりませんでした。テスト用のダミーデータで続行します。")
        # デモのため空リストではなくダミーを生成
        upcoming_races = [{
            'name': 'テスト重賞 G1',
            'url': 'https://race.netkeiba.com/race/shutuba.html?race_id=202405020111',
            'race_id': '202405020111'
        }]
    else:
        logger.info(f"{len(upcoming_races)} 件の重賞レースが見つかりました: {[r['name'] for r in upcoming_races]}")
        
    final_results = {}
    
    # 2. 各レースごとに出走馬を取得・評価
    for race in upcoming_races:
        race_name = race['name']
        race_url = race['url']
        logger.info(f"[{race_name}] の出走馬情報を取得中...")
        
        horses = scraper.get_horses_for_race(race_url)
        if not horses:
            logger.warning(f"[{race_name}] の出馬表がまだ公開されていないか取得に失敗しました。")
            final_results[race_name] = []
            continue
            
        logger.info(f"[{race_name}] {len(horses)} 頭の出走馬が見つかりました。詳細データの取得と評価を開始します...")
        
        evaluated_horses = []
        for horse in horses:
            horse_id = horse['horse_id']
            if not horse_id:
                continue
                
            horse_name_str = horse['name']
            logger.debug(f"{horse_name_str} の詳細データを取得中...")
            details = scraper.get_horse_details(horse_id)
            
            # 馬の評価
            evaluated = analyzer.evaluate_horse(horse, details)
            evaluated_horses.append(evaluated)
            time.sleep(1) # サーバー負荷軽減のため1秒待機
            
        # 3. 上位4頭を抽出
        top_picks = analyzer.get_top_picks(evaluated_horses, top_n=4)
        final_results[race_name] = top_picks
        
        logger.info(f"[{race_name}] の評価完了。上位 {len(top_picks)} 頭を抽出しました。")
        
    # 4. メールで結果を送信
    logger.info("予想結果をメールで送信します...")
    # NOTE: GitHub Actionsでは環境変数が正しくセットされている前提
    success = mailer.send_email(final_results)
    
    if success:
        logger.info("=== 処理が正常に完了しました（メール送信成功） ===")
    else:
        logger.warning("=== 処理は完了しましたが、メール送信に失敗したか、認証情報が未設定です（ログ出力へフォールバックします） ===")
        # メールが送れないときはログに表示
        print("\n" + mailer._format_body(final_results))

if __name__ == "__main__":
    main()
