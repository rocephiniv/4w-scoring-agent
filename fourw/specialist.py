"""Native-specialist request/response boundary. No inference or file mutation.

Construction mode accepts explicitly synthetic tests only. The coordinator owns
source/eligibility/authorization checks and native invocation. Paths, dates,
hospital fields, source hashes, hidden labels and reviewer identities are not
sent as case inputs. Full frozen canonical rules and approved lessons are sent.
"""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from .adjudication import prepare as prepare_adjudication
from .assessments import CaseContext, SCHEMA_SHA256
from .knowledge import AUTHORITY, DOCUMENTS
from .runs import RunError, canonical_json, _decode, _text, _hash

SELECTION = "KNOWLEDGE_BASE/HARMONIZED_V1/AUTHORITY_SELECTION.json"
SCHEMA = "KNOWLEDGE_BASE/HARMONIZED_V1/OUTPUT_SCHEMA.json"
ELIGIBILITY = "KNOWLEDGE_BASE/COHORTS_AND_DATA_DICTIONARY.md"
PROMPT = "KNOWLEDGE_BASE/HARMONIZED_V1/SCORER_PROMPT.md"
RULE_IDS = "KNOWLEDGE_BASE/HARMONIZED_V1/RULE_IDS.csv"
OFFICIAL_TEXT = "KNOWLEDGE_BASE/AUTHORITY/4W-Harmonized-v1/OFFICIAL_TEXT.md"
CONTEXT_DOCUMENTS = {*DOCUMENTS, SELECTION, ELIGIBILITY, PROMPT, RULE_IDS, OFFICIAL_TEXT}
REQUIRED = {*CONTEXT_DOCUMENTS, SELECTION, SCHEMA}
PROTOCOL = "fourw-native-synthetic-v1"
MAX_REQUEST_BYTES = 128_000  # Local batch guard, not a claim about model context capacity.


def digest(value):
    return sha256(canonical_json(value)).hexdigest()


def load_authority(project, expected_hashes):
    if type(expected_hashes) is not dict or set(expected_hashes) != REQUIRED or any(not _hash(v) for v in expected_hashes.values()):
        raise RunError("specialist_authority_manifest")
    contents = {}
    for ref in sorted(REQUIRED):
        try:raw = (Path(project) / ref).read_bytes()
        except OSError:raise RunError("specialist_authority_unavailable") from None
        if sha256(raw).hexdigest() != expected_hashes[ref]:raise RunError("specialist_authority_changed")
        try:contents[ref] = raw.decode("utf-8-sig")
        except UnicodeError:raise RunError("specialist_authority_encoding") from None
    selected = _decode(contents[SELECTION])
    if (type(selected) is not dict or type(selected.get("canonical_owners")) is not dict or selected.get("selected_authority") != AUTHORITY
            or selected.get("status") != "sole-active-prospective-scoring-and-adjudication-authority"
            or selected.get("canonical_owners", {}).get("scoring") != "KNOWLEDGE_BASE/PROSPECTIVE_4W_RUBRIC.md"
            or selected.get("canonical_owners", {}).get("adjudication") != "KNOWLEDGE_BASE/ADJUDICATION_AND_CALIBRATION.md"
            or selected.get("canonical_owners", {}).get("eligibility") != ELIGIBILITY
            or expected_hashes[SCHEMA] != SCHEMA_SHA256):
        raise RunError("specialist_authority_selection")
    return {"authority": AUTHORITY, "hashes": deepcopy(expected_hashes),
            "documents": [{"ref": ref, "sha256": expected_hashes[ref], "text": contents[ref]} for ref in sorted(CONTEXT_DOCUMENTS)],
            "context_scope": "Synthetic build conformance only. Canonical scoring/adjudication documents govern; the official paragraph register supplies exact referenced examples, and the rule-ID index supplies traceability. No historical permission is imported. The coordinator separately enforces baseline and research gates; this request does not authorize a baseline or patient-case run.",
            "assessment_schema": _decode(contents[SCHEMA])}


def _context_list(contexts):
    if type(contexts) is not list or not contexts or any(not isinstance(c, CaseContext) for c in contexts):
        raise RunError("specialist_case_contract")
    if len({c.case_ref for c in contexts}) != len(contexts) or len({c.run_ref for c in contexts}) != 1:
        raise RunError("specialist_case_identity")
    for c in contexts:
        if (not _text(c.case_ref) or not _text(c.run_ref) or type(c.indication) is not str
                or not _hash(c.source_sha256) or not _text(c.source_locator)
                or type(c.eligibility) is not dict or set(c.eligibility) != {"decision", "reason", "review_status"}
                or c.eligibility["decision"] != "eligible" or not _text(c.eligibility["reason"])
                or c.eligibility["review_status"] not in ("first-pass", "pending-human-review", "human-confirmed")):
            raise RunError("specialist_eligible_cases_only")


