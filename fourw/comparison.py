"""Compare frozen assessment views through explicit verified relationships only."""
from copy import deepcopy
from hashlib import sha256

from .runs import RunError, canonical_json

WS = ("what", "when", "where", "why")


def dataset_digest(records):
    return sha256(canonical_json(records)).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _index(records):
    if type(records) is not list:
        raise RunError("comparison_dataset_shape")
    result = {}
    for record in records:
        if (type(record) is not dict or set(record) != {"case_ref", "scores", "total", "rubric_version", "reviewer_ref", "finality", "rationale_kind"}
                or any(not _text(record[k]) for k in ("case_ref", "rubric_version", "reviewer_ref", "finality", "rationale_kind"))):
            raise RunError("comparison_record_shape")
        scores = record["scores"]
        if scores is not None and (type(scores) is not dict or set(scores) != set(WS)
                                   or any(v is not None and (type(v) is not int or v not in (0, 1)) for v in scores.values())):
            raise RunError("comparison_score_shape")
        # Historical raw totals may disagree or be nonnumeric. Preserve them;
        # comparisons below only use actual numeric values, never coercion.
        if record["case_ref"] in result:
            raise RunError("duplicate_record_id")
        result[record["case_ref"]] = record
    return result


def _complete_scores(record):
    return record["scores"] is not None and all(type(record["scores"][w]) is int for w in WS)


def _pair(left, right):
    per_w = {w: (left["scores"][w] == right["scores"][w]
                 if left["scores"] is not None and right["scores"] is not None
                 and left["scores"][w] is not None and right["scores"][w] is not None else None) for w in WS}
    totals_available = type(left["total"]) is int and type(right["total"]) is int
    return {"left": deepcopy(left), "right": deepcopy(right),
            "per_w_agreement": per_w,
            "vector_agreement": all(per_w.values()) if all(v is not None for v in per_w.values()) else None,
            "total_agreement": left["total"] == right["total"] if totals_available else None,
            "left_total_discrepancy": left["total"] != sum(left["scores"].values()) if _complete_scores(left) and type(left["total"]) is int else None,
            "right_total_discrepancy": right["total"] != sum(right["scores"].values()) if _complete_scores(right) and type(right["total"]) is int else None,
            "version_difference": left["rubric_version"] != right["rubric_version"]}


def compare(left_records, right_records, crosswalk):
    """Coordinator must verify approval and linkage basis before calling.

    This function verifies frozen content identities and relationship consistency;
    it cannot establish the truth of a human-entered crosswalk or reference label.
    No matching is inferred from text, dates, row order, filenames, or score values.
    """
    required = {"version", "approval_ref", "left_digest", "right_digest", "matches", "ambiguous"}
    if type(crosswalk) is not dict or set(crosswalk) != required or not _text(crosswalk["version"]):
        raise RunError("crosswalk_shape")
    if not _text(crosswalk["approval_ref"]):
        raise RunError("crosswalk_approval_required")
    if dataset_digest(left_records) != crosswalk["left_digest"] or dataset_digest(right_records) != crosswalk["right_digest"]:
        raise RunError("dataset_identity_mismatch")
    left, right = _index(left_records), _index(right_records)
    if type(crosswalk["matches"]) is not list or type(crosswalk["ambiguous"]) is not list:
        raise RunError("crosswalk_shape")
    used_left, used_right, pairs = set(), set(), []
    for match in crosswalk["matches"]:
        if (type(match) is not dict or set(match) != {"left", "right", "basis"}
                or any(not _text(match[k]) for k in match)):
            raise RunError("crosswalk_shape")
        l, r = match["left"], match["right"]
        if l not in left or r not in right:
            raise RunError("crosswalk_unknown_record")
        if l in used_left or r in used_right:
            raise RunError("crosswalk_duplicate_link")
        used_left.add(l); used_right.add(r)
        comparison = _pair(left[l], right[r]); comparison["matching_basis"] = match["basis"]
        pairs.append(comparison)
    for group in crosswalk["ambiguous"]:
        if (type(group) is not dict or set(group) != {"left", "right", "reason"} or not _text(group["reason"])
                or any(type(group[side]) is not list or not group[side] or any(not _text(v) for v in group[side]) for side in ("left", "right"))):
            raise RunError("crosswalk_shape")
        for side, index, used in (("left", left, used_left), ("right", right, used_right)):
            values = group[side]
            if any(v not in index for v in values):
                raise RunError("crosswalk_unknown_record")
            if len(set(values)) != len(values) or used.intersection(values):
                raise RunError("crosswalk_conflict")
            used.update(values)
    return {"status": "accepted", "summary": "Only explicitly verified crosswalk pairs compared.",
            "next_actions": ["Resolve ambiguous links through source evidence before comparison."] if crosswalk["ambiguous"] else [],
            "affected_references": [], "artifacts": [], "comparisons": pairs,
            "ambiguous": deepcopy(crosswalk["ambiguous"]),
            "unmatched_left": [key for key in left if key not in used_left],
            "unmatched_right": [key for key in right if key not in used_right],
            "crosswalk_digest": dataset_digest(crosswalk), "reference_standard": "not_established"}
