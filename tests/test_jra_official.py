#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JRA 公式の読み取り（jra_official.py）。ネットワークには接続しない。

資料は 2026-11-14 に取った実物を、読み取りに使う部分だけ切り詰めたもの（Shift_JIS のまま）。
"""

import io
import os
import sys
import urllib.parse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import jra_official  # noqa: E402

FIX = os.path.join(ROOT, 'tests', 'fixtures')


def raw(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


def text(name):
    return raw(name).decode('shift_jis', errors='replace')


def test_単勝と複勝と騎手を読む():
    got = jra_official.parse_win(text('jra_odds_tanpuku_202605050311.html'))   # 武蔵野S
    assert got['updated'] == '10時38分'
    assert got['start'] == '15時30分'
    assert len(got['horses']) == 16
    h = got['horses'][1]
    assert h['name'] == 'ダノンフィーゴ'
    assert h['win'] == 17.1
    assert (h['place_min'], h['place_max']) == (4.8, 6.6)
    assert h['sex_age'] == '牡4'
    assert h['kinryo'] == 57.0
    assert h['jockey'] == 'T.マーカンド'
    assert h['trainer'] == '友道 康夫'
    assert h['weight_text'] is None          # 馬体重は発表前
    assert got['horses'][16]['jockey'] == 'C.ルメール'


def test_オッズが数字でない馬はNoneにして元の文字を残す():
    page = text('jra_odds_tanpuku_202605050311.html').replace(
        '<td class="odds_tan">17.1</td>', '<td class="odds_tan">取消</td>', 1)
    h = jra_official.parse_win(page)['horses'][1]
    assert h['win'] is None and h['win_text'] == '取消'


def test_表が無ければ書き方の変化として止める():
    with pytest.raises(jra_official.JraError, match='書き方が変わった'):
        jra_official.parse_win('<html>メンテナンス中</html>')


def test_メニューから開催場とレースのリンクを拾う():
    menu = text('jra_odds_menu_20261114.html')
    venues = jra_official.parse_menu(menu)
    assert venues[('03', '2026', '03', '03')] == 'pw15orl00032026030320261114/53'   # 福島
    assert ('08', '2026', '05', '04') in venues                                    # 翌日の京都
    races = jra_official.parse_races(menu)
    assert races['202605050311'] == 'pw151ouS305202605031120261114Z/E1'


def test_開催場のページから全レースを拾う():
    races = jra_official.parse_races(text('jra_odds_venue_fukushima_20261114.html'))
    assert sorted(races) == [f'2026030303{r:02d}' for r in range(1, 13)]
    assert races['202603030311'] == 'pw151ouS303202603031120261114Z/EB'


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_opener(pages):
    sent = []

    def opener(request, timeout=None):
        form = urllib.parse.parse_qs(request.data.decode('ascii'))
        value = (form.get('cname') or form.get('CNAME'))[0]
        sent.append((request.full_url, value, request.get_header('User-agent')))
        return FakeResponse(pages[value])
    opener.sent = sent
    return opener


def test_メニューに無いレースは開催場のページを経由して拾う():
    opener = fake_opener({
        jra_official.ODDS_MENU: raw('jra_odds_menu_20261114.html'),
        'pw15orl00032026030320261114/53': raw('jra_odds_venue_fukushima_20261114.html'),
    })
    assert jra_official.race_cname('202603030311', opener=opener) == 'pw151ouS303202603031120261114Z/EB'
    assert [v for _, v, _ in opener.sent] == [jra_official.ODDS_MENU, 'pw15orl00032026030320261114/53']
    assert all(ua == jra_official.UA for _, _, ua in opener.sent)   # 既定の名乗りは 403 になる


def test_開催の無いレースは理由を言って止める():
    opener = fake_opener({jra_official.ODDS_MENU: raw('jra_odds_menu_20261114.html')})
    with pytest.raises(jra_official.JraError, match='メニューにありません'):
        jra_official.race_cname('202609050311', opener=opener)


def test_馬場発表を読む():
    got = jra_official.parse_baba(raw('jra_baba_20261114.json').decode('shift_jis'))
    assert [(v['date'], v['venue']) for v in got] == [
        ('20261114', '東京'), ('20261114', '京都'), ('20261114', '福島'), ('20261115', '京都')]
    kyoto = got[1]
    assert (kyoto['weather'], kyoto['going_turf'], kyoto['going_dirt']) == ('曇', '良', '良')
    assert kyoto['changes'] == {'発走時刻変更': 2}
    assert got[0]['changes'] == {}
    nxt = got[3]                                   # 翌日分は未発表
    assert (nxt['weather'], nxt['going_turf'], nxt['going_dirt']) == (None, None, None)


def test_メニューと開催場のページは1回の実行で控えて使い回す(monkeypatch):
    posts = []
    pages = {jra_official.ODDS_MENU: 'MENU', 'VENUE': 'VENUE_PAGE'}

    def fake_post(page, value, field='cname', timeout=20, opener=None):
        posts.append(value)
        return pages.get(value, 'ODDS')
    monkeypatch.setattr(jra_official, '_post', fake_post)
    monkeypatch.setattr(jra_official, '_LINK_PAGES', {})
    assert jra_official._link_page(jra_official.ODDS_MENU) == 'MENU'
    assert jra_official._link_page(jra_official.ODDS_MENU) == 'MENU'
    assert jra_official._link_page('VENUE') == 'VENUE_PAGE'
    assert posts == [jra_official.ODDS_MENU, 'VENUE']


def test_通信の失敗はJraFetchErrorで伝える():
    def opener(request, timeout=20):
        raise OSError('timed out')
    with pytest.raises(jra_official.JraFetchError):
        jra_official._post('accessO.html', 'x', opener=opener)


# ----------------------------------------------------------------------
# 馬体重（2026-11-28〜）
# ----------------------------------------------------------------------

@pytest.mark.parametrize('text,expected', [
    ('490 (+6)', (490, 6)),
    ('486 (-3)', (486, -3)),
    ('488(+14)', (488, 14)),
    ('500 (0)', (500, 0)),
    ('500 (±0)', (500, 0)),
    ('470', (470, None)),          # 前走比が無い（初出走など）
    (None, (None, None)),          # 発表前
    ('', (None, None)),
])
def test_馬体重を読む(text, expected):
    assert jra_official.parse_weight(text) == expected


def test_単勝複勝ページから直前検算用の出馬表を作る():
    parsed = {'horses': {
        2: {'name': 'ミッキーダムール', 'sex_age': '牡3', 'weight_text': '490 (+6)',
            'kinryo': 57.0, 'jockey': '騎手A'},
        3: {'name': 'タッセルノット', 'sex_age': '牝3', 'weight_text': None,
            'kinryo': 55.0, 'jockey': '騎手B'},
    }}
    got = jra_official.entries_from_win(parsed)
    assert got[2]['weight'] == 490 and got[2]['weight_diff'] == 6
    assert got[2]['age'] == 3 and got[2]['sex'] == '牡'
    assert got[3]['weight'] is None and got[3]['weight_diff'] is None   # 推測で埋めない
    assert got[3]['sex'] == '牝'


def test_同じレースは1回の実行で1度だけ取りに行く(monkeypatch):
    calls = []

    def fake(race_id, opener=None):
        calls.append(race_id)
        return {'horses': {}}
    monkeypatch.setattr(jra_official, 'fetch_win', fake)
    jra_official.fetch_win_once('202605050708')
    jra_official.fetch_win_once('202605050708')
    jra_official.fetch_win_once('202605050710')
    assert calls == ['202605050708', '202605050710']


def test_通信が届かなければその実行の残りでは叩かない(monkeypatch):
    calls = []

    def unreachable(race_id, opener=None):
        calls.append(race_id)
        raise jra_official.JraFetchError('timed out')
    monkeypatch.setattr(jra_official, 'fetch_win', unreachable)
    for race_id in ('202605050708', '202605050710'):
        with pytest.raises(jra_official.JraError):
            jra_official.fetch_win_once(race_id)
    assert calls == ['202605050708']


def test_ページの中身の問題では止めない(monkeypatch):
    """開催がメニューに無い等はそのレースだけの話。他のレースは JRA 公式を読みに行く。"""
    calls = []

    def missing(race_id, opener=None):
        calls.append(race_id)
        raise jra_official.JraError('開催がオッズのメニューにありません')
    monkeypatch.setattr(jra_official, 'fetch_win', missing)
    for race_id in ('202605050708', '202605050710'):
        with pytest.raises(jra_official.JraError):
            jra_official.fetch_win_once(race_id)
    assert calls == ['202605050708', '202605050710']