def adjudication_context(brief, context, validator):
    """Bind preserved evidence to the same source/case in a separate proposal run."""
    try:
        original = CaseContext(**brief["context"])
        rebuilt = prepare_adjudication(brief["original_assessment"], brief["original_audit"], original, brief["reviews"], validator)
        bound = asdict(original); bound["run_ref"] = context.run_ref
        if brief != rebuilt or bound != asdict(context):raise RunError("specialist_adjudication_context")
    except (KeyError, TypeError):raise RunError("specialist_adjudication_context") from None
    return {"brief_sha256": digest(brief), "first_pass": deepcopy(brief["original_assessment"]["first_pass"]),
        "reviews": [{"review_ref": f"review-{i+1}", **{k: deepcopy(v) for k, v in r["review"].items() if k != "reviewer_ref"}}
                    for i, r in enumerate(brief["reviews"])], "issues": deepcopy(brief["issues"])}


def make_request(project, expected_hashes, contexts, operation, ledger, release_id, release_sha256,
                 authorization, validator, *, new_run=True, briefs=None):
    """Build complete, bounded context; never trim rules to fit a batch."""
    _context_list(contexts)
    if operation not in ("initial_score", "rescore", "adjudication_support"):
        raise RunError("specialist_operation")
    if (type(authorization) is not dict or set(authorization) != {"scope", "authorization_ref", "rescore_authorization_ref"}
            or authorization["scope"] != "synthetic_build_only" or not _text(authorization["authorization_ref"])
            or (operation == "rescore" and not _text(authorization["rescore_authorization_ref"]))):
        raise RunError("specialist_synthetic_authorization_required")
    authority = load_authority(project, expected_hashes)
    loaded = ledger.load(release_id, release_sha256, {ref: expected_hashes[ref] for ref in DOCUMENTS}, new_run=new_run)
    knowledge = {k: deepcopy(loaded[k]) for k in ("release_id", "sha256", "authority", "documents")}
    knowledge["lessons"] = [{k: deepcopy(lesson[k]) for k in ("lesson_id", "text", "examples", "documents", "certainty", "rationale_kind")} for lesson in loaded["lessons"]]
    cases = [{"case_ref": c.case_ref, "indication": c.indication} for c in contexts]
    if operation == "adjudication_support":
        if type(briefs) is not dict or set(briefs) != {c.case_ref for c in contexts}:
            raise RunError("specialist_adjudication_context")
        for case, context in zip(cases, contexts):
            case["adjudication"] = adjudication_context(briefs[context.case_ref], context, validator)
    elif briefs is not None:
        raise RunError("blinded_scoring_must_not_receive_reviews")
    body = {"protocol": PROTOCOL, "scope": "synthetic_build_only", "operation": operation,
            "authority": authority, "knowledge": knowledge, "cases": cases,
            "output_contract": {"outer_fields": ["request_id", "records"],
                "record_fields": ["case_ref", "first_pass", "per_w"] + (["resolutions"] if briefs is not None else []),
                "first_pass": "Use the supplied canonical schema's $defs/scoring. finalized_complete is always null.",
                "per_w": "Exact what/when/where/why keys. Each value: basis (documented/absent/uncertain/insufficient), spans (list of {start,end,quote}, Unicode code-point [start,end)), rule_ids, rationale. Positive credit needs an exact permitted-source span. In this audit contract, absent means a settled zero under the canonical rubric, including clearly nonqualifying documentation; use empty spans and explain the settled exclusion in rationale. Uncertain and insufficient are reserved for unresolved evidence: both require score 0, an affected-W flag, and null completeness fields. Do not escalate an ordinary settled zero merely to fit an audit category. For every span, end must equal start plus the Unicode code-point length of quote, and indication[start:end] must exactly equal quote. Never change source text or clinical decisions to repair a span.",
                "resolutions": "For adjudication_support only: one {issue_ref, disposition (proposed/unresolved), rationale} per supplied issue. Proposals require human review; never make a human final decision.",
                "failure": "If unable to perform the contract, return an empty records list; never invent missing decisions or evidence."}}
    request = {"request_id": digest(body), **body}
    if len(canonical_json(request)) > MAX_REQUEST_BYTES:
        raise RunError("specialist_request_too_large", "Reduce the batch without truncating required rules or changing source text.")
    return request


