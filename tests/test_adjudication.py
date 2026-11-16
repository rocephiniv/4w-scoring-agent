"""Synthetic human-record contracts, not clinical adjudication or reference labels."""
from copy import deepcopy

import pytest

from fourw.adjudication import AUTHORITY, digest, finalize, prepare
from fourw.assessments import AssessmentValidator, WS
from fourw.runs import RunError
from test_assessments import SCHEMA, fixture


@pytest.fixture
def validator():
    return AssessmentValidator(SCHEMA)


def review(reviewer="clinician-a", eligibility="eligible", **scores):
    return {"case_ref": "case-1", "source_sha256": "a" * 64,
            "authority": AUTHORITY, "import_ref": "synthetic-import-" + reviewer,
            "import_sha256": "b" * 64,
            "review": {"reviewer_ref": reviewer, "eligibility": eligibility,
                       "scores": dict(dict.fromkeys(WS, 0), **scores) if eligibility == "eligible" else None,
                       "evidence": "Synthetic review fixture only.", "uncertainty": "",
                       "recorded_before_discussion": True}}


def brief(validator, reviews=None):
    context, assessment, audit = fixture()
    return prepare(assessment, audit, context, reviews if reviews is not None else [review(), review("superior")], validator)


def final_inputs(value, decision=None):
    decision = deepcopy(decision) if decision is not None else {
        "eligibility": "eligible", "scores": dict.fromkeys(WS, 0), "total": 0,
        "complete": False, "authority": "superior", "rationale": "Synthetic authorized decision fixture.",
        "policy_change": False}
    resolutions = [{"issue_ref": i["ref"], "rationale": "Synthetic issue resolution fixture."} for i in value["issues"]]
    approval = {"authorization_ref": "synthetic-approval", "human_authority": "superior",
                "designated_superior_ref": "superior", "mode": "consensus",
                "consensus_reviewers": ["clinician-a", "superior"], "reconciliation_ref": "synthetic-reconciliation",
                "brief_sha256": digest(value), "decision_sha256": digest(decision),
                "resolutions_sha256": digest(resolutions), "all_material_issues_resolved": True}
    return decision, resolutions, approval


def test_agreement_does_not_automatically_establish_finality(validator):
    value = brief(validator)
    assert value["issues"] == []
    assert value["final_adjudication"] is None
    assert value["reference_standard"] == "not_established"
    assert value["status"] == "pending_human_decision"


def test_separate_human_decision_preserves_originals_and_does_not_alias_inputs(validator):
    value = brief(validator)
    decision, resolutions, approval = final_inputs(value)
    before = deepcopy((value, decision, resolutions, approval))
    result = finalize(value, decision, resolutions, approval, validator)
    assert (value, decision, resolutions, approval) == before
    assert value["original_assessment"]["final_adjudication"] is None
    assert value["original_assessment"]["independent_reviews"] == []
    assert result["original_assessment_sha256"] == digest(value["original_assessment"])
    assert result["clinical_validation"] == "not_established_by_software"
    result["final_adjudication"]["scores"]["what"] = 1
    result["independent_reviews"][0]["review"]["evidence"] = "changed output"
    assert (value, decision, resolutions, approval) == before


@pytest.mark.parametrize("count", [0, 1])
def test_missing_review_remains_pending_and_cannot_be_finalized(validator, count):
    value = brief(validator, [review()][:count])
    assert sum(i["kind"] == "missing-independent-review" for i in value["issues"]) == 2 - count
    with pytest.raises(RunError, match="two_independent_reviews_required"):
        finalize(value, *final_inputs(value), validator)


def test_flags_and_review_disagreement_require_explicit_resolution(validator):
    context, assessment, audit = fixture()
    assessment["first_pass"].update(flags=[{"kind": "wording", "affected_ws": ["what"], "reason": "Synthetic ambiguity."}], provisional_complete=None)
    audit["per_w"]["what"]["basis"] = "uncertain"
    reviews = [review(what=1), review("superior")]
    reviews[0]["review"]["uncertainty"] = "Synthetic uncertainty."
    value = prepare(assessment, audit, context, reviews, validator)
    refs = {i["ref"] for i in value["issues"]}
    assert refs == {"first_pass_flag:0", "review:0:uncertainty", "first_pass_difference:what", "review_disagreement:what"}
    decision, resolutions, approval = final_inputs(value)
    approval.update(mode="superior_ruling", consensus_reviewers=[])
    result = finalize(value, decision, resolutions, approval, validator)
    assert result["independent_reviews"] == reviews
    assert result["resolutions"] == resolutions
    assert assessment["first_pass"]["flags"] and assessment["first_pass"]["provisional_complete"] is None


