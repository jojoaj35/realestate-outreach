import store
from store import LocalStore, listing_to_row


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    return LocalStore()


def test_listing_to_row_status_by_score():
    low = listing_to_row({"listing_id": "1", "score": 0.3})
    high = listing_to_row({"listing_id": "2", "score": 0.9})
    assert low["status"] == "queued"   # below threshold -> queued
    assert high["status"] == "new"     # good photos -> not contacted


def test_upsert_dedup_and_status_preserved(tmp_path, monkeypatch):
    s = _store(tmp_path, monkeypatch)
    listings = [
        {"listing_id": "a", "score": 0.2, "agent_phone": "(210) 555-0123", "address": "1 St"},
        {"listing_id": "b", "score": 0.95, "agent_phone": "210-555-0199", "address": "2 St"},
    ]
    added, updated = s.upsert_listings(listings)
    assert (added, updated) == (2, 0)

    # Mark 'a' as sent, then re-upsert: status must not be clobbered.
    s.update("a", status="sent")
    added, updated = s.upsert_listings(listings)
    assert added == 0 and updated == 2
    assert s.get("a")["status"] == "sent"


def test_phone_normalized_on_ingest(tmp_path, monkeypatch):
    s = _store(tmp_path, monkeypatch)
    s.upsert_listings([{"listing_id": "a", "score": 0.1, "agent_phone": "(210) 555-0123"}])
    assert s.get("a")["agent_phone"] == "+12105550123"


def test_dnc_blocks_and_find_by_phone(tmp_path, monkeypatch):
    s = _store(tmp_path, monkeypatch)
    s.upsert_listings([{"listing_id": "a", "score": 0.1, "agent_phone": "(210) 555-0123"}])
    assert not s.is_dnc("+12105550123")
    s.add_dnc("210-555-0123", reason="replied STOP")
    assert s.is_dnc("+12105550123")
    # find_by_phone matches across formats
    assert s.find_by_phone("(210) 555-0123")["listing_id"] == "a"


def test_get_by_status(tmp_path, monkeypatch):
    s = _store(tmp_path, monkeypatch)
    s.upsert_listings([
        {"listing_id": "a", "score": 0.1},
        {"listing_id": "b", "score": 0.2},
        {"listing_id": "c", "score": 0.9},
    ])
    assert len(s.get_by_status("queued")) == 2
    assert len(s.get_by_status("new")) == 1
