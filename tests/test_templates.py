import templates
from templates import OPT_OUT


def test_every_template_has_opt_out_and_placeholders():
    for t in templates.TEMPLATES:
        assert "{opt_out}" in t
        assert "{first_name}" in t
        assert "{address}" in t


def test_render_fills_in_and_includes_opt_out():
    row = {"agent_name": "Jane Smith", "address": "123 Main St",
           "score_reasons": "2/6 sampled photos blurry"}
    msg = templates.render(row, seed=1)
    assert "Jane" in msg
    assert "123 Main St" in msg
    assert OPT_OUT in msg
    assert "{" not in msg  # all placeholders filled


def test_issue_phrase_maps_reasons():
    assert "blur" in templates.issue_phrase("3/6 sampled photos blurry").lower()
    assert "expos" in templates.issue_phrase("2/6 poorly exposed").lower()
    assert "handful" in templates.issue_phrase("only 4 photos on the listing").lower()
    # Unknown reason -> generic fallback, still a non-empty phrase.
    assert templates.issue_phrase("") == templates._GENERIC_ISSUE


def test_render_handles_missing_name():
    msg = templates.render({"address": "9 Oak Ave"}, seed=2)
    assert "there" in msg  # default greeting
    assert OPT_OUT in msg