def test_pending_eligibility_can_only_gain_a_separate_resolved_human_decision(validator):
    context, assessment, audit = fixture()
    context.eligibility["decision"] = "pending"
    assessment["eligibility"]["decision"] = "pending"
    assessment["first_pass"] = None; audit["per_w"] = None
    value = prepare(assessment, audit, context, [review(eligibility="pending"), review("superior", eligibility="excluded")], validator)
    assert {i["ref"] for i in value["issues"]} == {"review:0:eligibility", "first_pass_difference:eligibility", "review_disagreement:eligibility"}
    decision, resolutions, approval = final_inputs(value)
    decision.update(eligibility="excluded", scores=None, total=None, complete=None)
    approval.update(mode="superior_ruling", consensus_reviewers=[], decision_sha256=digest(decision))
    result = finalize(value, decision, resolutions, approval, validator)
    assert result["final_adjudication"]["eligibility"] == "excluded"
    assert assessment["eligibility"]["decision"] == "pending" and assessment["first_pass"] is None


@pytest.mark.parametrize("reviews,code", [
    (None, "adjudication_review_count"),
    ([review(), review("b"), review("c")], "adjudication_review_count"),
    ([review(), review()], "adjudication_reviewer_or_scores"),
    ([dict(review(), case_ref="different")], "adjudication_review_binding"),
    ([dict(review(), source_sha256="c" * 64)], "adjudication_review_binding"),
    ([dict(review(), authority="retired")], "adjudication_review_binding"),
    ([dict(review(), import_ref=" ")], "adjudication_review_binding"),
    ([dict(review(), import_sha256="g" * 64)], "adjudication_review_binding"),
    ([dict(review(), import_sha256=None)], "adjudication_review_binding"),
    ([dict(review(), review={})], "adjudication_review_contract"),
    ([dict(review(), unexpected="label")], "adjudication_review_binding"),
    ([None], "adjudication_review_binding"),
])
def test_unbound_or_invalid_reviews_fail(validator, reviews, code):
    context, assessment, audit = fixture()
    with pytest.raises(RunError, match=code):
        prepare(assessment, audit, context, reviews, validator)


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(scores=dict.fromkeys(WS, 0.0)),
    lambda r: r.update(eligibility="excluded"),
    lambda r: r.update(reviewer_ref=" "),
])
def test_structurally_valid_review_still_needs_semantic_checks(validator, mutation):
    item = review(); mutation(item["review"])
    with pytest.raises(RunError, match="adjudication_reviewer_or_scores"):
        brief(validator, [item])


def test_invalid_first_pass_is_not_adjudicated(validator):
    context, assessment, audit = fixture(); assessment["first_pass"]["total"] = 4
    with pytest.raises(RunError, match="adjudication_first_pass_contract"):
        prepare(assessment, audit, context, [], validator)


@pytest.mark.parametrize("mutation,code", [
    (lambda b: b.pop("context"), "adjudication_brief_contract"),
    (lambda b: b.update(context=None), "adjudication_brief_contract"),
    (lambda b: b.update(issues=[{"ref": "invented", "kind": "invented"}]), "adjudication_brief_changed"),
    (lambda b: b.update(final_adjudication={}), "adjudication_brief_changed"),
])
def test_reconstruction_rejects_tampered_briefs(validator, mutation, code):
    value = brief(validator); inputs = final_inputs(value); mutation(value)
    with pytest.raises(RunError, match=code):
        finalize(value, *inputs, validator)


@pytest.mark.parametrize("mutation,code", [
    (lambda a: a.update(authorization_ref=""), "human_finalization_approval_required"),
    (lambda a: a.update(brief_sha256="c" * 64), "human_finalization_approval_required"),
    (lambda a: a.update(decision_sha256="c" * 64), "human_finalization_approval_required"),
    (lambda a: a.update(resolutions_sha256="c" * 64), "human_finalization_approval_required"),
    (lambda a: a.update(all_material_issues_resolved=False), "human_finalization_approval_required"),
    (lambda a: a.update(human_authority="agent"), "unapproved_human_authority"),
    (lambda a: a.update(designated_superior_ref="unrecorded-third-reviewer"), "unapproved_human_authority"),
    (lambda a: a.update(consensus_reviewers=None), "consensus_contract"),
    (lambda a: a.update(consensus_reviewers=["clinician-a", 1]), "consensus_contract"),
    (lambda a: a.update(consensus_reviewers=["clinician-a"]), "both_consensus_confirmations_required"),
    (lambda a: a.update(consensus_reviewers=["clinician-a", "clinician-a"]), "both_consensus_confirmations_required"),
    (lambda a: a.update(mode="superior_ruling"), "superior_ruling_contract"),
    (lambda a: a.update(mode="superior_ruling", consensus_reviewers=[], human_authority="clinician-a"), "superior_ruling_contract"),
    (lambda a: a.update(mode="automatic"), "human_finalization_mode"),
])
def test_human_authorization_boundaries(validator, mutation, code):
    value = brief(validator); decision, resolutions, approval = final_inputs(value); mutation(approval)
    with pytest.raises(RunError, match=code):
        finalize(value, decision, resolutions, approval, validator)


