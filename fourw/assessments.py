"""Canonical structural/semantic checks, never a clinical scoring algorithm."""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from jsonschema import Draft202012Validator

WS = ("what", "when", "where", "why")
SCHEMA_SHA256 = "5b628ed08c75edfa9d765db7cfd96265e740b9d546b14df84dadbd36e48237af"


@dataclass(frozen=True)
class CaseContext:
    case_ref: str
    run_ref: str
    indication: str
    source_sha256: str
    source_locator: str
    eligibility: dict


class AssessmentValidator:
    def __init__(self, schema_path, expected_sha256=SCHEMA_SHA256):
        data = Path(schema_path).read_bytes()
        if expected_sha256 != SCHEMA_SHA256 or sha256(data).hexdigest() != expected_sha256:
            raise ValueError("schema_identity")
        schema = json.loads(data)
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema)
        self.rule_ids = set(schema["$defs"]["scoring"]["properties"]["rule_ids"]["items"]["enum"])
        self.schema_sha256 = expected_sha256

    def validate_first_pass(self, payload, audit, context: CaseContext):
        """Validate a scorer's proposal. Never return repaired/substitute scores.

        Exact source spans use Python/Unicode code-point offsets [start, end).
        These checks prove cited text exists, not that a clinical inference is
        correct. Canonical evidence may be a paraphrase; audit spans quote source.
        """
        errors = []

        def error(code, path):
            errors.append({"code": code, "path": path})

        for issue in self.validator.iter_errors(payload):
            # Do not include jsonschema's message or instance: either can expose
            # indication text. The path identifies an actionable contract field.
            error("schema", "/" + "/".join(str(p) for p in issue.absolute_path))
        if errors:
            return self._result(errors, context, False)
        if payload["case_ref"] != context.case_ref:
            error("case_identity", "/case_ref")
        if payload["run_ref"] != context.run_ref:
            error("run_identity", "/run_ref")
        if payload["eligibility"] != context.eligibility:
            error("eligibility_identity", "/eligibility")
        if payload["independent_reviews"] or payload["final_adjudication"] is not None:
            error("fabricated_human_fields", "/human_review")

        first = payload["first_pass"]
        if first is not None:
            if first["total"] != sum(first["scores"].values()):
                error("arithmetic", "/first_pass/total")
            expected = None if first["flags"] else first["total"] >= 3
            if first["provisional_complete"] is not expected:
                error("completeness", "/first_pass/provisional_complete")

        expected_keys = {"case_ref", "run_ref", "source_sha256", "source_locator", "authority", "per_w"}
        if type(audit) is not dict or set(audit) != expected_keys:
            error("audit_shape", "/audit")
            return self._result(errors, context, False)
        for field, expected in (("case_ref", context.case_ref), ("run_ref", context.run_ref),
                                ("source_sha256", context.source_sha256), ("source_locator", context.source_locator),
                                ("authority", "4W-Harmonized-v1-docs.1")):
            if audit[field] != expected:
                error("source_identity", "/audit/" + field)

        if first is None:
            if audit["per_w"] is not None:
                error("eligibility_scoring_separation", "/audit/per_w")
            return self._result(errors, context, True)
        if type(audit["per_w"]) is not dict or set(audit["per_w"]) != set(WS):
            error("audit_shape", "/audit/per_w")
            return self._result(errors, context, False)

        flagged = {w for flag in first["flags"] for w in flag["affected_ws"]}
        for w in WS:
            detail = audit["per_w"][w]; location = "/audit/per_w/" + w
            if (type(detail) is not dict or set(detail) != {"basis", "spans", "rule_ids", "rationale"}
                    or detail["basis"] not in ("documented", "absent", "uncertain", "insufficient")
                    or type(detail["spans"]) is not list
                    or type(detail["rule_ids"]) is not list
                    or not isinstance(detail["rationale"], str) or not detail["rationale"].strip()):
                error("audit_shape", location)
                continue
            rules = detail["rule_ids"]
            if (not rules or any(not isinstance(r, str) or r not in self.rule_ids or r not in first["rule_ids"] for r in rules)
                    or len(set(rules)) != len(rules)):
                error("audit_rules", location + "/rule_ids")
            value = first["scores"][w]
            if detail["basis"] in ("uncertain", "insufficient") and (value != 0 or w not in flagged):
                error("uncertainty", location + "/basis")
            if detail["basis"] == "absent" and value != 0:
                error("absence_credit", location + "/basis")
            if value == 1 and (detail["basis"] != "documented" or not detail["spans"] or not first["evidence"][w].strip()):
                error("positive_evidence_required", location + "/spans")
            for index, span in enumerate(detail["spans"]):
                path = location + f"/spans/{index}"
                if (type(span) is not dict or set(span) != {"start", "end", "quote"}
                        or type(span["start"]) is not int or type(span["end"]) is not int
                        or not isinstance(span["quote"], str)
                        or not 0 <= span["start"] < span["end"] <= len(context.indication)):
                    error("evidence_span", path)
                elif context.indication[span["start"]:span["end"]] != span["quote"]:
                    error("evidence_fidelity", path)
        return self._result(errors, context, bool(first["flags"]))

    def _result(self, errors, context, human_review):
        return {"status": "failed" if errors else "accepted",
                "summary": "Assessment contract failed; no replacement scores produced." if errors else "Canonical structure and linked evidence checks passed.",
                "next_actions": ["Reconcile the listed contract fields; do not invent missing clinical decisions."] if errors else [],
                "affected_references": [context.case_ref], "artifacts": [], "errors": errors,
                "schema_sha256": self.schema_sha256, "clinical_validity": "not_established",
                "case_escalation_required": human_review or bool(errors),
                "human_review_required": True if human_review or errors else None,
                "review_coverage": "Apply the approved run protocol; an unflagged result does not waive sampled or calibration review."}
