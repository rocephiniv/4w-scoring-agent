"""Reviewer-specific packet plans and independent review contracts.

This module neither selects a sample nor establishes that attestations are true.
The coordinator verifies authorization and original reviewer identity. Workbook
package inspection must precede issuance/import; row validation alone is not a
blinding check. Private manifests must never be delivered with reviewer files.
"""
from copy import deepcopy
from hashlib import sha256
import hmac
import re

from .assessments import WS
from .runs import RunError, canonical_json

AUTHORITY = "4W-Harmonized-v1-docs.1"
HEADERS = ["Review case ID", "Permitted indication", "Eligibility", "What", "When", "Where", "Why",
           "Uncertain? yes/no", "Rationale / uncertainty (one sentence maximum)"]


def _text(value):
    return type(value) is str and bool(value.strip())


def _hex(value, length):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def make_packet(cases, *, packet_id, reviewer_ref, seed, approval_ref, protocol):
    """Construct a public plan and separate restricted linkage manifest.

    Selection is supplied and preserved exactly. The private seed affects only
    reviewer-specific opaque IDs/presentation order, never baseline sampling.
    """
    if (not _hex(packet_id, 32) or not _hex(seed, 64) or not _text(reviewer_ref) or not _text(approval_ref)
            or type(protocol) is not dict or set(protocol) != {"ref", "sha256"}
            or not _text(protocol["ref"]) or not _hex(protocol["sha256"], 64)):
        raise RunError("packet_authorization_contract")
    if type(cases) is not list or not cases:
        raise RunError("empty_packet_selection")
    seen = set(); entries = []
    for case in cases:
        if (type(case) is not dict or set(case) != {"case_ref", "indication", "source_sha256", "source_locator"}
                or not _text(case["case_ref"]) or case["case_ref"] in seen
                or type(case["indication"]) is not str or not _text(case["source_locator"])
                or not _hex(case["source_sha256"], 64)):
            raise RunError("invalid_packet_case", "Supply only verified case reference, permitted text and source identity; never include scores or review labels.")
        seen.add(case["case_ref"])
        binding = canonical_json([packet_id, reviewer_ref, case["case_ref"]])
        token = hmac.new(bytes.fromhex(seed), b"id:" + binding, "sha256").hexdigest()[:32]
        order = hmac.new(bytes.fromhex(seed), b"order:" + binding, "sha256").hexdigest()
        entries.append((order, token, case))
    entries.sort(key=lambda x: x[0])
    if len({e[1] for e in entries}) != len(entries):
        raise RunError("packet_identifier_collision")
    plan = {"version": 1, "sheets": [
        {"name": "Review", "headers": list(HEADERS),
         "rows": [[token, case["indication"], *([None] * 7)] for _, token, case in entries],
         "widths": [38, 70, 18, 10, 10, 10, 10, 22, 70]},
        {"name": "Instructions", "headers": ["Item", "Instructions"], "widths": [24, 95],
         "rows": [["Packet ID", packet_id], ["Authority", AUTHORITY],
                  ["Review", "Review independently before seeing scorer output or another reviewer's decisions."],
                  ["Eligibility", "Enter eligible, excluded, or pending under the approved eligibility policy."],
                  ["Ws", "For eligible cases, enter each W as 0 or 1 under the approved rubric; leave all Ws blank for excluded or pending eligibility."],
                  ["Uncertainty", "Enter yes or no. Preserve uncertainty in the single note; do not turn missing information into an invented fact."],
                  ["Note", "At most one narrative rationale/uncertainty sentence per case; retain the original indication and review case ID."],
                  ["Incomplete", "Leave unanswered review fields blank. Missing answers remain pending; they are never converted to zero."],
                  ["Return", "Return this workbook only through the approved research environment. Reviewer identity and blinding attestations are recorded separately."]]}]}
    private = {"version": 1, "packet_id": packet_id, "reviewer_ref": reviewer_ref,
               "approval_ref": approval_ref, "protocol": deepcopy(protocol), "authority": AUTHORITY,
               "seed": seed, "public_plan_sha256": sha256(canonical_json(plan)).hexdigest(),
               "bindings": [{"review_id": token, "case_ref": case["case_ref"], "source_sha256": case["source_sha256"],
                             "source_locator": case["source_locator"], "indication_sha256": sha256(case["indication"].encode("utf-8")).hexdigest()}
                            for _, token, case in entries]}
    return plan, private


