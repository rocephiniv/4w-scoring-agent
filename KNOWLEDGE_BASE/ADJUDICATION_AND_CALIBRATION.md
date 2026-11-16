# Harmonized human adjudication and calibration

Version: 4W-Harmonized-v1-docs.1. This document is the **canonical adjudication component of the sole active prospective authority**, selected in [AUTHORITY_SELECTION.json](HARMONIZED_V1/AUTHORITY_SELECTION.json); the [canonical rubric](PROSPECTIVE_4W_RUBRIC.md) owns scoring. The official record [research reference omitted from this distribution] is preserved foundation/provenance. Future review coverage is specified only in the baseline protocol [research reference omitted from this distribution]. Selection is not permission to run an audit or adjudication agent.

## Current fail-closed pathway

1. Identify exact permitted source/case and cohort, and require 4W-Harmonized-v1-docs.1. Follow the [eligibility and accounting policy](COHORTS_AND_DATA_DICTIONARY.md#harmonized-prospective-documentation-boundary) before W scoring; older rubrics are retired from future operational use.
2. Excluded or pending eligibility has no W scores; it is not a four-zero indication. Represent it according to the [output schema](HARMONIZED_V1/OUTPUT_SCHEMA.json) and the eligibility policy.
3. For eligible cases, score settled information independently. Distinguish clear absence from ambiguous wording, rule conflict, unlisted exception, uncertain negation/history linkage, reviewer disagreement and clerical discrepancy.
4. Keep provisional binary Ws and their arithmetic total under the [scoring output rules](PROSPECTIVE_4W_RUBRIC.md#6-scoring-pending-adjudication-and-reporting). Unsupported uncertain credit is provisionally 0 with the affected W flagged, not a final absence label. Do not suppress a settled positive merely because another W is uncertain. Record the affected W, competing interpretations and minimal evidence. Ordinary clear absence is 0 without automatic escalation.
5. A flagged first pass has neither a provisional completeness classification nor a finalized completeness classification; both completeness fields remain null. Unflagged first-pass completeness is a provisional algorithmic classification, not human validation; its finalized_complete remains null. Cohort accounting follows the linked eligibility policy.
6. Two clinicians record independent judgments before seeing each other's decisions or the scorer output. Preserve both reviews, then record consensus separately. Remaining disputes go to the user/designated clinical superior, with exact evidence, competing rules, rationale and scope. A case-specific ruling does not create a general exception.
7. Store final adjudication separately from first pass. All material issues must be resolved before a final reference label/completeness classification. Preserve first-pass values and flags unchanged; no reviewer may silently overwrite a prior label or repair a historical total. A total-only reviewer edit cannot establish which W changed. Scorer outputs must never fabricate independent human reviews or final adjudication; these fields stay empty/null until actual human review.
8. New exceptions require explicit clinical approval, a versioned rule and paired tests before automatic use. Unresolved policy stays pending; do not expand exceptions ad hoc. Historical scores remain unchanged.

The [output schema](HARMONIZED_V1/OUTPUT_SCHEMA.json) encodes the structural separation; the [prompt](HARMONIZED_V1/SCORER_PROMPT.md) loads this policy. Final eligible adjudication requires final Ws, their correct total and completeness, named human authority and rationale. Final exclusions have null scores, total and completeness. Pending eligibility has no first-pass scoring or final adjudication. A later authorized harness must check arithmetic, evidence fidelity and version agreement as well as schema conformance; structural conformance alone is not clinical validity. These requirements were already specified in the prior prompt/schema and are consolidated here without changing their meaning.

## Current unresolved boundary register

|ID|Boundary|Disposition|
|---|---|---|
|HOLD-01|Automatic irreducible/nonreducible-hernia Why exception|Historical decision retained; human confirmation before automatic prospective exception. Explicit documented concern still qualifies normally.|
|HOLD-02|Automatic LLQ + divertic expansion|Historical decision retained; uncertain truncation is not silently expanded.|
|HOLD-03|New-onset/subacute/chronic wording or uncertain temporal attachment|Use an unambiguous official onset/acuity principle normally; genuinely unresolved meaning requires affected-W adjudication, not a blanket keyword rule.|
|HOLD-04|Bare GIB or other uncertain localization|Specific official lower-GI wording applies; generic or unclear anatomy requires adjudication if unresolved under the general principle.|
|HOLD-05|Weak pattern/device/history linkage or conflicting hierarchy|No automatic rescue of unlocalized pain; exact official permissions do not authorize weak analogies.|
|HOLD-06|Negation/resolved-episode attachment|Preserve literal text and temporal context; no invented active episode.|
|HOLD-07|Source bookkeeping or reviewer inconsistency|Retain source discrepancies; do not infer per-W truth from a total or rationale that omits/adds facts.|

Prior PEND categories are not all still unresolved: the official established/suspected matrix, systemic Where permissions, explicit device questions, source-of-sepsis purpose and active reassessment resolve their respective policy-level holds. Case-specific ambiguity can still require review. See crosswalk [research reference omitted from this distribution].

## Current documentation fixtures

Calibration fixtures [research reference omitted from this distribution] reproduce all 14 official examples and add labelled synthetic contrasts. These are expected documentation outcomes, not model predictions or rescored historical cases. HOLD fixtures contain provisional vectors and must not be promoted to gold labels. Every fixture records its source/rule IDs. Human calibration and model conformance have not been performed merely by writing fixtures.

The baseline protocol [research reference omitted from this distribution] owns review coverage, sampling and later validation/agent gates; follow it without maintaining a second coverage rule here.

## Historical calibration provenance

The prior draft's 38-case proposed review set and 20 fixtures remain in the unchanged historical snapshot [research reference omitted from this distribution]. They are not Harmonized expected labels and have not been rescored. Frozen cohort calibration failures, prompts and outputs remain under their original version records [research reference omitted from this distribution].
