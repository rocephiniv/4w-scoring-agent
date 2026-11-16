"""Contract checks with synthetic text, not model predictions or clinical labels."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from fourw.assessments import AssessmentValidator, CaseContext, SCHEMA_SHA256

SCHEMA = Path(__file__).resolve().parents[1] / "KNOWLEDGE_BASE/HARMONIZED_V1/OUTPUT_SCHEMA.json"
WS = ("what", "when", "where", "why")


def fixture():
    eligibility = {"decision": "eligible", "reason": "synthetic input approval", "review_status": "first-pass"}
    context = CaseContext("case-1", "run-1", "synthetic evidence", "a" * 64, "Orders!R2C2", eligibility)
    payload = {
        "case_ref": "case-1", "run_ref": "run-1", "rubric_version": "4W-Harmonized-v1",
        "eligibility": deepcopy(eligibility), "independent_reviews": [], "final_adjudication": None,
        "first_pass": {"scores": dict.fromkeys(WS, 0), "total": 0,
                       "evidence": dict.fromkeys(WS, "No qualifying evidence in this synthetic fixture"),
                       "rule_ids": ["H1-OUTPUT"], "flags": [], "provisional_complete": False, "finalized_complete": None},
    }
    audit = {"case_ref": context.case_ref, "run_ref": context.run_ref, "source_sha256": context.source_sha256,
             "source_locator": context.source_locator, "authority": "4W-Harmonized-v1-docs.1",
             "per_w": {w: {"basis": "absent", "spans": [], "rule_ids": ["H1-OUTPUT"], "rationale": "Synthetic contract-only absence."} for w in WS}}
    return context, payload, audit


@pytest.fixture
def validator():
    return AssessmentValidator(SCHEMA, SCHEMA_SHA256)


def test_schema_and_semantic_checks_do_not_modify_inputs(validator):
    context, payload, audit = fixture(); before = deepcopy((payload, audit))
    result = validator.validate_first_pass(payload, audit, context)
    assert result["status"] == "accepted"
    assert (payload, audit) == before
    assert result["clinical_validity"] == "not_established"


@pytest.mark.parametrize("mutation,code", [
    (lambda p, a: p.update(case_ref="wrong"), "case_identity"),
    (lambda p, a: p.update(run_ref="wrong"), "run_identity"),
    (lambda p, a: p.update(rubric_version="retired"), "schema"),
    (lambda p, a: p["first_pass"].update(total=4), "arithmetic"),
    (lambda p, a: p["first_pass"].update(provisional_complete=True), "completeness"),
    (lambda p, a: p["first_pass"].update(finalized_complete=True), "schema"),
    (lambda p, a: p["first_pass"]["scores"].update(what=True), "schema"),
    (lambda p, a: p["first_pass"].update(rule_ids=["retired-rule"]), "schema"),
    (lambda p, a: a.update(source_sha256="b" * 64), "source_identity"),
    (lambda p, a: a["per_w"]["what"].update(rule_ids=["made-up-rule"]), "audit_rules"),
    (lambda p, a: a["per_w"]["what"].update(rationale=""), "audit_shape"),
    (lambda p, a: p["eligibility"].update(review_status="human-confirmed"), "eligibility_identity"),
])
def test_invalid_output_is_processing_failure_not_zero_score(validator, mutation, code):
    context, payload, audit = fixture(); mutation(payload, audit)
    result = validator.validate_first_pass(payload, audit, context)
    assert result["status"] == "failed"
    assert code in {e["code"] for e in result["errors"]}
    assert "assessment" not in result
    assert "synthetic evidence" not in str(result["errors"])


def test_positive_credit_requires_exact_permitted_evidence_span(validator):
    context, payload, audit = fixture()
    payload["first_pass"]["scores"]["what"] = 1
    payload["first_pass"]["total"] = 1
    payload["first_pass"]["evidence"]["what"] = "A minimal paraphrase is allowed by the canonical schema."
    audit["per_w"]["what"].update(basis="documented", spans=[{"start": 0, "end": 9, "quote": "synthetic"}])
    assert validator.validate_first_pass(payload, audit, context)["status"] == "accepted"
    audit["per_w"]["what"]["spans"][0]["quote"] = "invented"
    result = validator.validate_first_pass(payload, audit, context)
    assert "evidence_fidelity" in {e["code"] for e in result["errors"]}
    audit["per_w"]["what"]["spans"] = []
    assert validator.validate_first_pass(payload, audit, context)["status"] == "failed"


@pytest.mark.parametrize("basis", ["uncertain", "insufficient"])
def test_flagged_uncertainty_stays_provisional_and_visible(validator, basis):
    context, payload, audit = fixture()
    payload["first_pass"].update(flags=[{"kind": "wording", "affected_ws": ["why"], "reason": "Synthetic ambiguity"}], provisional_complete=None)
    audit["per_w"]["why"].update(basis=basis)
    result = validator.validate_first_pass(payload, audit, context)
    assert result["status"] == "accepted" and result["human_review_required"] is True
    payload["first_pass"]["scores"]["why"] = 1
    payload["first_pass"]["total"] = 1
    assert validator.validate_first_pass(payload, audit, context)["status"] == "failed"


@pytest.mark.parametrize("decision", ["excluded", "pending"])
def test_eligibility_is_not_four_zeros(validator, decision):
    context, payload, audit = fixture()
    context.eligibility["decision"] = decision
    payload["eligibility"]["decision"] = decision
    payload["first_pass"] = None; audit["per_w"] = None
    result = validator.validate_first_pass(payload, audit, context)
    assert result["status"] == "accepted" and result["human_review_required"] is True
    payload["first_pass"] = fixture()[1]["first_pass"]
    assert validator.validate_first_pass(payload, audit, context)["status"] == "failed"


def test_model_cannot_create_human_reviews(validator):
    context, payload, audit = fixture()
    payload["independent_reviews"] = [{"reviewer_ref": "invented", "eligibility": "eligible", "scores": dict.fromkeys(WS, 0), "evidence": "", "uncertainty": "", "recorded_before_discussion": True}]
    result = validator.validate_first_pass(payload, audit, context)
    assert "fabricated_human_fields" in {e["code"] for e in result["errors"]}


def test_schema_hash_mismatch_blocks_loading(tmp_path):
    schema = tmp_path / "schema.json"; schema.write_bytes(SCHEMA.read_bytes())
    with pytest.raises(ValueError, match="schema_identity"):
        AssessmentValidator(schema, "f" * 64)


@pytest.mark.parametrize("change", [
    lambda a: a.update(extra="unexpected"),
    lambda a: a.update(per_w={}),
    lambda a: a["per_w"]["what"].update(spans=[{"start": -1, "end": 9, "quote": "synthetic"}]),
    lambda a: a["per_w"]["what"].update(spans=[{"start": True, "end": 9, "quote": "synthetic"}]),
    lambda a: a["per_w"]["what"].update(spans=[{"start": 0, "end": 1000, "quote": "synthetic"}]),
])
def test_malformed_audit_fails_without_source_text_disclosure(validator, change):
    context, payload, audit = fixture(); change(audit)
    result = validator.validate_first_pass(payload, audit, context)
    assert result["status"] == "failed"
    assert context.indication not in str(result)


def test_absence_cannot_support_positive_credit(validator):
    context, payload, audit = fixture()
    payload["first_pass"]["scores"]["what"] = 1; payload["first_pass"]["total"] = 1
    assert "absence_credit" in {e["code"] for e in validator.validate_first_pass(payload, audit, context)["errors"]}


def test_noneligible_audit_must_not_contain_scoring(validator):
    context, payload, audit = fixture()
    context.eligibility["decision"] = "pending"; payload["eligibility"]["decision"] = "pending"
    payload["first_pass"] = None
    assert "eligibility_scoring_separation" in {e["code"] for e in validator.validate_first_pass(payload, audit, context)["errors"]}
