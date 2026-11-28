# -*- coding: utf-8 -*-
"""テスト全体の共通設定。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import form  # noqa: E402


@pytest.fixture(autouse=True)
def _no_live_entries(monkeypatch):
    """直前検算は毎回出馬表（馬体重）を引く（2026-10-08〜）。テストから
    netkeiba に繋がないよう、既定では「馬体重未発表」の空を返す。"""
    monkeypatch.setattr(form, 'fetch_entries', lambda race_id, opener=None: {})


@pytest.fixture(autouse=True)
def _no_live_jra_official(monkeypatch):
    """直前検算は単勝・複勝と馬体重を JRA 公式から読む（2026-11-28〜）。テストから
    JRA 公式に繋がないよう、既定では「取れなかった」扱いにして netkeiba 側（上で空）に回す。"""
    import jra_official

    def _fail(race_id, opener=None):
        raise jra_official.JraError('テストでは取りに行かない')
    monkeypatch.setattr(jra_official, 'fetch_win', _fail)
    monkeypatch.setattr(jra_official, '_WIN_ONCE', {})
    monkeypatch.setattr(jra_official, '_DOWN', [])


@pytest.fixture(autouse=True)
def _no_live_nar_odds(monkeypatch):
    """地方の確定単勝オッズ（月次のオッズZIP、約100MB）をテストから取りに行かない。
    既定では「取れなかった」扱いにする（2026-11-03〜）。"""
    import nar

    def _fail(month, opener=None):
        raise nar.NarError('テストでは取りに行かない')
    monkeypatch.setattr(nar, 'monthly_win_odds', _fail)
