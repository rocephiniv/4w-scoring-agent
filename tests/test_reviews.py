"""Synthetic packet contracts; no clinical labels or actual reviewers."""
from copy import deepcopy
import pytest
from hypothesis import given, settings, strategies as st

from fourw.reviews import make_packet, import_rows, validate_packet_contract
from fourw.runs import RunError, canonical_json


def packet(reviewer="synthetic-reviewer-a", packet_id="a" * 32):
    cases = [{"case_ref": f"private-case-{i}", "indication": "synthetic shared text",
              "source_sha256": "f" * 64, "source_locator": f"Private!R{i+1}"} for i in range(4)]
    return make_packet(cases, packet_id=packet_id, reviewer_ref=reviewer, seed="b" * 64,
                       approval_ref="synthetic-only-packet-approval", protocol={"ref": "synthetic-review-protocol", "sha256": "c" * 64})


def attestation(private):
    return {"reviewer_ref": private["reviewer_ref"], "packet_id": private["packet_id"],
            "authorization_ref": "synthetic-review-return", "independent_before_discussion": True,
            "unexposed_to_ai_and_other_reviews": True, "one_sentence_limit_confirmed": True}


def completed_rows(public):
    rows = deepcopy(public["sheets"][0]["rows"])
    for row in rows:
        row[2:] = ["eligible", 0, 1, 0, 1, "no", "Synthetic contract rationale."]
    return rows


def test_public_packet_does_not_contain_private_linkage():
    public, private = packet()
    data = canonical_json(public).decode()
    for secret in ("private-case-", "synthetic-reviewer-a", "Private!", "f" * 64, "b" * 64):
        assert secret not in data
    assert len(private["bindings"]) == 4
    assert len({row[0] for row in public["sheets"][0]["rows"]}) == 4
    assert all(row[2:] == [None] * 7 for row in public["sheets"][0]["rows"])
    assert packet() == (public, private)


def test_reviewers_have_distinct_opaque_ids_for_the_same_cases():
    first, _ = packet(); second, _ = packet("synthetic-reviewer-b", "d" * 32)
    assert {r[0] for r in first["sheets"][0]["rows"]}.isdisjoint(r[0] for r in second["sheets"][0]["rows"])


def test_import_preserves_individual_review_without_finalization():
    public, private = packet(); rows = completed_rows(public)
    before = deepcopy(rows)
    result = import_rows(rows, public, private, attestation(private))
    assert result["status"] == "accepted" and result["final_adjudication"] is None
    assert len(result["reviews"]) == 4
    assert result["reviews"][0]["review"]["scores"] == {"what": 0, "when": 1, "where": 0, "why": 1}
    assert rows == before


@pytest.mark.parametrize("field", ["independent_before_discussion", "unexposed_to_ai_and_other_reviews", "one_sentence_limit_confirmed"])
def test_missing_attestation_blocks_import(field):
    public, private = packet(); att = attestation(private); att[field] = False
    with pytest.raises(RunError, match="review_attestation_required"):
        import_rows(completed_rows(public), public, private, att)


def test_incomplete_reviews_remain_visible():
    public, private = packet(); rows = completed_rows(public)
    rows[0][3] = None
    result = import_rows(rows, public, private, attestation(private))
    assert result["status"] == "incomplete" and result["pending_count"] == 1
    assert result["reviews"][0]["review"] is None


@pytest.mark.parametrize("mutation", [lambda r: r[0].__setitem__(0, "wrong-id"), lambda r: r[0].__setitem__(1, "changed source"), lambda r: r.append(r[0]), lambda r: r.pop()])
def test_changed_identity_source_or_membership_rejected(mutation):
    public, private = packet(); rows = completed_rows(public); mutation(rows)
    with pytest.raises(RunError):
        import_rows(rows, public, private, attestation(private))


def test_invalid_boolean_score_is_not_a_one():
    public, private = packet(); rows = completed_rows(public); rows[0][3] = True
    with pytest.raises(RunError, match="invalid_review_score"):
        import_rows(rows, public, private, attestation(private))


def test_noneligible_review_has_no_scores():
    public, private = packet(); rows = completed_rows(public)
    rows[0][2:] = ["excluded", None, None, None, None, "no", "Synthetic exclusion rationale."]
    assert import_rows(rows, public, private, attestation(private))["reviews"][0]["review"]["scores"] is None
    rows[0][3] = 0
    with pytest.raises(RunError, match="noneligible_review_scores"):
        import_rows(rows, public, private, attestation(private))


def test_order_is_not_record_identity():
    public, private = packet(); rows = completed_rows(public)
    assert import_rows(rows, public, private, attestation(private)) == import_rows(rows[::-1], public, private, attestation(private))


@pytest.mark.parametrize("public_mutation", [lambda p: p["sheets"][0]["rows"][0].__setitem__(3, 1),
    lambda p: p["sheets"][0]["rows"][0].__setitem__(8, "AI label"),
    lambda p: p["sheets"][1]["rows"].append(["Other reviewer", "score=4"]),
    lambda p: p["sheets"][0]["rows"].append(p["sheets"][0]["rows"][0])])
