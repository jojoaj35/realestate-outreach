import datetime as dt
import json

import numpy as np
import pytest

import templates
from config import settings
from ig_corpus import IgCorpus
from ig_rank import build_profile_doc, should_queue
from ig_sources import discovery_hashtags, load_city_config, search_queries
from ig_store import IgStore, normalize_handle, profile_to_row


def test_normalize_handle():
    assert normalize_handle("@Jane_Realtor") == "jane_realtor"
    assert normalize_handle("https://instagram.com/Jane_Realtor/") == "jane_realtor"
    assert normalize_handle("") == ""


def test_profile_to_row():
    row = profile_to_row({
        "ig_handle": "@test_agent",
        "display_name": "Test Agent",
        "city": "San Antonio",
        "bio": "SA realtor",
        "source": "search:realtor",
        "match_score": 0.91,
        "city_score": 0.78,
        "rank_reasons": ["realtor 0.91", "city 0.78"],
    })
    assert row["ig_handle"] == "test_agent"
    assert row["status"] == "queued"
    assert row["match_score"] == 0.91
    assert row["city_score"] == 0.78
    assert "realtor 0.91" in row["rank_reasons"]
    assert "instagram.com/test_agent" in row["profile_url"]


def test_ig_store_upsert(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr("ig_store.DATA_DIR", data_dir)
    store = IgStore()
    added, updated = store.upsert_profiles([{
        "ig_handle": "agent_one",
        "display_name": "Agent One",
        "city": "Austin",
        "bio": "Austin realtor",
        "source": "hashtag:test",
        "match_score": 0.88,
        "city_score": 0.72,
        "rank_reasons": '["realtor 0.88"]',
    }])
    assert added == 1
    assert updated == 0
    assert store.get("agent_one")["display_name"] == "Agent One"
    assert float(store.get("agent_one")["match_score"]) == pytest.approx(0.88)

    added2, updated2 = store.upsert_profiles([{
        "ig_handle": "agent_one",
        "display_name": "Agent One Updated",
        "bio": "Austin realtor",
        "match_score": 0.90,
    }])
    assert added2 == 0
    assert updated2 == 1
    assert store.get("agent_one")["display_name"] == "Agent One Updated"
    assert float(store.get("agent_one")["match_score"]) == pytest.approx(0.90)


def test_ig_dnc(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr("ig_store.DATA_DIR", data_dir)
    store = IgStore()
    store.upsert_profiles([{"ig_handle": "blocked", "display_name": "Blocked"}])
    store.add_dnc("@blocked", reason="asked to stop")
    assert store.is_dnc("blocked")
    assert store.get("blocked")["status"] == "dnc"


def test_every_ig_template_has_required_fields():
    for t in templates.IG_TEMPLATES:
        for field in ("{greeting}", "{sender}", "{offer}", "{phone}", "{ig_opt_out}"):
            assert field in t


def _mock_rank(monkeypatch, *, match=0.9, city=0.8, exclude=0.2):
    def fake_score(profile, city_name, doc=None):
        return {
            "match_score": match,
            "city_score": city,
            "exclude_score": exclude,
            "lender_score": 0.1,
            "photo_score": 0.1,
            "rank_reasons": [f"realtor {match:.2f}", f"city {city:.2f}"],
            "profile_doc": doc or build_profile_doc(profile, city_name),
            "embedding": np.ones(512, dtype=np.float32) / np.sqrt(512),
        }

    monkeypatch.setattr("ig_rank.score_profile", fake_score)


def test_should_queue_skips_already_following(monkeypatch):
    profile = {
        "ig_handle": "some_realtor",
        "display_name": "Some Realtor",
        "bio": "San Antonio realtor",
        "already_following": True,
    }
    _mock_rank(monkeypatch)
    monkeypatch.setattr(settings, "ig_skip_already_following", True)
    ok, _ = should_queue(profile, "San Antonio")
    assert ok is False

    profile["already_following"] = False
    ok, result = should_queue(profile, "San Antonio")
    assert ok is True
    assert result["match_score"] == 0.9

    monkeypatch.setattr(settings, "ig_skip_already_following", False)
    profile["already_following"] = True
    ok, _ = should_queue(profile, "San Antonio")
    assert ok is True


def test_should_queue_thresholds(monkeypatch):
    profile = {
        "ig_handle": "agent",
        "display_name": "Agent",
        "bio": "Realtor in San Antonio",
    }
    monkeypatch.setattr(settings, "ig_realtor_score_threshold", 0.55)
    monkeypatch.setattr(settings, "ig_city_score_threshold", 0.45)
    monkeypatch.setattr(settings, "ig_exclude_score_threshold", 0.60)

    _mock_rank(monkeypatch, match=0.40, city=0.80, exclude=0.10)
    ok, result = should_queue(profile, "San Antonio")
    assert ok is False
    assert any("low realtor score" in r for r in result["rank_reasons"])

    _mock_rank(monkeypatch, match=0.80, city=0.30, exclude=0.10)
    ok, result = should_queue(profile, "San Antonio", require_city=True)
    assert ok is False
    assert any("low city score" in r for r in result["rank_reasons"])

    _mock_rank(monkeypatch, match=0.80, city=0.80, exclude=0.75)
    ok, result = should_queue(profile, "San Antonio")
    assert ok is False
    assert any("exclusion" in r for r in result["rank_reasons"])

    _mock_rank(monkeypatch, match=0.80, city=0.80, exclude=0.20)
    ok, _ = should_queue(profile, "San Antonio")
    assert ok is True


def test_build_profile_doc_includes_captions():
    doc = build_profile_doc({
        "ig_handle": "satx_agent",
        "display_name": "SATX Agent",
        "bio": "KW Heritage",
        "recent_post_captions": ["Just listed in Stone Oak!", "Open house SATX"],
        "link_in_bio_title": "My Team",
        "link_in_bio_url": "https://example.com",
    }, "San Antonio")
    assert "Stone Oak" in doc
    assert "City target: San Antonio" in doc
    assert "My Team" in doc


def test_city_config_search_queries():
    cfg = load_city_config("San Antonio")
    assert "satx" in cfg["metro_aliases"]
    queries = search_queries("San Antonio")
    assert len(queries) >= 15
    assert any("realtor" in q.lower() for q in queries)
    assert any("Stone Oak" in q for q in queries)

    tags = discovery_hashtags("San Antonio")
    assert "satxrealtor" in tags


def test_semantic_duplicate(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr("ig_corpus.DATA_DIR", data_dir)
    monkeypatch.setattr("ig_store.DATA_DIR", data_dir)

    store = IgStore()
    store.add_dnc("sent_agent", reason="test")
    corpus = IgCorpus()

    anchor = np.zeros(512, dtype=np.float32)
    anchor[0] = 1.0
    near = anchor.copy()
    far = np.zeros(512, dtype=np.float32)
    far[1] = 1.0

    corpus.record(
        "sent_agent",
        "doc",
        {"embedding": anchor, "match_score": 0.5, "city_score": 0.5, "exclude_score": 0.5},
        ["test"],
        exclude_anchor=True,
    )

    is_dup, handle = corpus.is_semantic_duplicate(near, store=store, threshold=0.92)
    assert is_dup is True
    assert handle == "sent_agent"

    is_dup, _ = corpus.is_semantic_duplicate(far, store=store, threshold=0.92)
    assert is_dup is False


def test_hybrid_clip_only_without_api_key(monkeypatch):
    from ig_llm import hybrid_should_queue

    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "ig_llm_enabled", True)
    _mock_rank(monkeypatch, match=0.80, city=0.80, exclude=0.20)
    profile = {"ig_handle": "agent", "display_name": "Agent", "bio": "SA realtor"}
    ok, result, tier = hybrid_should_queue(profile, "San Antonio")
    assert ok is True
    assert tier == "clip"


def test_hybrid_llm_borderline_accepts(monkeypatch):
    from ig_llm import hybrid_should_queue

    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "ig_llm_enabled", True)
    monkeypatch.setattr(settings, "ig_llm_mode", "borderline")
    _mock_rank(monkeypatch, match=0.50, city=0.50, exclude=0.30)

    def fake_classify(items, city, require_city=True):
        h = items[0]["ig_handle"]
        return {h: {
            "ig_handle": h,
            "queue": True,
            "is_realtor": True,
            "serves_target_city": True,
            "confidence": 0.9,
            "reason": "Listing agent in SATX",
        }}

    monkeypatch.setattr("ig_llm.llm_available", lambda: True)
    monkeypatch.setattr("ig_llm._client", lambda: object())
    monkeypatch.setattr("ig_llm.classify_profiles", fake_classify)

    profile = {"ig_handle": "satx_agent", "display_name": "SATX Agent", "bio": "Homes"}
    ok, result, tier = hybrid_should_queue(profile, "San Antonio")
    assert ok is True
    assert tier == "llm"
    assert any("LLM" in str(r) for r in result["rank_reasons"])


def test_hybrid_clip_auto_reject(monkeypatch):
    from ig_llm import hybrid_should_queue

    monkeypatch.setattr("ig_llm.llm_available", lambda: True)
    _mock_rank(monkeypatch, match=0.30, city=0.80, exclude=0.20)

    profile = {"ig_handle": "lender", "display_name": "Lender", "bio": "Mortgage"}
    ok, result, tier = hybrid_should_queue(profile, "San Antonio")
    assert ok is False
    assert tier == "clip_reject"


def test_agent_queue_requires_inspect(monkeypatch, tmp_path):
    from ig_agent import AgentContext, execute_tool

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr("ig_store.DATA_DIR", data_dir)
    monkeypatch.setattr("ig_corpus.DATA_DIR", data_dir)

    store = IgStore()
    corpus = IgCorpus()
    ctx = AgentContext(
        city="San Antonio",
        max_results=5,
        require_city=True,
        page=None,
        store=store,
        corpus=corpus,
    )
    ctx.inspected["good_agent"] = {
        "handle": "good_agent",
        "skip": False,
        "_profile": {
            "ig_handle": "good_agent",
            "display_name": "Good Agent",
            "bio": "SA Realtor",
            "city": "San Antonio",
        },
        "_rank": {
            "profile_doc": "doc",
            "match_score": 0.8,
            "city_score": 0.7,
            "exclude_score": 0.2,
            "embedding": np.ones(512, dtype=np.float32) / np.sqrt(512),
        },
    }
    result = json.loads(execute_tool(ctx, "queue_realtor", {
        "handle": "good_agent",
        "reason": "Residential listing agent in SATX",
        "confidence": 0.92,
    }))
    assert result["ok"] is True
    assert len(ctx.matched) == 1
    assert ctx.matched[0]["ig_handle"] == "good_agent"


def test_render_instagram_fills_placeholders():
    row = {"display_name": "Jane Smith", "ig_handle": "jane_sa"}
    msg = templates.render_instagram(row, seed=1, now=dt.datetime(2026, 5, 28, 14, 0))
    assert "Jane" in msg
    assert settings.contact_phone in msg
    assert settings.special_offer in msg
    assert "{" not in msg
