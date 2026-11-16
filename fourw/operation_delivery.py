"""Literal comparison and adjudication-support outputs; no inference or finality.

These pure plans use the existing workbook generator/read-back verifier. The
coordinator still owns frozen input approval, operation lifecycle and delivery.
Adjudication outputs are unblinded restricted artifacts, never reviewer packets.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from .assessments import CaseContext, WS
from .comparison import compare, dataset_digest
from .delivery import make_plan, _prepare_plan, _finish_plan
from .runs import RunError, canonical_json, _text, _decode
from .specialist import accept_response, adjudication_context


def _json(value):
    canonical_json(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _metadata(run_ref, provenance):
    if not _text(run_ref) or type(provenance) is not dict:
        raise RunError("invalid_operation_delivery_metadata")
    canonical_json(provenance)
    return {"run_ref": run_ref, "clinical_validity": "not_established",
            "human_finalization": "not_performed", "provenance": deepcopy(provenance)}


def _sheet(name, headers, rows, widths):
    return {"name": name, "headers": headers, "rows": rows, "widths": widths}


def comparison_plan(run_ref, left, right, crosswalk, provenance):
    """Recompute comparisons from frozen views; preserve every input record.

    Recorded totals are serialized as explicit JSON so 2, "2", null and an
    exclusion marker remain distinguishable. Nothing is coerced or repaired.
    Ambiguous/unmatched records are visible and never counted as matched pairs.
    """
    metadata = _metadata(run_ref, provenance)
    result = compare(left, right, crosswalk)
    sources = []
    for side, records in (("left", left), ("right", right)):
        for row in records:
            scores = row["scores"]
            sources.append([side, row["case_ref"], scores is not None,
                *[scores[w] if scores is not None else None for w in WS], _json(row["total"]),
                row["rubric_version"], row["reviewer_ref"], row["finality"], row["rationale_kind"]])
    pairs = [[p["left"]["case_ref"], p["right"]["case_ref"], p["matching_basis"],
        *[p["per_w_agreement"][w] for w in WS], p["vector_agreement"], p["total_agreement"],
        p["left_total_discrepancy"], p["right_total_discrepancy"], p["version_difference"]] for p in result["comparisons"]]
    unresolved = [[_json(g["left"]), _json(g["right"]), g["reason"]] for g in result["ambiguous"]]
    unmatched = [[side, ref] for side in ("left", "right") for ref in result[f"unmatched_{side}"]]
    metadata.update(operation="compare", left_digest=dataset_digest(left), right_digest=dataset_digest(right),
        crosswalk_digest=result["crosswalk_digest"], left_count=len(left), right_count=len(right),
        matched_pairs=len(pairs), ambiguous_groups=len(unresolved), unmatched_records=len(unmatched),
        linkage_status="unresolved" if unresolved or unmatched else "fully_accounted_verified_pairs",
        reference_standard="not_established")
    plan = {"version": 1, "operation": "compare", "run_ref": run_ref, "processing_state": "complete",
        "records": {"left": deepcopy(left), "right": deepcopy(right), "crosswalk": deepcopy(crosswalk), "comparison": result},
        "sheets": [
            _sheet("Comparisons", ["Left case", "Right case", "Verified matching basis", *[w.title()+" agreement" for w in WS],
                "Vector agreement", "Recorded total agreement", "Left total discrepancy", "Right total discrepancy", "Version differs"],
                pairs, [26,26,60,15,15,15,15,18,22,22,22,18]),
            _sheet("Source records", ["Side", "Case reference", "Scores supplied", *[w.title() for w in WS],
                "Recorded total (JSON)", "Rubric version", "Reviewer", "Finality", "Rationale provenance"],
                sources, [12,26,18,10,10,10,10,35,35,30,30,35]),
            _sheet("Ambiguous links", ["Left references (JSON)", "Right references (JSON)", "Unresolved reason"], unresolved, [45,45,75]),
            _sheet("Unmatched", ["Side", "Case reference"], unmatched, [15,40]),
            _sheet("Provenance", ["Property", "Value"], [[k, v if type(v) is str else _json(v)] for k,v in metadata.items()], [30,90])]}
    canonical_json(plan)
    return plan


def adjudication_plan(run_ref, request, contexts, raw_response, briefs, validator, provenance):
    """Validate proposals and preserve original scores/reviews alongside them.

    Each source brief keeps its original run; the proposal belongs to this new
    run. Context must match every source identity field except that run reference.
    Missing/invalid proposals have no displayed replacement scores. Agreement or
    a proposed resolution never becomes a human decision or completed reference.
    """
    metadata = _metadata(run_ref, provenance)
    if type(request) is not dict or request.get("operation") != "adjudication_support":
        raise RunError("adjudication_delivery_operation")
    if (type(contexts) is not list or not contexts or any(not isinstance(c, CaseContext) or c.run_ref != run_ref for c in contexts)
            or type(briefs) is not dict or set(briefs) != {c.case_ref for c in contexts}):
        raise RunError("adjudication_delivery_context")
    checked = accept_response(request, contexts, raw_response, validator)
    display = []; originals = []; reviews = []; issues = []
    for context, case, row in zip(contexts, request["cases"], checked["records"]):
        brief = briefs[context.case_ref]
        if case.get("adjudication") != adjudication_context(brief, context, validator):
            raise RunError("adjudication_delivery_brief_binding")
        proposal = row.get("proposal")
        item = {k: deepcopy(row[k]) for k in ("processing_status", "assessment", "audit", "error_code")}
        item["context"] = context
        if "validation_errors" in row:item["validation_errors"] = deepcopy(row["validation_errors"])
        if proposal:
            item.update(assessment=proposal["assessment"], audit=proposal["audit"])
        display.append(item)
        first = brief["original_assessment"]["first_pass"]
        originals.append([context.case_ref, brief["context"]["run_ref"], brief["context"]["eligibility"]["decision"],
            *[first["scores"][w] if first else None for w in WS], first["total"] if first else None,
            _json(first["flags"]) if first else None, brief["original_assessment_sha256"]])
        for envelope in brief["reviews"]:
            review = envelope["review"]
            reviews.append([context.case_ref, review["reviewer_ref"], review["eligibility"],
                *[review["scores"][w] if review["scores"] is not None else None for w in WS],
                review["evidence"], review["uncertainty"], review["recorded_before_discussion"],
                envelope["import_ref"], envelope["import_sha256"]])
        resolutions = {r["issue_ref"]: r for r in proposal["resolutions"]} if proposal else {}
        for issue in brief["issues"]:
            resolution = resolutions.get(issue["ref"])
            issues.append([context.case_ref, issue["ref"], issue["kind"],
                resolution["disposition"] if resolution else "unresolved",
                resolution["rationale"] if resolution else None, "human review required"])
    metadata.update(operation="adjudication_support", request_id=request["request_id"],
                    audience="restricted unblinded adjudication support; never a blinded review packet",
                    reference_standard="not_established")
    plan = make_plan(run_ref, display, validator, metadata)
    plan["operation"] = "adjudication_support"
    plan["sheets"][0]["name"] = "Proposals"
    plan["records"] = deepcopy(checked["records"])
    plan["briefs"] = deepcopy(briefs)
    plan["sheets"].extend([
        _sheet("Original first pass", ["Case reference", "Original run", "Eligibility", *[w.title() for w in WS],
            "Original total", "Original flags (JSON)", "Original assessment SHA256"], originals, [26,30,18,10,10,10,10,15,60,70]),
        _sheet("Human reviews", ["Case reference", "Reviewer", "Eligibility", *[w.title() for w in WS],
            "Recorded rationale", "Recorded uncertainty", "Before discussion", "Import reference", "Import SHA256"],
            reviews, [26,30,18,10,10,10,10,65,50,20,35,70]),
        _sheet("Issues", ["Case reference", "Issue reference", "Issue kind", "Agent proposal status", "Agent rationale", "Finalization status"],
            issues, [26,40,35,25,65,30])])
    canonical_json(plan)
    return plan


def _input(store, spec, ref):
    references = spec["inputs"] + [spec["mapping"]]
    expected = next((r for r in references if r["ref"] == ref), None)
    if expected is None or ref not in store.bindings:
        raise RunError("operation_input_binding")
    try:raw = Path(store.bindings[ref]).read_bytes()
    except OSError:raise RunError("operation_input_unavailable") from None
    if sha256(raw).hexdigest() != expected["sha256"]:
        raise RunError("operation_input_changed")
    return raw


def _operation_plan(store, spec, validator):
    operation = spec["operation"]
    roles = {"compare": {"left", "right", "crosswalk"},
             "adjudication_support": {"request", "contexts", "response", "briefs"}}
    if operation not in roles:raise RunError("operation_delivery_not_supported")
    mapping = _decode(_input(store, spec, spec["mapping"]["ref"]))
    if (type(mapping) is not dict or set(mapping) != {"operation", *roles[operation]}
            or mapping["operation"] != operation or any(not _text(mapping[r]) for r in roles[operation])
            or len({mapping[r] for r in roles[operation]}) != len(roles[operation])
            or not {mapping[r] for r in roles[operation]} <= {i["ref"] for i in spec["inputs"]}):
        raise RunError("operation_mapping_contract")
    data = {r: _input(store, spec, mapping[r]) for r in roles[operation]}
    metadata = {"specification_sha256": dataset_digest(spec), "mapping": spec["mapping"],
                "authority": spec["authority"], "parents": spec["parents"]}
    if operation == "compare":
        left, right, crosswalk = (_decode(data[r]) for r in ("left", "right", "crosswalk"))
        plan = comparison_plan(spec["run_ref"], left, right, crosswalk, metadata)
        # Side qualification is an operation identity, never a crosswalk match.
        expected = {side+":"+row["case_ref"]: {"status":"accepted", "result":{"side":side,"record":deepcopy(row)}}
                    for side, records in (("left",left),("right",right)) for row in records}
    else:
        try:contexts = [CaseContext(**c) for c in _decode(data["contexts"])]
        except (TypeError, KeyError):raise RunError("adjudication_delivery_context") from None
        if any(c.source_sha256 not in {r["sha256"] for r in spec["inputs"]} for c in contexts):
            raise RunError("operation_source_binding")
        plan = adjudication_plan(spec["run_ref"], _decode(data["request"]), contexts, data["response"],
                                 _decode(data["briefs"]), validator, metadata)
        expected = {row["context"]["case_ref"]:{"status":row["processing_status"],"result":row} for row in plan["records"]}
    if spec["selection"] != list(expected):raise RunError("operation_delivery_selection")
    return plan, expected


def record_operation(store, key, device, session, validator):
    """Checkpoint deterministic results of already authorized frozen inputs.

    Actual crosswalk approval/native completion/reviewer provenance is checked by
    the coordinator before this helper. No model or workbook process is launched.
    Repeated calls reconcile identical records, never replace earlier results.
    """
    with store._locked():
        spec, _, _ = store._owned(key, device, session)
        _, expected = _operation_plan(store, spec, validator)
    for ref, item in expected.items():
        store.record(key, device, session, ref, item["status"], item["result"])
    return {"status":"checkpointed", "summary":"Frozen operation results recorded; delivery remains incomplete.",
            "next_actions":["Prepare and verify separate outputs before finalizing."],
            "affected_references":list(expected), "artifacts":[]}


def _planner(store):
    def plan(spec, state, validator):
        value, expected = _operation_plan(store, spec, validator)
        if set(state["records"]) != set(expected):raise RunError("unfinished_records")
        for ref, row in expected.items():
            actual = state["records"][ref]
            if actual["status"] != row["status"] or actual["result"] != row["result"]:
                raise RunError("operation_checkpoint_binding")
        return value
    return plan


def prepare_operation_delivery(store, key, device, session, validator):
    return _prepare_plan(store, key, device, session, validator, _planner(store))


def finish_operation_delivery(store, key, device, session, export_dir, validator):
    return _finish_plan(store, key, device, session, export_dir, validator, _planner(store), "operation-results.json")