def test_enriched_public_plan_rejected(public_mutation):
    public, private = packet(); public_mutation(public)
    with pytest.raises(RunError, match="review_packet_contract"):
        validate_packet_contract(public, private)


@pytest.mark.parametrize("field", ["bindings", "packet_id", "seed", "protocol", "public_plan_sha256"])
def test_missing_private_field_has_explicit_error(field):
    public, private = packet(); del private[field]
    with pytest.raises(RunError, match="review_packet_contract"):
        import_rows(completed_rows(public), public, private, {})


def test_uncertainty_without_explanation_stays_pending():
    public, private = packet(); rows = completed_rows(public)
    rows[0][7:] = ["yes", "   "]
    result = import_rows(rows, public, private, attestation(private))
    assert result["pending_count"] == 1 and result["reviews"][0]["returned_values"][-1] == "   "


def test_changed_reviewer_or_packet_requires_matching_attestation():
    public, private = packet(); att = attestation(private); att["reviewer_ref"] = "another"
    with pytest.raises(RunError, match="review_packet_identity"):
        import_rows(completed_rows(public), public, private, att)


@pytest.mark.parametrize("position,value,error", [(2, "unknown", "invalid_review_eligibility"), (7, "maybe", "invalid_review_uncertainty"), (8, 123, "invalid_review_note")])
def test_invalid_answers_have_explicit_diagnostics(position, value, error):
    public, private = packet(); rows = completed_rows(public); rows[0][position] = value
    with pytest.raises(RunError, match=error):
        import_rows(rows, public, private, attestation(private))


@pytest.mark.parametrize("field,value", [("packet_id", "bad"), ("seed", "bad"), ("reviewer_ref", ""), ("protocol", {}), ("approval_ref", "")])
def test_packet_requires_explicit_version_authorization_contract(field, value):
    args = dict(packet_id="a"*32, reviewer_ref="synthetic", seed="b"*64, approval_ref="synthetic", protocol={"ref":"synthetic", "sha256":"c"*64})
    args[field] = value
    with pytest.raises(RunError, match="packet_authorization_contract"):
        make_packet([], **args)


def test_invalid_or_enriched_selection_is_rejected():
    args = dict(packet_id="a"*32, reviewer_ref="synthetic", seed="b"*64, approval_ref="synthetic", protocol={"ref":"synthetic", "sha256":"c"*64})
    with pytest.raises(RunError, match="empty_packet_selection"):
        make_packet([], **args)
    case = {"case_ref":"synthetic", "indication":"synthetic", "source_locator":"S!R2", "source_sha256":"f"*64}
    for cases in ([{**case,"AI_score":4}], [case,case], [None]):
        with pytest.raises(RunError, match="invalid_packet_case"):
            make_packet(cases, **args)


def test_private_linkage_must_match_exact_public_source():
    public, private = packet(); private["bindings"][0]["indication_sha256"] = "0"*64
    with pytest.raises(RunError, match="review_linkage_identity"):
        validate_packet_contract(public, private)
    del private["bindings"][0]["source_locator"]
    with pytest.raises(RunError, match="review_packet_contract"):
        validate_packet_contract(public, private)
    with pytest.raises(RunError, match="review_packet_contract"):
        validate_packet_contract({}, private)


def test_malformed_public_row_is_not_a_review():
    public, private = packet(); public["sheets"][0]["rows"][0] = None
    with pytest.raises(RunError, match="review_packet_contract"):
        validate_packet_contract(public, private)


def test_identifier_collision_stops_packet(monkeypatch):
    from fourw import reviews
    class Collision:
        def hexdigest(self):return "1"*64
    monkeypatch.setattr(reviews.hmac, "new", lambda *a, **kw: Collision())
    with pytest.raises(RunError, match="packet_identifier_collision"):
        packet()


@settings(max_examples=50, deadline=None, database=None, derandomize=True)
@given(st.lists(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=60), min_size=1, max_size=10))
def test_generated_texts_preserve_identity_and_serialization(texts):
    """Structural data only: generated texts never define clinical labels."""
    import json
    cases = [{"case_ref":str(i), "indication":text, "source_locator":f"S!R{i+1}", "source_sha256":"f"*64} for i,text in enumerate(texts)]
    args = dict(packet_id="a"*32, reviewer_ref="synthetic", seed="b"*64, approval_ref="synthetic", protocol={"ref":"synthetic", "sha256":"c"*64})
    public, private = make_packet(cases, **args)
    assert make_packet(list(reversed(cases)), **args) == (public, private)
    validate_packet_contract(json.loads(canonical_json(public)), json.loads(canonical_json(private)))
    assert sorted(r[1] for r in public["sheets"][0]["rows"]) == sorted(texts)
    assert all(row[2:] == [None]*7 for row in public["sheets"][0]["rows"])
