#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当日の結果ページ（race.netkeiba.com）から昼のバイアスを測る経路。ネットワークには接続しない。"""

import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import jra_bias  # noqa: E402

FIXTURE = os.path.join(ROOT, 'tests', 'fixture_nkresult_20260927_202606040902.html')
JST = timezone(timedelta(hours=9))


def parsed():
    with open(FIXTURE, encoding='utf-8') as f:
        return jra_bias.parse_nk_result(f.read(), '202606040902')


def test_レース条件と馬場の略記を読む():
    r = parsed()
    assert (r['R'], r['馬場'], r['距離'], r['馬場状態'], r['頭数']) == (2, '芝', 2000, '稍重', 12)
    assert (r['勝ちタイム'], r['上がり3F'], r['前半3F'], r['後半3F']) == (125.3, 37.1, 37.5, 37.8)


def test_当日空欄の通過順をコーナー通過順位表から復元する():
    horses = {h['馬番']: h for h in parsed()['horses']}
    assert horses[7]['通過'] == '1-1-1-1'       # 逃げて6着
    assert horses[2]['通過'] == '8-7-6-5'       # 勝ち馬
    assert horses[9]['通過'] == '11-11-11-12'


def test_脚質まで既存の判定に乗る():
    r = jra_bias.attach_legs(parsed())
    legs = {h['馬番']: h['_leg'] for h in r['horses']}
    assert legs[7] == '逃'
    assert legs[6] == '先'
    assert legs[9] == '追'


def test_人気薄のオッズも読む():
    horses = {h['馬番']: h for h in parsed()['horses']}
    assert horses[1]['単勝'] == '440.5'      # class の無い span になる
    assert horses[2]['単勝'] == '2.6'


def test_未確定のページは読まない():
    assert jra_bias.parse_nk_result('<html><div class="RaceData01">芝2000m</div></html>', 'x') is None


def test_発走15分前後で取りに行くかを分ける(monkeypatch):
    monkeypatch.setattr(jra_bias, 'card_races', lambda d: [
        ('202606040901', '中山', '10:00'), ('202606040902', '中山', '10:35')])
    fetched = []

    def fake(url):
        fetched.append(url)
        with open(FIXTURE, encoding='utf-8') as f:
            return f.read()

    monkeypatch.setattr(jra_bias, 'fetch_html_utf8', fake)
    monkeypatch.setattr(jra_bias.time, 'sleep', lambda s: None)
    monkeypatch.setattr(jra_bias, 'DATA', os.path.join(ROOT, 'tests', '__pycache__', 'live_tmp'))
    jra_bias.do_live('20260927', 'all', now=datetime(2026, 9, 27, 10, 40, tzinfo=JST))
    assert [u[-12:] for u in fetched] == ['202606040901']


def _fixture():
    with open(FIXTURE, encoding='utf-8') as f:
        return f.read()


def test_live_は払戻も保存する(monkeypatch, tmp_path):
    import json
    monkeypatch.setattr(jra_bias, 'card_races', lambda d: [('202606040902', '中山', '10:35')])
    monkeypatch.setattr(jra_bias, 'fetch_html_utf8', lambda url: _fixture())
    monkeypatch.setattr(jra_bias.time, 'sleep', lambda s: None)
    monkeypatch.setattr(jra_bias, 'DATA', str(tmp_path))
    jra_bias.do_live('20260927', 'all', now=datetime(2026, 9, 27, 12, 0, tzinfo=JST))
    saved = json.loads((tmp_path / '20260927_中山.json').read_text(encoding='utf-8'))
    pay = saved[0]['払戻']
    assert pay['単勝'] == [{'combination': [2], 'yen': 260}]
    assert set(pay) >= {'複勝', '馬連', 'ワイド', '馬単', '3連複', '3連単'}


def test_payouts_は払戻の無いレースだけ足す(tmp_path):
    import json
    path = tmp_path / '20260927_中山.json'
    path.write_text(json.dumps([
        {'race_id': '202606040902', 'horses': [], '通過': 'x'},
        {'race_id': '202606040903', 'horses': [], '払戻': {'単勝': [{'combination': [1], 'yen': 100}]}},
    ], ensure_ascii=False), encoding='utf-8')
    fetched = []

    def fake(rid):
        fetched.append(rid)
        return _fixture()

    assert jra_bias.do_payouts(limit=10, data_dir=str(tmp_path), fetcher=fake, wait=0) == 1
    assert fetched == ['202606040902']
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved[0]['払戻']['単勝'][0]['yen'] == 260
    assert saved[0]['通過'] == 'x'                       # 他の項目は触らない
    assert saved[1]['払戻']['単勝'][0]['yen'] == 100      # 既にあるものは取り直さない