def validate_packet_contract(public, private):
    """Reconstruct the public-only packet; reject malformed or enriched plans.

    This checks consistency, not the truth of approval records. The coordinator
    must additionally bind the manifest to its recorded immutable identity.
    """
    try:
        if (type(private) is not dict or set(private) != {"version", "packet_id", "reviewer_ref", "approval_ref", "protocol", "authority", "seed", "public_plan_sha256", "bindings"}
                or type(private["bindings"]) is not list or not private["bindings"]
                or type(public) is not dict):
            raise RunError("review_packet_contract")
        rows = public["sheets"][0]["rows"]
        if type(rows) is not list or any(type(row) is not list or len(row) != len(HEADERS) or type(row[0]) is not str or type(row[1]) is not str for row in rows):
            raise RunError("review_packet_contract")
        indications = {row[0]: row[1] for row in rows}
        cases = []
        for binding in private["bindings"]:
            if type(binding) is not dict or set(binding) != {"review_id", "case_ref", "source_sha256", "source_locator", "indication_sha256"}:
                raise RunError("review_packet_contract")
            text = indications[binding["review_id"]]
            if sha256(text.encode("utf-8")).hexdigest() != binding["indication_sha256"]:
                raise RunError("review_linkage_identity")
            cases.append({"case_ref": binding["case_ref"], "indication": text,
                          "source_sha256": binding["source_sha256"], "source_locator": binding["source_locator"]})
        expected_public, expected_private = make_packet(cases, packet_id=private["packet_id"], reviewer_ref=private["reviewer_ref"],
            seed=private["seed"], approval_ref=private["approval_ref"], protocol=private["protocol"])
        if canonical_json(public) != canonical_json(expected_public) or canonical_json(private) != canonical_json(expected_private):
            raise RunError("review_packet_contract")
    except (KeyError, TypeError, IndexError, UnicodeError):
        raise RunError("review_packet_contract") from None


def import_rows(rows, public, private, attestation):
    """Validate returned values after separate OOXML/packet identity checks.

    Row order may change, but explicit issued IDs and exact indication text must
    still bind. No note is generated or interpreted as undocumented human intent.
    Sentence-count compliance is attested, not guessed from clinical punctuation.
    """
    validate_packet_contract(public, private)
    required = {"reviewer_ref", "packet_id", "authorization_ref", "independent_before_discussion",
                "unexposed_to_ai_and_other_reviews", "one_sentence_limit_confirmed"}
    if (type(attestation) is not dict or set(attestation) != required
            or not _text(attestation["authorization_ref"])
            or any(attestation[k] is not True for k in required - {"reviewer_ref", "packet_id", "authorization_ref"})):
        raise RunError("review_attestation_required", "Confirm reviewer authorization, independence, exposure and the approved note limit before import.")
    if (attestation["reviewer_ref"] != private["reviewer_ref"] or attestation["packet_id"] != private["packet_id"]
            or private["authority"] != AUTHORITY or sha256(canonical_json(public)).hexdigest() != private["public_plan_sha256"]):
        raise RunError("review_packet_identity")
    expected = {row[0]: row[1] for row in public["sheets"][0]["rows"]}
    bindings = {b["review_id"]: b for b in private["bindings"]}
    if type(rows) is not list or len(rows) != len(expected):
        raise RunError("review_membership_changed")
    returned = {}
    for row in rows:
        if (type(row) is not list or len(row) != len(HEADERS) or type(row[0]) is not str
                or row[0] not in expected or row[0] in returned):
            raise RunError("review_membership_changed")
        if row[1] != expected[row[0]] or sha256(row[1].encode("utf-8")).hexdigest() != bindings[row[0]]["indication_sha256"]:
            raise RunError("review_source_changed")
        returned[row[0]] = row
    reviews = []; pending = 0
    for token, indication in expected.items():
        row = returned[token]; eligibility, scores, uncertain, note = row[2], row[3:7], row[7], row[8]
        blank = lambda value: value is None or (type(value) is str and not value.strip())
        if not blank(eligibility) and eligibility not in ("eligible", "excluded", "pending"):
            raise RunError("invalid_review_eligibility")
        if not blank(uncertain) and uncertain not in ("yes", "no"):
            raise RunError("invalid_review_uncertainty")
        if not blank(note) and type(note) is not str:
            raise RunError("invalid_review_note")
        for score in scores:
            if not blank(score) and (type(score) is not int or score not in (0, 1)):
                raise RunError("invalid_review_score")
        if eligibility in ("excluded", "pending") and not all(blank(s) for s in scores):
            raise RunError("noneligible_review_scores")
        incomplete = (blank(eligibility) or blank(uncertain) or (eligibility == "eligible" and any(blank(s) for s in scores))
                      or (uncertain == "yes" and blank(note)))
        review = None
        if incomplete:
            pending += 1
        else:
            review = {"reviewer_ref": private["reviewer_ref"], "eligibility": eligibility,
                      "scores": dict(zip(WS, scores)) if eligibility == "eligible" else None,
                      "evidence": note or "", "uncertainty": (note or "") if uncertain == "yes" else "",
                      "recorded_before_discussion": True}
        reviews.append({"case_ref": bindings[token]["case_ref"], "review_id": token,
                        "status": "pending" if incomplete else "accepted", "review": review, "returned_values": deepcopy(row[2:])})
    return {"status": "incomplete" if pending else "accepted", "summary": "Independent review values imported; no final adjudication created.",
            "next_actions": ["Resolve pending responses without overwriting this returned original."] if pending else ["Keep reviews separate until authorized reconciliation."],
            "affected_references": [private["packet_id"]], "artifacts": [], "reviews": reviews,
            "pending_count": pending, "attestation": deepcopy(attestation), "final_adjudication": None,
            "reference_standard": "not_established"}
