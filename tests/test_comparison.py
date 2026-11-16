"""Source-bound comparisons only; historical totals remain literal evidence."""
from copy import deepcopy
import pytest

from fourw.comparison import compare, dataset_digest
from fourw.runs import RunError


def record(ref, scores=None, total=2):
    return {"case_ref": ref, "scores": scores or {"what": 1, "when": 0, "where": 1, "why": 0},
            "total": total, "rubric_version": "historical-test-version", "reviewer_ref": "test-reviewer",
            "finality": "historical-unverified", "rationale_kind": "unavailable"}


def fixture():
    left = [record("l1"), record("l2"), record("l3"), record("l4")]
    right = [record("r1", total=4), record("r2"), record("r3"), record("r4")]
    crosswalk = {"version": "test-1", "approval_ref": "verified-crosswalk-approval",
                 "left_digest": dataset_digest(left), "right_digest": dataset_digest(right),
                 "matches": [{"left": "l1", "right": "r1", "basis": "verified source crosswalk"}],
                 "ambiguous": [{"left": ["l2", "l3"], "right": ["r2"], "reason": "Unresolved linkage"}]}
    return left, right, crosswalk


def test_verified_pairs_only_and_historical_total_is_not_repaired():
    left, right, crosswalk = fixture(); before = deepcopy((left, right, crosswalk))
    result = compare(left, right, crosswalk)
    assert len(result["comparisons"]) == 1
    row = result["comparisons"][0]
    assert row["vector_agreement"] is True
    assert row["total_agreement"] is False
    assert row["right"]["total"] == 4
    assert row["right_total_discrepancy"] is True
    assert result["ambiguous"] == crosswalk["ambiguous"]
    assert result["unmatched_left"] == ["l4"]
    assert result["unmatched_right"] == ["r3", "r4"]
    assert result["reference_standard"] == "not_established"
    assert (left, right, crosswalk) == before


@pytest.mark.parametrize("mutation,code", [
    (lambda l, r, c: l[0].update(total=3), "dataset_identity_mismatch"),
    (lambda l, r, c: c.update(approval_ref=""), "crosswalk_approval_required"),
    (lambda l, r, c: c["matches"][0].update(left="unknown"), "crosswalk_unknown_record"),
    (lambda l, r, c: c["matches"].append({"left": "l1", "right": "r3", "basis": "x"}), "crosswalk_duplicate_link"),
    (lambda l, r, c: c["ambiguous"][0]["left"].append("l1"), "crosswalk_conflict"),
    (lambda l, r, c: c["matches"][0].update(basis=""), "crosswalk_shape"),
])
def test_unverified_or_conflicting_relationships_block(mutation, code):
    left, right, crosswalk = fixture(); mutation(left, right, crosswalk)
    with pytest.raises(RunError, match=code):
        compare(left, right, crosswalk)


def test_duplicate_record_ids_are_not_merged():
    left, right, crosswalk = fixture(); left.append(deepcopy(left[0]))
    crosswalk["left_digest"] = dataset_digest(left)
    with pytest.raises(RunError, match="duplicate_record_id"):
        compare(left, right, crosswalk)


def test_missing_scores_are_unavailable_not_zero_or_agreement():
    left, right, crosswalk = fixture(); right[0]["scores"] = None; right[0]["total"] = None
    crosswalk["right_digest"] = dataset_digest(right)
    result = compare(left, right, crosswalk)["comparisons"][0]
    assert result["vector_agreement"] is None
    assert result["total_agreement"] is None
    assert all(v is None for v in result["per_w_agreement"].values())


def test_no_crosswalk_matches_means_no_text_or_date_guessing():
    left, right, crosswalk = fixture(); crosswalk["matches"] = []; crosswalk["ambiguous"] = []
    result = compare(left, right, crosswalk)
    assert result["comparisons"] == []
    assert len(result["unmatched_left"]) == 4 and len(result["unmatched_right"]) == 4


def test_changed_rubric_and_rationale_provenance_remain_visible():
    left, right, crosswalk = fixture(); right[0]["rubric_version"] = "4W-Harmonized-v1-docs.1"
    right[0]["rationale_kind"] = "agent-generated"
    crosswalk["right_digest"] = dataset_digest(right)
    row = compare(left, right, crosswalk)["comparisons"][0]
    assert row["version_difference"] is True
    assert row["right"]["rationale_kind"] == "agent-generated"


@pytest.mark.parametrize("change,code", [
    (lambda c: c.update(version=""), "crosswalk_shape"),
    (lambda c: c.update(matches={}), "crosswalk_shape"),
    (lambda c: c["ambiguous"][0].update(reason=""), "crosswalk_shape"),
    (lambda c: c["ambiguous"][0].update(right=["unknown"]), "crosswalk_unknown_record"),
])
def test_malformed_crosswalk_fields_fail(change, code):
    left, right, crosswalk = fixture(); change(crosswalk)
    with pytest.raises(RunError, match=code):
        compare(left, right, crosswalk)


@pytest.mark.parametrize("change,code", [
    (lambda rows: rows[0].update(reviewer_ref=""), "comparison_record_shape"),
    (lambda rows: rows[0]["scores"].update(what=True), "comparison_score_shape"),
])
def test_invalid_comparison_records_are_not_coerced(change, code):
    left, right, crosswalk = fixture(); change(left); crosswalk["left_digest"] = dataset_digest(left)
    with pytest.raises(RunError, match=code):
        compare(left, right, crosswalk)


def test_comparison_requires_record_list():
    left, right, crosswalk = fixture(); left = {}; crosswalk["left_digest"] = dataset_digest(left)
    with pytest.raises(RunError, match="comparison_dataset_shape"):
        compare(left, right, crosswalk)
