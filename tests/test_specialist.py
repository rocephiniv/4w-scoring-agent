"""Synthetic response-contract tests; no model inference or clinical accuracy claim."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import pytest
import fourw.specialist as specialist
from fourw.assessments import AssessmentValidator
from fourw.runs import RunError, canonical_json
from test_assessments import fixture, SCHEMA
from test_adjudication import brief
from test_knowledge import ready

PROJECT = SCHEMA.parents[2]
HASHES = {ref: sha256((PROJECT / ref).read_bytes()).hexdigest() for ref in specialist.REQUIRED}
AUTH = {"scope": "synthetic_build_only", "authorization_ref": "synthetic-build-approval", "rescore_authorization_ref": None}


@pytest.fixture(scope="module")
def ledger(tmp_path_factory):
    return ready(tmp_path_factory.mktemp("specialist-knowledge"))


@pytest.fixture
def validator():
    return AssessmentValidator(SCHEMA)


def request(ledger, validator, operation="initial_score", contexts=None, **overrides):
    args = dict(project=PROJECT, expected_hashes=HASHES, contexts=[fixture()[0]] if contexts is None else contexts,
                operation=operation, ledger=ledger, release_id="release-1", release_sha256=ledger.read()["releases"]["release-1"]["sha256"],
                authorization=AUTH, validator=validator)
    args.update(overrides)
    return specialist.make_request(**args)


def response(value, records=None):
    _, assessment, audit = fixture()
    record = {"case_ref": "case-1", "first_pass": assessment["first_pass"], "per_w": audit["per_w"]}
    return {"request_id": value["request_id"], "records": records if records is not None else [record]}


def test_complete_rules_and_approved_knowledge_without_private_case_metadata(ledger, validator):
    context = fixture()[0]; value = request(ledger, validator)
    assert value["cases"] == [{"case_ref": context.case_ref, "indication": context.indication}]
    assert value["authority"]["hashes"] == HASHES
    assert {d["ref"] for d in value["authority"]["documents"]} == specialist.CONTEXT_DOCUMENTS
    # The canonical loader mandates the selection document itself, not only its hash.
    assert {specialist.SELECTION, specialist.PROMPT, specialist.RULE_IDS, specialist.OFFICIAL_TEXT} <= specialist.CONTEXT_DOCUMENTS
    for document in value["authority"]["documents"]:
        assert document["text"] == (PROJECT / document["ref"]).read_bytes().decode("utf-8-sig")
    assert value["knowledge"]["lessons"] and "approval" not in value["knowledge"]
    assert "observation_refs" not in value["knowledge"]["lessons"][0]
    assert value["request_id"] == specialist.digest({k:v for k,v in value.items() if k != "request_id"})
    assert len(canonical_json(value)) <= specialist.MAX_REQUEST_BYTES


def test_response_maps_to_canonical_record_without_human_fields(ledger, validator):
    context = fixture()[0]; value = request(ledger, validator); original = deepcopy(value)
    result = specialist.accept_response(value, [context], canonical_json(response(value)), validator)
    assert result["status"] == "complete" and result["clinical_validity"] == "not_established"
    row = result["records"][0]
    assert row["audit"]["source_sha256"] == context.source_sha256
    assert row["assessment"]["first_pass"]["total"] == 0
    assert row["assessment"]["independent_reviews"] == [] and row["assessment"]["final_adjudication"] is None
    assert value == original


def test_missing_response_is_failure_not_absence(ledger, validator):
    value = request(ledger, validator)
    result = specialist.accept_response(value, [fixture()[0]], canonical_json(response(value, [])), validator)
    assert result["status"] == "complete_with_failures"
    assert result["records"][0]["assessment"] is None
    assert result["records"][0]["error_code"] == "missing_specialist_record"


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(final_adjudication={"invented": True}),
    lambda r: r["first_pass"].update(total=4),
    lambda r: r["first_pass"].update(finalized_complete=True),
    lambda r: r["first_pass"]["scores"].update(what=True),
    lambda r: r["per_w"]["what"].update(spans=[{"start": 0, "end": 3, "quote": "not"}]),
    lambda r: r.update(first_pass=None),
])
def test_invalid_decisions_are_processing_failures(ledger, validator, mutation):
    value = request(ledger, validator); observed = response(value); mutation(observed["records"][0])
    result = specialist.accept_response(value, [fixture()[0]], canonical_json(observed), validator)
    assert result["records"][0]["processing_status"] == "failed"
    assert result["records"][0]["assessment"] is None and result["records"][0]["audit"] is None


def test_uncertainty_stays_visible_with_no_completeness_finality(ledger, validator):
    value = request(ledger, validator); observed = response(value); row = observed["records"][0]
    row["first_pass"].update(flags=[{"kind": "wording", "affected_ws": ["why"], "reason": "Synthetic ambiguity."}], provisional_complete=None)
    row["per_w"]["why"]["basis"] = "uncertain"
    result = specialist.accept_response(value, [fixture()[0]], canonical_json(observed), validator)
    first = result["records"][0]["assessment"]["first_pass"]
    assert first["scores"]["why"] == 0 and first["flags"]
    assert first["provisional_complete"] is None and first["finalized_complete"] is None


@pytest.mark.parametrize("mutation,code", [
    (lambda v: v.update(request_id="wrong"), "specialist_response_contract"),
    (lambda v: v.update(records=None), "specialist_response_contract"),
    (lambda v: v.update(extra="unapproved"), "specialist_response_contract"),
    (lambda v: v["records"].append(deepcopy(v["records"][0])), "specialist_response_binding"),
    (lambda v: v["records"][0].update(case_ref="unknown"), "specialist_response_binding"),
    (lambda v: v.update(records=[None]), "specialist_response_binding"),
])
def test_unbound_or_duplicate_response_fails_whole_batch(ledger, validator, mutation, code):
    value = request(ledger, validator); observed = response(value); mutation(observed)
    with pytest.raises(RunError, match=code):specialist.accept_response(value, [fixture()[0]], canonical_json(observed), validator)


def test_json_extraction_or_repair_is_not_performed(ledger, validator):
    value = request(ledger, validator)
    for raw in (b'```json\n{}\n```', b'{"records":[],"records":[]}'):
        with pytest.raises(RunError, match="unreadable_run_artifact"):
            specialist.accept_response(value, [fixture()[0]], raw, validator)


@pytest.mark.parametrize("decision", ["excluded", "pending"])
def test_noneligible_cases_remain_unscored_without_model_request(ledger, validator, decision):
    context = fixture()[0]; context.eligibility["decision"] = decision
    row = specialist.noneligible_record(context, validator)
    assert row["assessment"]["first_pass"] is None and row["audit"]["per_w"] is None
    with pytest.raises(RunError, match="specialist_eligible_cases_only"):request(ledger, validator, contexts=[context])


def test_blinding_and_separate_rescore_authorization(ledger, validator):
    with pytest.raises(RunError, match="blinded_scoring_must_not_receive_reviews"):
        request(ledger, validator, briefs={"case-1": brief(validator)})
    with pytest.raises(RunError, match="specialist_synthetic_authorization_required"):
        request(ledger, validator, operation="rescore")
    value = request(ledger, validator, operation="rescore", authorization={**AUTH, "rescore_authorization_ref": "explicit-synthetic-rescore"})
    assert value["operation"] == "rescore" and "adjudication" not in value["cases"][0]


def test_adjudication_proposals_never_overwrite_original_or_finalize(ledger, validator):
    original = brief(validator, reviews=[]); before = deepcopy(original)
    value = request(ledger, validator, operation="adjudication_support", briefs={"case-1": original})
    observed = response(value)
    observed["records"][0]["resolutions"] = [{"issue_ref": i["ref"], "disposition": "unresolved", "rationale": "Independent review still missing."} for i in original["issues"]]
    result = specialist.accept_response(value, [fixture()[0]], canonical_json(observed), validator)
    row = result["records"][0]
    assert row["processing_status"] == "accepted" and row["assessment"] is None
    assert row["proposal"]["requires_human_review"] is True
    assert row["proposal"]["final_adjudication"] is None and row["proposal"]["rationale_kind"] == "agent_explanation"
    assert original == before


def test_reviewer_names_are_not_forwarded(ledger, validator):
    value = request(ledger, validator, operation="adjudication_support", briefs={"case-1": brief(validator)})
    reviews = value["cases"][0]["adjudication"]["reviews"]
    assert [r["review_ref"] for r in reviews] == ["review-1", "review-2"]
    assert all("reviewer_ref" not in r for r in reviews)


@pytest.mark.parametrize("resolutions", [None, [], [{"issue_ref": "invented", "disposition": "proposed", "rationale": "x"}]])
def test_adjudication_cannot_suppress_issues(ledger, validator, resolutions):
    value = request(ledger, validator, operation="adjudication_support", briefs={"case-1": brief(validator, reviews=[])})
    observed = response(value); observed["records"][0]["resolutions"] = resolutions
    result = specialist.accept_response(value, [fixture()[0]], canonical_json(observed), validator)
    assert result["records"][0]["error_code"] == "specialist_resolution_contract"
    assert "proposal" not in result["records"][0]


@pytest.mark.parametrize("kwargs,code", [
    ({"operation": "compare"}, "specialist_operation"),
    ({"authorization": {**AUTH, "scope": "production"}}, "specialist_synthetic_authorization_required"),
    ({"contexts": []}, "specialist_case_contract"),
    ({"contexts": [fixture()[0], fixture()[0]]}, "specialist_case_identity"),
    ({"contexts": [replace(fixture()[0], source_sha256="invalid")]}, "specialist_eligible_cases_only"),
    ({"contexts": [replace(fixture()[0], eligibility=None)]}, "specialist_eligible_cases_only"),
    ({"contexts": [replace(fixture()[0], indication="x" * 130_000)]}, "specialist_request_too_large"),
    ({"release_sha256": "f" * 64}, "unknown_or_changed_knowledge_release"),
    ({"operation": "adjudication_support", "briefs": {}}, "specialist_adjudication_context"),
    ({"operation": "adjudication_support", "briefs": {"case-1": {}}}, "specialist_adjudication_context"),
])
def test_request_rejects_unsupported_or_unapproved_inputs(ledger, validator, kwargs, code):
    with pytest.raises(RunError, match=code):request(ledger, validator, **kwargs)


def test_authority_files_are_verified(ledger, validator, tmp_path):
    with pytest.raises(RunError, match="specialist_authority_manifest"):request(ledger, validator, expected_hashes={})
    with pytest.raises(RunError, match="specialist_authority_unavailable"):request(ledger, validator, project=tmp_path)
    wrong = dict(HASHES); wrong[specialist.SCHEMA] = "f" * 64
    with pytest.raises(RunError, match="specialist_authority_changed"):request(ledger, validator, expected_hashes=wrong)


def test_changed_request_or_case_binding_blocks_response(ledger, validator):
    value = request(ledger, validator); observed = canonical_json(response(value)); value["scope"] = "production"
    with pytest.raises(RunError, match="specialist_request_identity"):
        specialist.accept_response(value, [fixture()[0]], observed, validator)
    value = request(ledger, validator)
    with pytest.raises(RunError, match="specialist_request_identity"):
        specialist.accept_response(value, [replace(fixture()[0], indication="other")], observed, validator)


@pytest.mark.parametrize("ref,raw,code", [
    (specialist.RULE_IDS, b"\xff", "specialist_authority_encoding"),
    (specialist.SELECTION, b'{"canonical_owners":null}', "specialist_authority_selection"),
    (specialist.SCHEMA, b'{}', "specialist_authority_selection"),
])
def test_changed_authority_is_not_accepted_merely_because_its_hash_matches(tmp_path, ref, raw, code):
    hashes = dict(HASHES)
    for relative in specialist.REQUIRED:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = raw if relative == ref else (PROJECT / relative).read_bytes()
        destination.write_bytes(content)
        hashes[relative] = sha256(content).hexdigest()
    with pytest.raises(RunError, match=code):specialist.load_authority(tmp_path, hashes)


def test_tampered_adjudication_brief_is_rejected(ledger, validator):
    value = brief(validator)
    value["issues"] = [{"ref": "invented", "kind": "invented"}]
    with pytest.raises(RunError, match="specialist_adjudication_context"):
        request(ledger, validator, operation="adjudication_support", briefs={"case-1": value})


def test_noneligible_record_rejects_invalid_eligibility(validator):
    context = fixture()[0]
    with pytest.raises(RunError, match="noneligible_context_required"):
        specialist.noneligible_record(context, validator)
    context.eligibility.update(decision="excluded", reason=None)
    with pytest.raises(RunError, match="noneligible_record_contract"):
        specialist.noneligible_record(context, validator)


def test_positive_evidence_is_exact_with_unicode_codepoint_offsets(ledger, validator):
    context = replace(fixture()[0], indication="🙂 pain")
    value = request(ledger, validator, contexts=[context]); observed = response(value)
    row = observed["records"][0]
    row["first_pass"]["scores"]["what"] = 1
    row["first_pass"]["total"] = 1
    row["first_pass"]["rule_ids"].append("H1-WHAT-CURRENT")
    row["first_pass"]["evidence"]["what"] = "pain"
    row["per_w"]["what"].update(basis="documented", spans=[{"start": 2, "end": 6, "quote": "pain"}],
        rule_ids=["H1-WHAT-CURRENT"], rationale="The permitted indication explicitly documents pain.")
    result = specialist.accept_response(value, [context], canonical_json(observed), validator)
    assert result["records"][0]["processing_status"] == "accepted"
    row["per_w"]["what"]["spans"][0].update(start=3, end=7)
    failed = specialist.accept_response(value, [context], canonical_json(observed), validator)
    assert failed["records"][0]["assessment"] is None
    assert failed["records"][0]["validation_errors"] == [{"code": "evidence_span", "path": "/audit/per_w/what/spans/0"}]