@pytest.mark.parametrize("resolutions", [None, [], [{"issue_ref": "first_pass_difference:what", "rationale": ""}],
    [{"issue_ref": "wrong", "rationale": "Synthetic."}],
    [{"issue_ref": "first_pass_difference:what", "rationale": "Synthetic."}] * 2])
def test_unresolved_or_duplicate_issue_disposition_blocks_finality(validator, resolutions):
    value = brief(validator, [review(what=1), review("superior")])
    decision, _, approval = final_inputs(value); approval["resolutions_sha256"] = digest(resolutions)
    with pytest.raises(RunError, match="material_issues_unresolved"):
        finalize(value, decision, resolutions, approval, validator)


@pytest.mark.parametrize("mutation,code", [
    (lambda d: d.update(eligibility="pending"), "final_adjudication_contract"),
    (lambda d: d.update(authority="clinician-a"), "final_adjudication_authority"),
    (lambda d: d.update(rationale=" "), "final_adjudication_authority"),
    (lambda d: d.update(policy_change=True), "policy_change_requires_separate_approval_and_version"),
    (lambda d: d.update(scores=None), "final_adjudication_scores"),
    (lambda d: d.update(scores=dict.fromkeys(WS, 0.0)), "final_adjudication_scores"),
    (lambda d: d.update(total=4), "final_adjudication_arithmetic"),
    (lambda d: d.update(total=0.0), "final_adjudication_arithmetic"),
    (lambda d: d.update(complete=True), "final_adjudication_arithmetic"),
    (lambda d: d.update(eligibility="excluded", scores=None), "final_exclusion_must_not_have_scores"),
])
def test_final_labels_require_resolved_eligibility_math_and_unchanged_authority(validator, mutation, code):
    value = brief(validator); decision, resolutions, approval = final_inputs(value); mutation(decision)
    approval["decision_sha256"] = digest(decision)
    with pytest.raises(RunError, match=code):
        finalize(value, decision, resolutions, approval, validator)


def test_final_complete_threshold_is_checked(validator):
    value = brief(validator, [review(what=1, when=1, where=1), review("superior", what=1, when=1, where=1)])
    decision, resolutions, approval = final_inputs(value)
    decision.update(scores=dict(what=1, when=1, where=1, why=0), total=3, complete=True)
    approval["decision_sha256"] = digest(decision)
    assert finalize(value, decision, resolutions, approval, validator)["final_adjudication"]["complete"] is True


def test_imported_independent_values_feed_reconciliation_without_inventing_reviews(validator):
    from fourw.reviews import make_packet, import_rows
    from test_reviews import attestation
    context, assessment, audit = fixture()
    envelopes = []
    for index, reviewer_ref in enumerate(("clinician-a", "superior"), 1):
        public, private = make_packet([
            {"case_ref": context.case_ref, "indication": context.indication,
             "source_sha256": context.source_sha256, "source_locator": context.source_locator}],
            packet_id=str(index) * 32, reviewer_ref=reviewer_ref, seed=str(index) * 64,
            approval_ref="synthetic-packet-approval", protocol={"ref": "synthetic-review-protocol", "sha256": "c" * 64})
        rows = deepcopy(public["sheets"][0]["rows"])
        rows[0][2:] = ["eligible", 0, 0, 0, 0, "no", "Synthetic clinician fixture."]
        imported = import_rows(rows, public, private, attestation(private))
        assert imported["final_adjudication"] is None
        envelopes.append({"case_ref": context.case_ref, "source_sha256": context.source_sha256,
                          "authority": AUTHORITY, "import_ref": private["packet_id"],
                          "import_sha256": digest(imported), "review": imported["reviews"][0]["review"]})
    value = prepare(assessment, audit, context, envelopes, validator)
    result = finalize(value, *final_inputs(value), validator)
    assert result["independent_reviews"] == envelopes
    assert result["original_assessment_sha256"] == digest(assessment)
