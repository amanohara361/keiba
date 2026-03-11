import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import time
import logging

# ロギングの設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class NetkeibaScraper:
    """netkeiba.comから重賞情報と出走馬データを取得・解析するスクレイパー"""
    
    BASE_URL = "https://race.netkeiba.com"
    DB_BASE_URL = "https://db.netkeiba.com"
    
    def __init__(self):
        self.session = requests.Session()
        # 簡易的なUser-Agentを設定
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        
    def _get_soup(self, url):
        """指定したURLからBeautifulSoupオブジェクトを取得する"""
        try:
            time.sleep(1) # サーバー負荷軽減のため1秒待機
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            response.encoding = 'EUC-JP' # netkeibaは基本的にEUC-JP
            return BeautifulSoup(response.text, 'html.parser')
        except Exception as e:
            logging.error(f"Failed to fetch {url}: {e}")
            return None

    def get_this_weekend_races(self):
        """今週末の重賞レースのリストとそのURLを取得する"""
        # 実際の運用では動的に今週末のレース一覧URLを構築するか、トップページからリンクを辿る
        # ここではnetkeibaのトップページまたはレーストップから今週の重賞を抽出する想定
        # (※ 実行タイミングによって取得先URLが変わるため、開催日一覧ページ等を利用)
        top_url = "https://race.netkeiba.com/top/race_list.html"
        soup = self._get_soup(top_url)
        races = []
        
        if not soup:
            return races
            
        # 重賞レースのリンクを取得 (例: クラスに 'Icon_GradeType' が含まれるか等で判定)
        # 簡易実装として、aタグの中に「G1」「G2」「G3」のいずれかが含まれるものを抽出
        for a_tag in soup.select('a[href*="race/shutuba.html"]'):
            text = a_tag.get_text(strip=True)
            if re.search(r'(GI|GII|GIII|G1|G2|G3)', text):
                href = a_tag.get('href')
                # urlが相対パスの場合を考慮
                if href.startswith('../'):
                    href = href.replace('../', f"{self.BASE_URL}/")
                elif href.startswith('/'):
                    href = f"{self.BASE_URL}{href}"
                
                race_id_match = re.search(r'race_id=(\d+)', href)
                if race_id_match:
                    races.append({
                        'name': text,
                        'url': href,
                        'race_id': race_id_match.group(1)
                    })
                    
        # 重複排除
        seen = set()
        unique_races = []
        for r in races:
            if r['race_id'] not in seen:
                seen.add(r['race_id'])
                unique_races.append(r)
                
        # 取得できない場合のフォールバック（テスト用）
        if not unique_races:
            logging.info("We couldn't naturally find graded races on the top page. Returning a dummy/demo race structure for testing.")
            unique_races.append({
                'name': 'G1 ダミーレース (テスト)',
                'url': 'https://race.netkeiba.com/race/shutuba.html?race_id=202405020111', 
                'race_id': '202405020111'
            })
            
        return unique_races

    def get_horses_for_race(self, race_url):
        """指定されたレースURLから出走馬の基本一覧を取得する"""
        soup = self._get_soup(race_url)
        horses = []
        
        if not soup:
            return horses
            
        # 出馬表のテーブル行を取得
        rows = soup.select('.Shutuba_Table tr.HorseList')
        for row in rows:
            horse_td = row.select_one('td.HorseInfo a')
            jockey_td = row.select_one('td.Jockey a')
            num_td = row.select_one('td.Umaban')
            
            if horse_td and jockey_td:
                horse_name = horse_td.get_text(strip=True)
                horse_url = horse_td.get('href')
                jockey_name = jockey_td.get_text(strip=True)
                umaban = num_td.get_text(strip=True) if num_td else ""
                
                horse_id_match = re.search(r'/horse/(\d+)', horse_url)
                horse_id = horse_id_match.group(1) if horse_id_match else ""
                
                horses.append({
                    'umaban': umaban,
                    'name': horse_name,
                    'horse_id': horse_id,
                    'jockey': jockey_name
                })
                
        return horses

    def get_horse_details(self, horse_id):
        """血統、過去戦績など馬の詳細データを取得する"""
        url = f"{self.DB_BASE_URL}/horse/{horse_id}/"
        soup = self._get_soup(url)
        details = {
            'sire': '', # 父
            'broodmare_sire': '', # 母父
            'past_runs': 0, # 過去出走数
            'wins': 0, # 1着回数
            'top2': 0, # 2着以内回数
            'top3': 0, # 3着以内回数
            'course_history': [] # コース履歴(簡易的)
        }
        
        if not soup:
            return details
            
        # --- 血統情報の取得 ---
        blood_table = soup.select_one('.blood_table')
        if blood_table:
            # 父は左上のtd
            sire_td = blood_table.select('tr')[0].select('td')[0]
            if sire_td and sire_td.a:
                details['sire'] = sire_td.a.get_text(strip=True)
            # 母父は左下のtdのそのまた父 (通常は3行目の最初か、2行目の最初のtdの親)
            # 簡易的に、プロフィールテーブルか血統テーブル内の母父項目を取得
            bms_td = blood_table.select('tr')[2].select('td')[0]
            if bms_td and bms_td.a:
                details['broodmare_sire'] = bms_td.a.get_text(strip=True)
                
        # --- 戦績情報の取得 ---
        # 競走成績のテーブルを取得
        history_table = soup.select_one('table.db_h_race_results')
        if history_table:
            rows = history_table.select('tbody tr')
            details['past_runs'] = len(rows)
            for row in rows:
                cols = row.select('td')
                if len(cols) > 11:
                    # 着順 (例: '1', '2', '中止'など)
                    rank_str = cols[11].get_text(strip=True)
                    if rank_str.isdigit():
                        rank = int(rank_str)
                        if rank == 1:
                            details['wins'] += 1
                        if rank <= 2:
                            details['top2'] += 1
                        if rank <= 3:
                            details['top3'] += 1
                    
                    # コース長や芝/ダの判別 (例: '芝1600' など)
                    distance_str = cols[14].get_text(strip=True)
                    details['course_history'].append(distance_str)
                    
        return details

# 簡単なモジュールテスト用
if __name__ == "__main__":
    scraper = NetkeibaScraper()
    races = scraper.get_this_weekend_races()
    print("見つかった重賞レース:", races)
    if races:
        horses = scraper.get_horses_for_race(races[0]['url'])
        print(f"[{races[0]['name']}] の出走馬数:", len(horses))
        if horses:
            details = scraper.get_horse_details(horses[0]['horse_id'])
            print(f"{horses[0]['name']} の詳細データ:", details)
