#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""地方競馬（NAR公式CSV）の取り込みの検証。ネットワークには接続しない。

ここで確かめたいのは「公式CSVの形をそのまま信じてよいか」ではなく、
**読み違えると静かに間違った結論になる箇所**を押さえたかどうか。

  - 中央馬（交流重賞の遠征馬）の「近走0走」を休み明けと読まないこと
  - 帯広ばんえいを混ぜないこと（第14章が対象外と定めている）
  - オッズが未発売のときに「0件だった」と静かに済ませないこと
  - race_id が中央のものと混ざらないよう org で判別すること

CSVの実物は 2026-08-11〜12 に keiba.go.jp から取得したものを縮めて使っている。
着順は 2026-08-11 クラスターカップJpnIII の実データではなく、
**架空のフィクスチャ**（決定ログ「実在レースの着順を手で書き起こしてテストに
埋めない」）。所属欄の 'JRA' という値だけが実物由来の事実である。
"""

import io
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date  # noqa: E402

import bets  # noqa: E402
import nar  # noqa: E402
import nar_card  # noqa: E402


RACELIST_HEADER = (
    '競馬場,競走年月日,レース番号,発走時刻,競走種類名称,レース名,芝ダート区分,回り,'
    '距離,天候,馬場,頭数,条件,1着賞金(円),コーナー通過順1,コーナー通過順2,'
    'コーナー通過順3,コーナー通過順4'
)

RACELIST = RACELIST_HEADER + '\n' + '\n'.join([
    '笠松,20260812,11,1635,重賞,テスト記念,ダート,右,1580,晴,良,10,オープン,6000000,,,,',
    '浦和,20260812,11,1845,準重賞,テストスプリント,ダート,左,1400,曇,稍重,9,オープン,7000000,,,,',
    '大井,20260812,3,1520,普通,平場戦,ダート,右,1200,晴,良,12,一般,300000,,,,',
    # ばんえいは第14章が対象外と定めている。混ぜないことを確かめる。
    '帯広ば,20260812,1,1410,重賞,ばんえい記念,ダート,直,200,晴,良,10,一般,1000000,,,,',
    # 近走の材料（確定済みの過去のレース）。
    # コーナー通過順は**全馬分**が1つの文字列に入る。区切り（',' '-' '=' 括弧）は
    # 差の大きさを表すだけで、順序の解釈には影響しない。
    '笠松,20260720,12,1550,特別,前走にあたるレース,ダート,右,1400,晴,良,5,一般,3000000,'
    '"6,5-4,3,1","5,6-4,1=3","4,6-5,1,3","(1,4),6-3,5"',
    '浦和,20260715,10,1820,特別,別の前走,ダート,左,1400,雨,重,8,一般,900000,'
    '"1,2,3","1,2,3","1,2,3","1,2,3"',
])

HORSELIST_HEADER = (
    '競馬場,競走年月日,レース番号,枠番,馬番,馬名,性,齢,生年月日,父馬名,母馬名,母父馬名,'
    '騎手名,騎手所属,負担重量,調教師,調教師所属,馬体重,馬体重増減,全成績,'
    'ダート左成績,ダート右成績,当競馬場成績,うち当距離成績,最高タイム,最高タイム良馬場,'
    '着順,タイム,着差,上がり3F,人気'
)

HORSELIST = HORSELIST_HEADER + '\n' + '\n'.join([
    # 当日（着順は空＝これから走る）
    '笠松,20260812,11,1,1,チホウノホシ,牡,6,20200410,トーセンラー,ハハウマ,テイオー,'
    'ジョッキーA,愛知,57,トレーナーA,愛知,,,9-4-6-15,0-0-0-5,9-4-6-10,1-0-0-1,0-0-0-0,,,'
    ',,,,',
    '笠松,20260812,11,2,2,エンカイノウマ,牡,5,20210301,サウスポー,ハハニ,テイオー,'
    'ジョッキーB,JRA,56,トレーナーB,JRA,,,5-2-1-3,3-1-0-1,2-1-1-2,0-0-0-0,0-0-0-0,,,'
    ',,,,',
    '笠松,20260812,11,3,3,ヤスミアケ,牝,4,20220505,ノーザン,ハハサン,テイオー,'
    'ジョッキーC,笠松,54,トレーナーC,笠松,,,2-0-1-8,1-0-0-4,1-0-1-4,0-0-0-0,0-0-0-0,,,'
    ',,,,',
    # 過去（着順が入っている＝近走の材料）
    '笠松,20260720,12,5,5,チホウノホシ,牡,6,20200410,トーセンラー,ハハウマ,テイオー,'
    'ジョッキーA,愛知,56,トレーナーA,愛知,455,+2,8-4-6-15,0-0-0-5,8-4-6-10,1-0-0-1,0-0-0-0,,,'
    '1,1284,,38.7,1',
    # 同名の別馬。生年月日で区別できなければ、この着順が混ざる。
    '浦和,20260715,10,1,1,チホウノホシ,牝,3,20230101,ベツノチチ,ベツノハハ,ベツノボブ,'
    'ジョッキーZ,浦和,54,トレーナーZ,浦和,430,0,0-0-0-4,0-0-0-4,0-0-0-0,0-0-0-0,0-0-0-0,,,'
    '8,1300,,41.0,7',
])

ODDS_HEADER = '競馬場,競走年月日,レース番号,賭式,番号1,番号2,番号3,オッズ,オッズ（最大）,人気'

ODDS = ODDS_HEADER + '\n' + '\n'.join([
    '笠松,20260812,11,単勝,1,,,4.2,,2',
    '笠松,20260812,11,単勝,2,,,2.1,,1',
    '笠松,20260812,11,単勝,3,,,18.0,,5',
    '笠松,20260812,11,複勝,1,,,1.8,3.0,2',
    '笠松,20260812,11,馬複,1,2,,11.4,,3',
    '笠松,20260812,11,ワイド,1,2,,4.5,5.2,3',
    '笠松,20260812,11,馬単,2,1,,19.8,,4',
    '笠松,20260812,11,馬単,1,2,,28.0,,9',
    '笠松,20260812,11,３連複,1,2,3,33.8,,6',
    '笠松,20260812,11,３連単,1,2,3,320.9,,40',
    # 別レースの行が混ざっても引っ張られないこと
    '浦和,20260812,11,単勝,1,,,3.3,,1',
])


def rows(text):
    import csv
    return list(csv.DictReader(io.StringIO(text)))


def zip_of(**files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, text in files.items():
            zf.writestr(name, '﻿' + text)
    return buf.getvalue()


# ----------------------------------------------------------------------
# 取得層
# ----------------------------------------------------------------------

def test_ZIPの中のCSVをBOM付きで読める():
    data = zip_of(**{'20260812_racelist.csv': RACELIST})
    tables = nar.read_zip(data)
    parsed = tables['20260812_racelist.csv']
    # BOM を取りこぼすと先頭列の名前が '﻿競馬場' になって全部引けなくなる
    assert parsed[0]['競馬場'] == '笠松'


def test_ZIPでないものを黙って受け取らない():
    with pytest.raises(nar.NarError):
        nar.read_zip('<html>メンテナンス中</html>'.encode('utf-8'))


def test_月次のオッズは3分割されているので連結する():
    data = zip_of(**{'202608_01_odds.csv': ODDS,
                     '202608_02_odds.csv': ODDS_HEADER + '\n笠松,20260812,11,単勝,4,,,9.9,,4'})
    assert len(nar._pick(nar.read_zip(data), 'odds')) == len(rows(ODDS)) + 1


# ----------------------------------------------------------------------
# race_id
# ----------------------------------------------------------------------

def test_race_idは日付と場とRから組む():
    assert nar.race_id('笠松', '20260812', 11) == '202608124711'
    assert nar.split_race_id('202608124711') == ('20260812', '笠松', 11)


def test_race_idはbetsの契約を満たす():
    race = bets.RaceBets(
        race_id=nar.race_id('浦和', '20260812', 11), name='テスト', start_time='18:45',
        marks=[{'mark': '◎', 'umaban': 1}], bets=[bets.Bet('単勝', [1])], org='nar')
    assert race.org == 'nar'


def test_知らない主催者は弾く():
    with pytest.raises(bets.BetsError):
        bets.RaceBets(race_id='202608124711', name='x', start_time='18:45',
                      marks=[], bets=[], org='kaigai')


def test_貼り付け経路は9から10桁目で主催者を見分ける():
    # 中央はそこが開催日（01〜12）、地方は場コード（30以上）。範囲が重ならない。
    import make_bets
    assert make_bets._guess_org('202608124711') == 'nar'   # 笠松
    assert make_bets._guess_org('202601020811') == 'jra'   # 札幌・8日目
    assert make_bets._guess_org('202605010512') == 'jra'   # 東京・5日目


def test_地方のrace_idを中央として読んで別の場にしない():
    # 202608124711 を中央の形式で読むと [4:6]='08' で**京都**になる。
    # 黙って別の場が表示されるのがいちばん危ない形。
    import make_bets
    race = make_bets.parse_block(
        '202608124711 16:35 くろゆり賞 A 35%\n◎1 ○2\n馬連 1-2 100')
    assert race.org == 'nar'
    assert race.venue == '笠松'
    assert race.race_no == 11


def test_主催者は書いてあれば推測より優先する():
    import make_bets
    race = make_bets.parse_block(
        'org:nar 202608124211 18:45 ヴィーナスSS B 20%\n◎5 ○7\nワイド 5-7 100')
    assert race.org == 'nar'
    assert race.venue == '浦和'


def test_orgが無い過去の買い目は中央として読む():
    sheet = bets.parse_sheet({
        'date': '2026-08-02',
        'races': [{'race_id': '202609030811', 'start_time': '15:45',
                   'marks': [], 'bets': []}],
    })
    assert sheet.races[0].org == 'jra'


# ----------------------------------------------------------------------
# レース一覧
# ----------------------------------------------------------------------

def test_ばんえいは最初から落とす():
    races = nar.parse_races(rows(RACELIST), date(2026, 8, 12))
    assert '帯広ば' not in {r['venue'] for r in races}


def test_重賞と準重賞だけを候補にする():
    races = nar.parse_races(rows(RACELIST), date(2026, 8, 12))
    names = [r['name'] for r in nar.graded(races)]
    assert names == ['テスト記念', 'テストスプリント']


def test_発走時刻は夜まである():
    races = nar.parse_races(rows(RACELIST), date(2026, 8, 12))
    assert [r['start_time'] for r in nar.graded(races)] == ['16:35', '18:45']


def test_日付で絞れる():
    assert nar.parse_races(rows(RACELIST), date(2026, 7, 20))[0]['venue'] == '笠松'


def test_南関東と園田はハロンタイムが収録されている():
    races = nar.parse_races(rows(RACELIST), date(2026, 8, 12))
    by_venue = {r['venue']: r['has_furlong_times'] for r in races}
    assert by_venue['浦和'] is True
    assert by_venue['笠松'] is False


# ----------------------------------------------------------------------
# 出馬表
# ----------------------------------------------------------------------

def test_出馬表から血統と各種成績が引ける():
    entries = nar.parse_entries(rows(HORSELIST), '202608124711')
    first = entries[0]
    assert first['name'] == 'チホウノホシ'
    assert first['sire'] == 'トーセンラー'
    assert first['broodmare_sire'] == 'テイオー'
    assert first['record_all']['starts'] == 34
    assert first['record_venue'] == {'1着': 1, '2着': 0, '3着': 0, '着外': 1, 'starts': 2}


def test_中央所属の馬が分かる():
    entries = nar.parse_entries(rows(HORSELIST), '202608124711')
    assert [e['belongs_jra'] for e in entries] == [False, True, False]


def test_馬体重が未発表なら0ではなくNone():
    # 発走の直前まで空。0kg と読み替えると第5章のダート馬体重の判断が壊れる。
    entries = nar.parse_entries(rows(HORSELIST), '202608124711')
    assert entries[0]['horse_weight'] is None


# ----------------------------------------------------------------------
# 近走の復元
# ----------------------------------------------------------------------

def test_近走を過去の出馬表から組み上げる():
    history = nar_card.build_history(rows(RACELIST), rows(HORSELIST))
    runs = history[('チホウノホシ', '20200410')]
    assert len(runs) == 1
    assert runs[0]['finish'] == 1
    assert runs[0]['race_name'] == '前走にあたるレース'
    assert runs[0]['distance'] == 1400


def test_同名の別馬の成績を貼らない():
    history = nar_card.build_history(rows(RACELIST), rows(HORSELIST))
    # 生年月日まで含めた鍵なので、3歳牝馬の8着は6歳牡馬の近走に入らない
    assert [r['finish'] for r in history[('チホウノホシ', '20230101')]] == [8]


def test_これから走るレースは近走に入れない():
    history = nar_card.build_history(rows(RACELIST), rows(HORSELIST))
    assert all(r['date'] != '20260812'
               for runs in history.values() for r in runs)


def test_通過順から脚質を出す():
    # 数字は馬番で、並び順が通過順位。「6,5-4,3,1」なら馬番6が先頭。
    race = rows(RACELIST)[4]
    assert nar_card.corner_positions(race, 6) == [1, 2, 2, 3]
    assert nar_card.corner_positions(race, 3) == [4, 5, 5, 4]


def test_通過順の区切り文字は順序に影響しない():
    # ',' '-' '=' と括弧は差の大きさを表すだけ。数字の並びだけを読む。
    race = rows(RACELIST)[4]
    assert nar_card.corner_positions(race, 1) == [5, 4, 4, 1]


def test_通過順に載っていない馬はNoneにする():
    # 競走中止など。0 や最下位で埋めると脚質を誤る。
    race = rows(RACELIST)[5]
    assert nar_card.corner_positions(race, 8) == [None, None, None, None]


def test_脚質は前半の位置で決める():
    assert nar_card.running_style([1, 1, 1, 1], 10) == '逃げ'
    assert nar_card.running_style([3, 2, 2, 1], 10) == '先行'
    assert nar_card.running_style([9, 4, 2, 1], 11) == '追い込み'
    assert nar_card.running_style([], 10) is None


# ----------------------------------------------------------------------
# 中央馬の「近走なし」を休み明けと読まない
# ----------------------------------------------------------------------

def _annotated():
    entries = nar.parse_entries(rows(HORSELIST), '202608124711')
    history = nar_card.build_history(rows(RACELIST), rows(HORSELIST))
    return {e['umaban']: e
            for e in nar_card.annotate(entries, history, date(2026, 8, 12), 1580)}


def test_中央馬の近走0走は休み明けではないと書く():
    entry = _annotated()[2]
    assert entry['recent_runs'] == []
    assert '中央' in entry['last_run']['note']
    assert '休み明けではない' in entry['last_run']['note']


def test_地方馬の近走0走は休み明けの候補として書く():
    entry = _annotated()[3]
    assert entry['recent_runs'] == []
    assert '出走記録なし' in entry['last_run']['note']
    assert '中央' not in entry['last_run']['note'][:6]


def test_近走がある馬は距離の増減と乗り替わりが出る():
    entry = _annotated()[1]
    assert entry['last_run']['finish'] == 1
    assert entry['last_run']['days_since'] == 23
    assert entry['last_run']['distance_change'] == 180     # 1400 → 1580
    assert entry['last_run']['jockey_change'] is False


# ----------------------------------------------------------------------
# オッズ
# ----------------------------------------------------------------------

def test_券種名を第13章の綴りに揃える():
    tables, _ = nar.parse_odds(rows(ODDS), '202608124711')
    assert set(tables) >= {'単勝', '複勝', '馬連', 'ワイド', '馬単', '3連複'}
    # 3連単は第13章で扱わないので拾わない
    assert '3連単' not in tables


def test_他のレースの行を引っ張らない():
    tables, _ = nar.parse_odds(rows(ODDS), '202608124711')
    assert nar.odds_for(tables, [1], '単勝') == 4.2


def test_馬単は着順を入れ替えない():
    tables, _ = nar.parse_odds(rows(ODDS), '202608124711')
    assert nar.odds_for(tables, [2, 1], '馬単') == 19.8
    assert nar.odds_for(tables, [1, 2], '馬単') == 28.0


def test_馬連とワイドは並べ替えて引ける():
    tables, _ = nar.parse_odds(rows(ODDS), '202608124711')
    assert nar.odds_for(tables, [2, 1], '馬連') == 11.4
    assert nar.odds_for(tables, [2, 1], 'ワイド') == 4.5


def test_複勝は下限を返す():
    # 期待値を低く見積もる側に倒す。規律の判定が甘くならないように。
    tables, _ = nar.parse_odds(rows(ODDS), '202608124711')
    assert nar.odds_for(tables, [1], '複勝') == 1.8


def _race():
    return bets.RaceBets(
        race_id='202608124711', name='テスト記念', start_time='16:35',
        marks=[{'mark': '◎', 'umaban': 1}, {'mark': '○', 'umaban': 2}],
        bets=[bets.Bet('馬連', [1, 2]), bets.Bet('単勝', [1])],
        confidence='A', subjective_hit_rate=0.35, org='nar')


def test_collectはoddsモジュールと同じ形を返す():
    bet_odds, win_table, meta = nar.collect(_race(), fetcher=lambda: rows(ODDS))
    assert bet_odds == [11.4, 4.2]
    assert win_table[2] == (2.1, 1)
    assert meta['status'] == 'middle'
    assert meta['errors'] == []


def test_発売前は0件ではなくyosoとして返す():
    # 当日ファイルは発売開始まで中身が空で降ってくる。これを「取得できた」と
    # 扱うと、オッズ無しのまま規律が通ってしまう。
    _, _, meta = nar.collect(_race(), fetcher=lambda: [])
    assert meta['status'] == 'yoso'


def test_取得に失敗したらstatusを立てずエラーを残す():
    def broken():
        raise nar.NarError('接続できません')
    bet_odds, _, meta = nar.collect(_race(), fetcher=broken)
    assert bet_odds == [None, None]
    assert meta['status'] is None
    assert meta['errors'] == ['接続できません']


# ----------------------------------------------------------------------
# カード
# ----------------------------------------------------------------------

def test_カードは重賞だけを候補にして出馬表を付ける():
    def fake(type_, month=None):
        return {'racelist': rows(RACELIST), 'horselist': rows(HORSELIST),
                'payback': []}

    # today を day と同じに固定し、当日ファイル経路（daily）を確実に通す。
    # 実行日の巡り合わせで daily と月次（monthly）のどちらを通るかが
    # 変わると、この fixture（月次だと同じ内容が6回重複する）では
    # 結果が変わってしまう。
    card = nar_card.build_card(date(2026, 8, 12), fetcher=fake,
                               today=date(2026, 8, 12))
    assert card['org'] == 'nar'
    assert card['race_count'] == 3          # ばんえいを除いた当日のレース数
    assert [r['name'] for r in card['races']] == ['テスト記念', 'テストスプリント']
    assert len(card['races'][0]['entries']) == 3
    assert card['races'][0]['jra_horses'] == [2]


def test_開催があって重賞が無い日は空のカードを書く():
    # **ファイルが無い状態は「取得できなかった」を意味させたい。**
    # 該当なしと取得失敗を同じ形にすると区別が付かなくなる。
    def fake(type_, month=None):
        return {'racelist': rows(RACELIST), 'horselist': rows(HORSELIST),
                'payback': []}

    card = nar_card.build_card(date(2026, 7, 20), fetcher=fake, today=date(2026, 9, 1))
    assert card['races'] == []
    assert card['race_count'] == 1


# ----------------------------------------------------------------------
# 確定結果（週次レビューの入力）
# ----------------------------------------------------------------------

PAYBACK_HEADER = (
    '競馬場,競走年月日,レース番号,レース名,単勝組番,単勝払戻金（円）,単勝人気,'
    '複勝組番1,複勝払戻金1（円）,複勝人気1,複勝組番2,複勝払戻金2（円）,複勝人気2,'
    '複勝組番3,複勝払戻金3（円）,複勝人気3,'
    '馬複組番1,馬複組番2,馬複払戻金（円）,馬複人気1,'
    '馬単組番1,馬単組番2,馬単払戻金（円）,馬単人気1,'
    'ワイド組番1馬番1,ワイド組番1馬番2,ワイド払戻金1（円）,ワイド人気1,'
    'ワイド組番2馬番1,ワイド組番2馬番2,ワイド払戻金2（円）,ワイド人気2,'
    'ワイド組番3馬番1,ワイド組番3馬番2,ワイド払戻金3（円）,ワイド人気3,'
    '３連複組番馬番1,３連複組番馬番2,３連複組番馬番3,３連複払戻金（円）,３連複人気,'
    '３連単組番馬番1,３連単組番馬番2,３連単組番馬番3,３連単払戻金（円）,３連単人気'
)

PAYBACK = PAYBACK_HEADER + '\n' + '\n'.join([
    '笠松,20260812,11,テスト記念,1,420,2,1,180,2,2,150,1,3,600,5,'
    '1,2,1140,3,1,2,2800,9,1,2,450,3,1,3,1600,12,2,3,1300,10,'
    '1,2,3,3380,6,1,2,3,32090,40',
    # **同着は行が増える形で表される**（2026-08 の実データで確認）。
    # 先頭行だけ見ると片方を落とす。
    '笠松,20260812,11,テスト記念,1,420,2,1,180,2,2,150,1,4,610,6,'
    '1,2,1140,3,1,2,2800,9,1,2,450,3,1,4,1700,13,2,4,1400,11,'
    '1,2,4,3400,7,1,2,4,32500,41',
])

RESULT_HORSELIST = HORSELIST_HEADER + '\n' + '\n'.join([
    '笠松,20260812,11,1,1,チホウノホシ,牡,6,20200410,トーセンラー,ハハウマ,テイオー,'
    'ジョッキーA,愛知,57,トレーナーA,愛知,456,+1,9-4-6-15,0-0-0-5,9-4-6-10,1-0-0-1,0-0-0-0,,,'
    '1,1420,,38.5,2',
    '笠松,20260812,11,2,2,エンカイノウマ,牡,5,20210301,サウスポー,ハハニ,テイオー,'
    'ジョッキーB,JRA,56,トレーナーB,JRA,480,-2,5-2-1-3,3-1-0-1,2-1-1-2,0-0-0-0,0-0-0-0,,,'
    '2,1421,,38.2,1',
    '笠松,20260812,11,3,3,ヤスミアケ,牝,4,20220505,ノーザン,ハハサン,テイオー,'
    'ジョッキーC,笠松,54,トレーナーC,笠松,442,0,2-0-1-8,1-0-0-4,1-0-1-4,0-0-0-0,0-0-0-0,,,'
    '3,1423,,38.9,5',
    # 競走中止。着順が数字にならないので集計から落とす（0着にしない）。
    '笠松,20260812,11,4,4,チュウシウマ,牡,5,20210909,ノーザン,ハハヨン,テイオー,'
    'ジョッキーD,笠松,56,トレーナーD,笠松,470,+4,1-1-1-9,0-0-0-3,1-1-1-6,0-0-0-0,0-0-0-0,,,'
    '中,,,,8',
])


def test_確定着順を読む():
    order = nar.parse_finishing_order(rows(RESULT_HORSELIST), '202608124711')
    assert [(h['rank'], h['umaban']) for h in order] == [(1, 1), (2, 2), (3, 3)]
    assert order[0]['ninki'] == 2


def test_競走中止は0着にしない():
    order = nar.parse_finishing_order(rows(RESULT_HORSELIST), '202608124711')
    assert 4 not in {h['umaban'] for h in order}


def test_払戻をresultsと同じ形にする():
    payouts = nar.parse_payouts(rows(PAYBACK), '202608124711')
    assert payouts['単勝'] == [{'combination': [1], 'yen': 420}]
    assert {'combination': [1, 2], 'yen': 1140} in payouts['馬連']
    assert {'combination': [1, 2, 3], 'yen': 3380} in payouts['3連複']


def test_同着は複数行にまたがるので全部たたむ():
    payouts = nar.parse_payouts(rows(PAYBACK), '202608124711')
    wide = {tuple(e['combination']) for e in payouts['ワイド']}
    assert wide == {(1, 2), (1, 3), (2, 3), (1, 4), (2, 4)}
    # 両方の行に同じ値で入っている組を二重に数えない
    assert len([e for e in payouts['ワイド'] if e['combination'] == [1, 2]]) == 1


def test_精算まで中央と同じコードが通る():
    import results as results_module
    result = {
        'race_id': '202608124711',
        'finishing_order': nar.parse_finishing_order(rows(RESULT_HORSELIST), '202608124711'),
        'payouts': nar.parse_payouts(rows(PAYBACK), '202608124711'),
    }
    race = bets.RaceBets(
        race_id='202608124711', name='テスト記念', start_time='16:35',
        marks=[{'mark': '◎', 'umaban': 1}],
        bets=[bets.Bet('単勝', [1]), bets.Bet('馬連', [1, 2]), bets.Bet('馬連', [3, 4])],
        org='nar')
    settled = results_module.settle(race, result)
    assert settled['staked'] == 300
    assert settled['returned'] == 420 + 1140
    assert [b['hit'] for b in settled['bets']] == [True, True, False]


def test_馬単は着順の向きを守る():
    payouts = nar.parse_payouts(rows(PAYBACK), '202608124711')
    import results as results_module
    assert results_module.payout_for(payouts, '馬単', [1, 2]) == 2800
    assert results_module.payout_for(payouts, '馬単', [2, 1]) == 0


def test_データに無い日はカードを書かない():
    def fake(type_, month=None):
        return {'racelist': rows(RACELIST), 'horselist': [], 'payback': []}

    with pytest.raises(nar_card.CardError):
        nar_card.build_card(date(2026, 1, 1), fetcher=fake, today=date(2026, 9, 1))
