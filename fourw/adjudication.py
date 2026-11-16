"""Human reconciliation contracts; no automatic final-label selection.

The coordinator verifies actual reviewers/authorization and import provenance.
These pure helpers check their bindings, preserve first-pass evidence and return
a separate linked human decision. They neither execute scoring nor change rules.
"""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256

from .assessments import WS
from .runs import RunError, canonical_json

AUTHORITY = "4W-Harmonized-v1-docs.1"


def digest(value):
    return sha256(canonical_json(value)).hexdigest()


def _text(value):
    return type(value) is str and bool(value.strip())


def _scores(eligibility, scores):
    return (type(scores) is dict and set(scores) == set(WS) and all(type(v) is int and v in (0, 1) for v in scores.values())) if eligibility == "eligible" else scores is None


def prepare(assessment, audit, context, reviews, validator):
    """List documented disagreement/uncertainty without interpreting labels.

    Each review envelope binds a canonical human review to an already verified
    import record. No match is inferred from wording, date, row order or totals.
    Missing independent reviews remain visibly pending.
    """
    if validator.validate_first_pass(assessment, audit, context)["status"] != "accepted":
        raise RunError("adjudication_first_pass_contract")
    if type(reviews) is not list or len(reviews) > 2:
        raise RunError("adjudication_review_count")
    seen = set()
    canonical_reviews = []
    for item in reviews:
        if (type(item) is not dict or set(item) != {"case_ref", "source_sha256", "authority", "import_ref", "import_sha256", "review"}
                or item["case_ref"] != context.case_ref or item["source_sha256"] != context.source_sha256
                or item["authority"] != AUTHORITY or not _text(item["import_ref"])
                or type(item["import_sha256"]) is not str or len(item["import_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in item["import_sha256"])):
            raise RunError("adjudication_review_binding")
        review = item["review"]
        candidate = deepcopy(assessment); candidate["independent_reviews"] = [review]
        if list(validator.validator.iter_errors(candidate)):
            raise RunError("adjudication_review_contract")
        if (not _text(review["reviewer_ref"]) or review["reviewer_ref"] in seen
                or not _scores(review["eligibility"], review["scores"])):
            raise RunError("adjudication_reviewer_or_scores")
        seen.add(review["reviewer_ref"]); canonical_reviews.append(review)
    issues = []
    def issue(ref, kind):issues.append({"ref": ref, "kind": kind})
    first = assessment["first_pass"]
    for index, flag in enumerate(first["flags"] if first else []):
        issue(f"first_pass_flag:{index}", flag["kind"])
    for index in range(len(canonical_reviews), 2):
        issue(f"missing_review:{index+1}", "missing-independent-review")
    for index, review in enumerate(canonical_reviews):
        if review["eligibility"] == "pending":issue(f"review:{index}:eligibility", "pending-eligibility")
        if review["uncertainty"].strip():issue(f"review:{index}:uncertainty", "reviewer-uncertainty")
    if any(r["eligibility"] != assessment["eligibility"]["decision"] for r in canonical_reviews):
        issue("first_pass_difference:eligibility", "eligibility-difference")
    if first:
        for w in WS:
            if any(r["scores"] is not None and r["scores"][w] != first["scores"][w] for r in canonical_reviews):
                issue(f"first_pass_difference:{w}", "scorer-reviewer-difference")
    if len(canonical_reviews) == 2:
        left, right = canonical_reviews
        if left["eligibility"] != right["eligibility"]:issue("review_disagreement:eligibility", "reviewer-disagreement")
        if left["scores"] is not None and right["scores"] is not None:
            for w in WS:
                if left["scores"][w] != right["scores"][w]:issue(f"review_disagreement:{w}", "reviewer-disagreement")
    return {"status": "pending_human_decision", "summary": "Evidence and independent reviews retained; no final reference label selected.",
            "next_actions": ["Complete missing reviews and record authorized consensus or the designated superior's residual-dispute ruling."],
            "affected_references": [context.case_ref], "artifacts": [], "authority": AUTHORITY,
            "context": asdict(context), "original_assessment": deepcopy(assessment), "original_audit": deepcopy(audit),
            "original_assessment_sha256": digest(assessment), "reviews": deepcopy(reviews), "issues": issues,
            "final_adjudication": None, "reference_standard": "not_established"}


def finalize(brief, decision, resolutions, approval, validator):
    """Record an explicitly approved human decision as a separate artifact.

    Approval binds exact bytes, two independent reviewers and their authorization.
    Software cannot establish that human attestations are true. This method never
    populates human fields in a preserved scorer output or promotes a policy.
    """
    from .assessments import CaseContext
    try:
        rebuilt = prepare(brief["original_assessment"], brief["original_audit"], CaseContext(**brief["context"]), brief["reviews"], validator)
    except (KeyError, TypeError):
        raise RunError("adjudication_brief_contract") from None
    if canonical_json(brief) != canonical_json(rebuilt):
        raise RunError("adjudication_brief_changed")
    if len(brief["reviews"]) != 2:
        raise RunError("two_independent_reviews_required")
    fields = {"authorization_ref", "human_authority", "designated_superior_ref", "mode", "consensus_reviewers",
              "reconciliation_ref", "brief_sha256", "decision_sha256", "resolutions_sha256", "all_material_issues_resolved"}
    if (type(approval) is not dict or set(approval) != fields
            or any(not _text(approval[k]) for k in ("authorization_ref", "human_authority", "designated_superior_ref", "reconciliation_ref"))
            or approval["all_material_issues_resolved"] is not True
            or approval["brief_sha256"] != digest(brief) or approval["decision_sha256"] != digest(decision)
            or approval["resolutions_sha256"] != digest(resolutions)):
        raise RunError("human_finalization_approval_required")
    reviewers = {r["review"]["reviewer_ref"] for r in brief["reviews"]}
    if approval["designated_superior_ref"] not in reviewers or approval["human_authority"] not in reviewers:
        raise RunError("unapproved_human_authority")
    consensus = approval["consensus_reviewers"]
    if type(consensus) is not list or any(type(r) is not str for r in consensus):
        raise RunError("consensus_contract")
    if approval["mode"] == "consensus":
        if len(consensus) != 2 or set(consensus) != reviewers:
            raise RunError("both_consensus_confirmations_required")
    elif approval["mode"] == "superior_ruling":
        if consensus or approval["human_authority"] != approval["designated_superior_ref"]:
            raise RunError("superior_ruling_contract")
    else:
        raise RunError("human_finalization_mode")
    if (type(resolutions) is not list or any(type(r) is not dict or set(r) != {"issue_ref", "rationale"}
            or not _text(r["issue_ref"]) or not _text(r["rationale"]) for r in resolutions)
            or len({r["issue_ref"] for r in resolutions}) != len(resolutions)
            or {r["issue_ref"] for r in resolutions} != {i["ref"] for i in brief["issues"]}):
        raise RunError("material_issues_unresolved")
    candidate = deepcopy(brief["original_assessment"])
    candidate["independent_reviews"] = [deepcopy(r["review"]) for r in brief["reviews"]]
    candidate["final_adjudication"] = decision
    if decision is None or list(validator.validator.iter_errors(candidate)):
        raise RunError("final_adjudication_contract")
    if not _text(decision["rationale"]) or decision["authority"] != approval["human_authority"]:
        raise RunError("final_adjudication_authority")
    if decision["policy_change"] is not False:
        raise RunError("policy_change_requires_separate_approval_and_version")
    if not _scores(decision["eligibility"], decision["scores"]):
        raise RunError("final_adjudication_scores")
    if decision["eligibility"] == "eligible":
        if type(decision["total"]) is not int or decision["total"] != sum(decision["scores"].values()) or decision["complete"] is not (decision["total"] >= 3):
            raise RunError("final_adjudication_arithmetic")
    elif decision["total"] is not None or decision["complete"] is not None:
        raise RunError("final_exclusion_must_not_have_scores")
    return {"status": "recorded_human_decision", "summary": "Authorized human adjudication recorded separately; first-pass evidence unchanged.",
            "next_actions": ["Retain the signed review/approval provenance; apply the separately approved reference-standard protocol."],
            "affected_references": [brief["context"]["case_ref"]], "artifacts": [], "authority": AUTHORITY,
            "case_ref": brief["context"]["case_ref"], "source_sha256": brief["context"]["source_sha256"],
            "original_assessment_sha256": brief["original_assessment_sha256"], "brief_sha256": digest(brief),
            "independent_reviews": deepcopy(brief["reviews"]), "final_adjudication": deepcopy(decision),
            "resolutions": deepcopy(resolutions), "approval": deepcopy(approval),
            "scope": "case-specific; no general rule or model update", "clinical_validation": "not_established_by_software"}