def _assessment(context, first_pass, per_w):
    return ({"case_ref": context.case_ref, "run_ref": context.run_ref, "rubric_version": "4W-Harmonized-v1",
             "eligibility": deepcopy(context.eligibility), "first_pass": deepcopy(first_pass), "independent_reviews": [], "final_adjudication": None},
            {"case_ref": context.case_ref, "run_ref": context.run_ref, "source_sha256": context.source_sha256,
             "source_locator": context.source_locator, "authority": AUTHORITY, "per_w": deepcopy(per_w)})


def noneligible_record(context, validator):
    """Preserve eligibility without sending unnecessary indication text to a model."""
    if type(context.eligibility) is not dict or context.eligibility.get("decision") not in ("excluded", "pending"):
        raise RunError("noneligible_context_required")
    assessment, audit = _assessment(context, None, None)
    if validator.validate_first_pass(assessment, audit, context)["status"] != "accepted":
        raise RunError("noneligible_record_contract")
    return {"context": asdict(context), "processing_status": "accepted", "assessment": assessment, "audit": audit, "error_code": None}


def accept_response(request, contexts, response_bytes, validator):
    """Validate model output without repairing scores or accepting extra records.

The coordinator first verifies actual named invocation/completion and absence of
observed unauthorized actions. This function proves neither of those conditions.
Malformed response/binding fails the batch; missing/invalid individual records
remain explicit processing failures without substituting zero scores.
"""
    _context_list(contexts)
    if (type(request) is not dict or request.get("request_id") != digest({k:v for k,v in request.items() if k != "request_id"})
            or request.get("protocol") != PROTOCOL or request.get("scope") != "synthetic_build_only"
            or request.get("operation") not in ("initial_score", "rescore", "adjudication_support")
            or type(request.get("cases")) is not list or any(type(c) is not dict for c in request["cases"])
            or [(c.get("case_ref"), c.get("indication")) for c in request.get("cases", [])] != [(c.case_ref, c.indication) for c in contexts]):
        raise RunError("specialist_request_identity")
    response = _decode(response_bytes)
    if (type(response) is not dict or set(response) != {"request_id", "records"} or response["request_id"] != request["request_id"]
            or type(response["records"]) is not list):
        raise RunError("specialist_response_contract")
    seen = {}; expected = {c.case_ref for c in contexts}
    for record in response["records"]:
        if type(record) is not dict or type(record.get("case_ref")) is not str or record["case_ref"] not in expected or record["case_ref"] in seen:
            raise RunError("specialist_response_binding")
        seen[record["case_ref"]] = record
    results = []; adjudication = request["operation"] == "adjudication_support"
    for context, input_case in zip(contexts, request["cases"]):
        row = seen.get(context.case_ref)
        result = {"context": asdict(context), "processing_status": "failed", "assessment": None, "audit": None, "error_code": "missing_specialist_record"}
        if row is not None:
            fields = {"case_ref", "first_pass", "per_w"} | ({"resolutions"} if adjudication else set())
            result["error_code"] = "specialist_record_contract"
            if set(row) == fields:
                assessment, audit = _assessment(context, row["first_pass"], row["per_w"])
                check = validator.validate_first_pass(assessment, audit, context)
                if check["status"] != "accepted":
                    result["validation_errors"] = deepcopy(check["errors"])
                if check["status"] == "accepted":
                    if adjudication:
                        resolutions = row["resolutions"]
                        issues = {i["ref"] for i in input_case["adjudication"]["issues"]}
                        if (type(resolutions) is not list or any(type(r) is not dict or set(r) != {"issue_ref", "disposition", "rationale"}
                                or not _text(r["issue_ref"]) or r["disposition"] not in ("proposed", "unresolved") or not _text(r["rationale"]) for r in resolutions)
                                or len({r["issue_ref"] for r in resolutions}) != len(resolutions) or {r["issue_ref"] for r in resolutions} != issues):
                            result["error_code"] = "specialist_resolution_contract"
                        else:
                            result.update(processing_status="accepted", error_code=None,
                                proposal={"assessment": assessment, "audit": audit, "resolutions": deepcopy(resolutions),
                                          "brief_sha256": input_case["adjudication"]["brief_sha256"], "requires_human_review": True,
                                          "final_adjudication": None, "rationale_kind": "agent_explanation"})
                    else:
                        result.update(processing_status="accepted", assessment=assessment, audit=audit, error_code=None)
        results.append(result)
    return {"status": "complete" if all(r["processing_status"] == "accepted" for r in results) else "complete_with_failures",
            "summary": "Native response contract checked; no human finality or clinical validation established.",
            "next_actions": ["Preserve the first response and failures; follow the approved review protocol."],
            "affected_references": [c.case_ref for c in contexts], "artifacts": [], "request_id": request["request_id"],
            "operation": request["operation"], "records": results, "clinical_validity": "not_established"}
