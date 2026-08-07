"""netkeiba.com から重賞情報・出走馬データ・レース結果を取得するスクレイパー。

クラウド（GitHub Actions）上での無人実行を前提としているため、
以下の方針で実装している:

- 取得に失敗したときは黙ってダミーを返さず、例外か空を返して呼び出し側に判断させる
- ネットワークの一時的な失敗はリトライで吸収する
- netkeiba はホストごとに文字コードが違う（race=UTF-8 / db=EUC-JP）ため個別に判定する
- HTML 構造の変化に備え、主要セレクタが取れない場合は代替の探索に切り替える
"""

import logging
import re
import time
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Icon_GradeType の数字とグレード表記の対応（netkeiba のクラス名準拠）
GRADE_ICON_MAP = {
    '1': 'G1',
    '2': 'G2',
    '3': 'G3',
    '15': 'L',      # リステッド
    '16': 'J.G1',
    '17': 'J.G2',
    '18': 'J.G3',
}

# 重賞とみなすグレード（リステッド・障害重賞は対象外）
GRADED_LABELS = {'G1', 'G2', 'G3'}


class ScrapeError(RuntimeError):
    """取得そのものに失敗したことを表す。0件だったこととは区別する。"""


class NetkeibaScraper:
    RACE_HOST = 'https://race.netkeiba.com'
    DB_HOST = 'https://db.netkeiba.com'

    # ホストごとの既定文字コード。ヘッダや meta で判別できたときはそちらを優先する。
    ENCODING_HINTS = {
        'race.netkeiba.com': 'utf-8',
        'db.netkeiba.com': 'euc-jp',
    }

    def __init__(self, request_interval=1.0, max_retries=3, timeout=20):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
        })
        self.request_interval = request_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_request_at = 0.0

    # ------------------------------------------------------------------
    # 低レベルの取得処理
    # ------------------------------------------------------------------

    def _throttle(self):
        """前回リクエストから request_interval 秒あくように待つ。"""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.request_interval:
            time.sleep(self.request_interval - elapsed)
        self._last_request_at = time.monotonic()

    def _resolve_encoding(self, response):
        """レスポンスの文字コードを決める。

        requests はヘッダに charset が無い HTML を latin-1 とみなすため、
        そのままだと日本語が壊れる。ヘッダ → meta タグ → ホスト既定 の順に見る。
        """
        content_type = response.headers.get('Content-Type', '')
        header_charset = re.search(r'charset=([\w\-]+)', content_type, re.I)
        if header_charset:
            return header_charset.group(1)

        head = response.content[:2048]
        meta_charset = re.search(br'charset=["\']?([\w\-]+)', head, re.I)
        if meta_charset:
            return meta_charset.group(1).decode('ascii', 'ignore')

        host = requests.utils.urlparse(response.url).hostname or ''
        return self.ENCODING_HINTS.get(host) or response.apparent_encoding or 'utf-8'

    def fetch(self, url, params=None):
        """URL を取得して BeautifulSoup を返す。失敗時は ScrapeError。"""
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                response.encoding = self._resolve_encoding(response)
                return BeautifulSoup(response.text, 'lxml')
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    backoff = 2 ** attempt
                    logger.warning(
                        '取得失敗 (%s/%s) %s: %s / %s秒後に再試行',
                        attempt, self.max_retries, url, exc, backoff,
                    )
                    time.sleep(backoff)

        raise ScrapeError(f'{url} の取得に {self.max_retries} 回失敗しました: {last_error}')

    def fetch_json(self, url, params=None):
        """JSON API を取得する。失敗しても致命的でない用途向けに None を返す。"""
        try:
            self._throttle()
            response = self.session.get(
                url, params=params, timeout=self.timeout,
                headers={'Referer': f'{self.RACE_HOST}/'},
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.warning('JSON取得に失敗しました %s: %s', url, exc)
            return None

    # ------------------------------------------------------------------
    # 開催日・重賞一覧
    # ------------------------------------------------------------------

    @staticmethod
    def upcoming_weekend_dates(today=None):
        """直近の土曜・日曜の日付を返す。土日に実行した場合はその週末を対象にする。"""
        today = today or date.today()
        # weekday(): 月=0 ... 土=5, 日=6
        days_to_saturday = (5 - today.weekday()) % 7
        saturday = today + timedelta(days=days_to_saturday)
        if today.weekday() == 6:  # 日曜に実行したら「今日」を含む週末を見る
            saturday = today - timedelta(days=1)
        return [saturday, saturday + timedelta(days=1)]

    def get_races_for_date(self, target_date):
        """指定日の全レースを取得する。開催が無ければ空リスト。"""
        kaisai_date = target_date.strftime('%Y%m%d')
        # race_list.html は JavaScript で描画されるため、実データを返す
        # race_list_sub.html を直接叩く。取れなければ通常ページにフォールバック。
        endpoints = [
            f'{self.RACE_HOST}/top/race_list_sub.html',
            f'{self.RACE_HOST}/top/race_list.html',
        ]

        errors = []
        for endpoint in endpoints:
            try:
                soup = self.fetch(endpoint, params={'kaisai_date': kaisai_date})
            except ScrapeError as exc:
                errors.append(str(exc))
                continue

            races = self._parse_race_list(soup, target_date)
            if races:
                return races
            logger.info('%s では %s のレースを抽出できませんでした', endpoint, kaisai_date)

        if errors and len(errors) == len(endpoints):
            raise ScrapeError(f'{kaisai_date} のレース一覧を取得できませんでした: {errors[0]}')
        return []

    def _parse_race_list(self, soup, target_date):
        """レース一覧ページから race_id 付きのリンクを拾う。"""
        races = {}
        for anchor in soup.select('a[href*="race_id="]'):
            href = anchor.get('href', '')
            race_id_match = re.search(r'race_id=(\d+)', href)
            if not race_id_match:
                continue
            race_id = race_id_match.group(1)
            if race_id in races:
                continue

            # グレードのアイコンは、リンク自身か親要素のどこかに入っている
            container = anchor
            grade = self._extract_grade(container)
            if grade is None and anchor.parent is not None:
                grade = self._extract_grade(anchor.parent)

            name = self._extract_race_name(anchor)
            if not name:
                continue

            races[race_id] = {
                'race_id': race_id,
                'name': name,
                'grade': grade,
                'date': target_date.isoformat(),
                'start_time': self._extract_start_time(anchor),
                'url': f'{self.RACE_HOST}/race/shutuba.html?race_id={race_id}',
                'result_url': f'{self.DB_HOST}/race/{race_id}/',
            }

        return list(races.values())

    @staticmethod
    def _extract_start_time(anchor):
        """発走時刻を "HH:MM" で返す。取れなければ None。

        当日の追加配信で「もう発走したレースを送らない」ために使う。
        """
        cell = anchor.select_one('.RaceList_Itemtime, .RaceData_Item01, .Race_Time')
        text = cell.get_text(strip=True) if cell else anchor.get_text(' ', strip=True)
        match = re.search(r'(\d{1,2}):(\d{2})', text)
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f'{hour:02d}:{minute:02d}'
        return None

    @staticmethod
    def _extract_grade(element):
        """Icon_GradeType クラス、無ければレース名テキストからグレードを判定する。"""
        for span in element.select('[class*="Icon_GradeType"]'):
            for cls in span.get('class', []):
                digits = re.fullmatch(r'Icon_GradeType(\d+)', cls)
                if digits and digits.group(1) in GRADE_ICON_MAP:
                    return GRADE_ICON_MAP[digits.group(1)]

        text = element.get_text(' ', strip=True)
        # GIII → GII → GI の順に見ないと GIII が GI に誤判定される
        for pattern, label in (
            (r'G\s*III|G3\b', 'G3'),
            (r'G\s*II|G2\b', 'G2'),
            (r'G\s*I\b|G1\b', 'G1'),
        ):
            if re.search(pattern, text):
                return label
        return None

    @staticmethod
    def _extract_race_name(anchor):
        """リンクからレース名を取り出す。専用要素が無ければリンク文字列全体を使う。"""
        title = anchor.select_one('.ItemTitle, .RaceName, .Race_Name')
        text = title.get_text(strip=True) if title else anchor.get_text(' ', strip=True)
        text = re.sub(r'\s+', ' ', text).strip()
        # 一覧では「11R 天皇賞(秋) G1 15:40 芝2000m」のように付随情報が混ざる
        text = re.sub(r'^\d{1,2}R\s*', '', text)
        return text

    def get_weekend_graded_races(self, today=None):
        """今週末の重賞レース一覧を返す。

        戻り値は (重賞レースのリスト, 取得できた開催日のリスト)。
        1日も取得できなければ ScrapeError を投げる。
        """
        graded = []
        fetched_dates = []
        errors = []

        for target_date in self.upcoming_weekend_dates(today):
            try:
                races = self.get_races_for_date(target_date)
            except ScrapeError as exc:
                logger.error('%s の取得に失敗: %s', target_date, exc)
                errors.append(exc)
                continue

            fetched_dates.append(target_date)
            graded.extend(r for r in races if r['grade'] in GRADED_LABELS)

        if not fetched_dates:
            raise ScrapeError(f'今週末のレース一覧をどの日も取得できませんでした: {errors}')

        graded.sort(key=lambda r: (r['date'], r['race_id']))
        return graded, fetched_dates

    # ------------------------------------------------------------------
    # 出馬表
    # ------------------------------------------------------------------

    def get_race_card(self, race):
        """出馬表を取得して、出走馬と当日の条件をまとめて返す。

        戻り値は {'horses': [...], 'conditions': {...}}。
        馬体重と馬場状態は当日にならないと出ないため、
        取れなかった項目は None のままにして呼び出し側で扱えるようにする。
        """
        soup = self.fetch(race['url'])
        rows = soup.select('.Shutuba_Table tr.HorseList')
        if not rows:
            logger.info('[%s] 出馬表がまだ公開されていないようです', race['name'])
            return {'horses': [], 'conditions': {}}

        horses = []
        for row in rows:
            horse_link = row.select_one('td.HorseInfo a')
            if not horse_link:
                continue

            horse_id_match = re.search(r'/horse/(\d+)', horse_link.get('href', ''))
            jockey_link = row.select_one('td.Jockey a')
            umaban_cell = row.select_one('td.Umaban, td[class*="Umaban"]')
            weight, weight_diff = _parse_weight(row.select_one('td.Weight'))

            horses.append({
                'umaban': _clean(umaban_cell),
                'name': _clean(horse_link),
                'horse_id': horse_id_match.group(1) if horse_id_match else '',
                'jockey': _clean(jockey_link),
                'weight': weight,
                'weight_diff': weight_diff,
                'odds': None,
                'ninki': None,
            })

        self._attach_odds(race['race_id'], horses)
        return {'horses': horses, 'conditions': _parse_conditions(soup)}

    def get_horses_for_race(self, race):
        """出走馬の一覧だけが欲しいとき用。"""
        return self.get_race_card(race)['horses']

    def _attach_odds(self, race_id, horses):
        """単勝オッズと人気を付与する。取得できなくても処理は続行する。

        出馬表ページのオッズ欄は JavaScript で後から埋まるため、
        ページ側の HTML ではなく netkeiba のオッズ API を参照する。
        """
        payload = self.fetch_json(
            f'{self.RACE_HOST}/api/api_get_jra_odds.html',
            params={'race_id': race_id, 'type': '1', 'action': 'init'},
        )
        if not payload:
            return

        odds_table = (payload.get('data') or {}).get('odds', {}).get('1', {})
        if not isinstance(odds_table, dict) or not odds_table:
            logger.info('レース %s のオッズがまだ配信されていません', race_id)
            return

        for horse in horses:
            if not horse['umaban'].isdigit():
                continue
            number = int(horse['umaban'])
            # API は馬番を "1" と "01" のどちらの形式でも返しうる
            entry = odds_table.get(str(number)) or odds_table.get(f'{number:02d}')
            if not entry:
                continue
            # entry は ["3.5", "3.9", "1", ...] のような配列（先頭=オッズ, 3番目=人気）
            horse['odds'] = _to_float(entry[0]) if len(entry) > 0 else None
            horse['ninki'] = _to_int(entry[2]) if len(entry) > 2 else None

    # ------------------------------------------------------------------
    # 馬の詳細（血統・戦績）
    # ------------------------------------------------------------------

    def get_horse_details(self, horse_id):
        """血統と過去戦績を取得する。取得できない項目は既定値のまま返す。"""
        details = {
            'sire': '',
            'broodmare_sire': '',
            'past_runs': 0,
            'wins': 0,
            'top3': 0,
            'recent_ranks': [],
        }

        try:
            soup = self.fetch(f'{self.DB_HOST}/horse/{horse_id}/')
        except ScrapeError as exc:
            logger.warning('馬 %s の詳細取得に失敗しました: %s', horse_id, exc)
            return details

        blood_rows = soup.select('.blood_table tr')
        if blood_rows:
            details['sire'] = _first_link_text(blood_rows[0])
            # 血統表は 4 行構成で、3 行目の先頭セルが母父にあたる
            if len(blood_rows) > 2:
                details['broodmare_sire'] = _first_link_text(blood_rows[2])

        details.update(self._parse_race_history(soup))
        return details

    @staticmethod
    def _parse_race_history(soup):
        """競走成績テーブルから着順を集計する。

        列の位置は表示設定で前後するため、ヘッダから「着順」列を探す。
        """
        table = soup.select_one('table.db_h_race_results')
        if not table:
            return {}

        headers = [th.get_text(strip=True) for th in table.select('tr th')]
        try:
            rank_index = headers.index('着順')
        except ValueError:
            rank_index = 11  # 従来のレイアウトでの位置

        ranks = []
        for row in table.select('tbody tr'):
            cells = row.select('td')
            if len(cells) <= rank_index:
                continue
            rank_text = cells[rank_index].get_text(strip=True)
            ranks.append(int(rank_text) if rank_text.isdigit() else None)

        finished = [r for r in ranks if r is not None]
        return {
            'past_runs': len(ranks),
            'wins': sum(1 for r in finished if r == 1),
            'top3': sum(1 for r in finished if r <= 3),
            'recent_ranks': ranks[:5],  # 上から新しい順に並んでいる
        }

    # ------------------------------------------------------------------
    # レース結果（答え合わせ用）
    # ------------------------------------------------------------------

    def get_race_result(self, race_id):
        """確定したレース結果と払戻を取得する。未確定なら None。"""
        try:
            soup = self.fetch(f'{self.DB_HOST}/race/{race_id}/')
        except ScrapeError as exc:
            logger.warning('レース %s の結果取得に失敗しました: %s', race_id, exc)
            return None

        table = soup.select_one('table.race_table_01')
        if not table:
            return None

        finishing_order = []
        for row in table.select('tr')[1:]:
            cells = row.select('td')
            if len(cells) < 4:
                continue
            rank_text = cells[0].get_text(strip=True)
            horse_link = cells[3].select_one('a')
            if not horse_link:
                continue
            finishing_order.append({
                'rank': int(rank_text) if rank_text.isdigit() else None,
                'umaban': cells[2].get_text(strip=True),
                'name': horse_link.get_text(strip=True),
            })

        if not finishing_order:
            return None

        return {
            'race_id': race_id,
            'finishing_order': finishing_order,
            'payouts': _parse_payouts(soup),
        }


# ----------------------------------------------------------------------
# 小さなヘルパー
# ----------------------------------------------------------------------

def _clean(element):
    return element.get_text(strip=True) if element else ''


def _parse_weight(cell):
    """馬体重セルを (体重, 増減) に分解する。当日朝まで未発表なので (None, None) もありうる。"""
    text = _clean(cell)
    if not text:
        return None, None

    weight_match = re.match(r'(\d{3})', text)
    diff_match = re.search(r'\(([-+±]?\d+)\)', text)

    weight = int(weight_match.group(1)) if weight_match else None
    diff = _to_int(diff_match.group(1).replace('±', '')) if diff_match else None
    return weight, diff


def _parse_conditions(soup):
    """出馬表ヘッダから天候・馬場状態・コースを読む。

    「直前に雨が降って馬場が重くなった」を検知するのがこの関数の役目。
    当日にならないと馬場状態は出ないので、取れない項目は None を入れる。
    """
    header = soup.select_one('.RaceData01')
    text = header.get_text(' ', strip=True) if header else ''
    text = text.replace('\xa0', ' ')

    # 「馬場:良」「芝 : 稍重」どちらの書き方でも拾えるようにする
    going_match = re.search(
        r'(?:馬場|芝|ダート|ダ)\s*[:：]\s*(良|稍重|重|不良)', text
    )
    weather_match = re.search(r'天候\s*[:：]\s*(\S+?)(?:\s|/|$)', text)
    distance_match = re.search(r'(\d{3,4})\s*m', text)

    surface = None
    if re.search(r'ダート|ダ\s*\d', text):
        surface = 'ダート'
    elif '芝' in text:
        surface = '芝'

    return {
        'going': going_match.group(1) if going_match else None,
        'weather': weather_match.group(1) if weather_match else None,
        'surface': surface,
        'distance': int(distance_match.group(1)) if distance_match else None,
    }


def _first_link_text(row):
    link = row.select_one('td a')
    return link.get_text(strip=True) if link else ''


def _to_float(value):
    try:
        return float(str(value).replace(',', ''))
    except (TypeError, ValueError):
        return None


def _to_int(value):
    try:
        return int(str(value).replace(',', ''))
    except (TypeError, ValueError):
        return None


def _parse_payouts(soup):
    """払戻テーブルを {'単勝': [{'combination':..., 'yen':...}], ...} に整形する。"""
    payouts = {}
    for table in soup.select('table.pay_table_01'):
        for row in table.select('tr'):
            header = row.select_one('th')
            value_cells = row.select('td')
            if not header or len(value_cells) < 2:
                continue

            bet_type = header.get_text(strip=True)
            combinations = [t for t in value_cells[0].stripped_strings]
            amounts = [t for t in value_cells[1].stripped_strings]

            entries = []
            for combination, amount in zip(combinations, amounts):
                yen = _to_int(amount.replace('円', ''))
                if yen is not None:
                    entries.append({'combination': combination, 'yen': yen})
            if entries:
                payouts[bet_type] = entries

    return payouts
