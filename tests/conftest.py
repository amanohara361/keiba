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
