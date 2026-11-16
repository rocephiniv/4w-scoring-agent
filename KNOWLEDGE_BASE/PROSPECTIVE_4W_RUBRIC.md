# 4W-Harmonized-v1 — prospective documentation specification

Implementation version: `4W-Harmonized-v1-docs.1`. This document is the **canonical scoring component of the sole active prospective authority**, selected 2026-09-23 in [AUTHORITY_SELECTION.json](HARMONIZED_V1/AUTHORITY_SELECTION.json). [ADJUDICATION_AND_CALIBRATION.md](ADJUDICATION_AND_CALIBRATION.md) owns its adjudication component. The original 2026-09-22 foundation remains frozen; this routing-only transition changes no clinical scoring meaning and does not activate a scorer, authorize a run, or establish clinical validity.

Sources: unchanged official rubric [research reference omitted from this distribution], [paragraph register](AUTHORITY/4W-Harmonized-v1/OFFICIAL_TEXT.md), crosswalk [research reference omitted from this distribution], decisions [research reference omitted from this distribution], [rule IDs](HARMONIZED_V1/RULE_IDS.csv). O-P references below are exact paragraph identifiers in the register. Historical draft `4W-Prospective-v1-draft.1` and its prior approvals remain in the pre-change snapshot [research reference omitted from this distribution]; they do not override this version.

## 1. Scope, input and precedence

H1-INPUT / H1-GOV-AUTHORITY. Use only the permitted indication text. No chart lookup, demographic inference, invented symptoms, anatomy, timing or diagnostic intent. Interpret clinical language narrowly and reproducibly, not by indiscriminate keyword matching. Score each W independently subject to the explicit Where hierarchy. Apply the prerequisite [eligibility policy](COHORTS_AND_DATA_DICTIONARY.md#approved-trauma-eligibility-rule-for-future-reviews). Preserve raw text and negation. No new cleaning transformation is authorized by this rubric: any future preprocessing must be specified, versioned and approved in the run protocol. Historical terminal-token cleaning does not silently become new policy. [O-P003–011, O-P267–273]

Within platform/project safeguards and cohort eligibility, explicit official rules and applicable general principles supersede conflicting draft provisions. Official examples fix their exact documented applications; they do not create unlimited analogies. Specific exclusions override general wording (e.g. AKI does not earn When merely from its expanded name). Contradictions or genuine ambiguity are flagged, not resolved by choosing a convenient rule. Ordinary unambiguous applications of a general rule proceed normally. Unlisted exceptions that would override an otherwise applicable rule await human confirmation. [O-P007, O-P039, O-P083–087, O-P263]

Historical v2, refined-v3 and Central-v1 records retain their original scoring-version labels and provenance. Those rubrics, prompts and tools, and earlier drafts, are retired from future operational use; they explain recorded work only. No automatic backport, pooling, score repair or rescore. Version index [research reference omitted from this distribution]

## 2. What — current clinical problem

H1-WHAT-CURRENT, H1-WHAT-ESTABLISHED, H1-WHAT-DEVICE, H1-WHAT-TRAUMA, H1-WHAT-REASSESS, H1-WHAT-EXCLUDE. [O-P013–025]

What=1 for a documented current symptom/sign, meaningful objective finding, established active diagnosis/problem, traumatic event, device malfunction, or known abnormality actively reassessed. Examples include pain, fever, vomiting, hematuria, rectal bleeding, distention/tenderness; leukocytosis, tachycardia, hypotension, elevated lactate, ascites or active abnormal imaging; established pancreatitis/SBO/UTI/sepsis; a tube not draining; a known abscess being reassessed.

What=0 when the only entity is suspected (concern for/rule out/possible/?/evaluate for), historical without current activity, or a generic target such as an unspecified abnormality without a meaningful active finding. A suspected diagnosis does not become a symptom merely because it is plausible.

The prior “diagnosis-only earns Why only” rule is superseded. Distinguish established active diagnosis from suspected diagnosis; independently assess separately documented symptoms/findings. Bare established pancreatitis: 1/0/1/0; “concern for pancreatitis”: 0/0/1/1, subject to the Where hierarchy. These are documentation fixtures, not rescored patient records. [O-P124–149]

## 3. When — timing or acuity

H1-WHEN-ANCHOR, H1-WHEN-ACUITY, H1-WHEN-TRANSITION, H1-WHEN-LINKED-EVENT, H1-WHEN-EXCLUDE. [O-P027–039; O-P134–143]

When=1 for explicit active-problem onset/duration (“3 days”, “since yesterday”, “this morning”), explicit acuity (“acute abdominal pain”, “acute-on-chronic pain”, “sudden onset”), a current-encounter transition (“became unresponsive in the waiting room”), or a clearly linked temporal event. The official certainty matrix also awards When for established/suspected acute specific diagnoses; the AKI/ARF exclusion still takes precedence.

A specifically dated or clearly recent procedure must link to the evolving postoperative problem or suspected complication. A recent intervention explicitly linked to reassessment (abscess drained 1–2 weeks ago, now evaluating persistence/worsening) qualifies; “since discharge” can qualify as an anchor.

Recent, worsening, increased, intermittent, persistent, ongoing, continued, recurrent, episode or downtrending alone usually earn 0. Unlinked historical/procedure timing, postprandial pattern alone, and acute embedded in AKI/ARF do not qualify. “Post-op” is not universally positive. “Worsening abdominal pain for 2 days” earns timing from the duration, not worsening.

Do not turn “new onset”, subacute or chronic into an automatic unlisted positive/negative rule. If the general onset/acuity principle clearly applies, use it; if the meaning or attachment is genuinely unresolved, retain provisional scoring with an affected-W flag. Decision register [research reference omitted from this distribution]

## 4. Where — ordered localization hierarchy

H1-WHERE-HIERARCHY and the specific H1-WHERE IDs below. [O-P041–101; O-P149; O-P259–260]

1. **Localizable current symptom first.** Require meaningful localization: RLQ/LLQ/RUQ, epigastric, suprapubic, right flank, left-sided, or a meaningful diffuse/generalized distribution. Abdominal pain alone, back pain alone, and abdominal tenderness without region/distribution earn 0. A descriptor without an identifiable relevant body region does not invent one.
2. **Competing unlocalized symptom.** Systemic findings, unrelated history, or device/procedure presence generally do not rescue unlocalized pain. The rejected automatic linked-device/pathology override remains rejected.
3. **No competing unlocalized localizable symptom.** Intrinsically nonlocalizable presentations may earn Where by default: sepsis, fever, hypotension, post-arrest, lactic acidosis, anemia/Hgb drop, AMS/unresponsiveness, generalized weakness, ascites, hyperkalemia, nausea/vomiting. This is an official documentation exception, not a claim of physical anatomical precision.
4. **Specific entity/organ system.** A sufficiently specific diagnosis, syndrome or entity can localize. Official mappings: pancreatitis→pancreas/GI; aortic dissection→aorta/vascular; UTI/pyelonephritis/nephrolithiasis/urolithiasis→GU; pneumatosis intestinalis→bowel; intussusception/SBO/bowel obstruction→GI; appendicitis→appendix/GI; diverticulitis/colitis→colon/GI; peritonitis→peritoneum; Fournier→perineal/genital soft tissues; cholangitis→biliary system; surgical-site infection→surgical-site compartment. Infection, malignancy, abnormality, acute process or context-free obstruction do not automatically localize.
5. **Specific contextual permissions.** Apply the narrow coherent-pattern, objective-imaging, procedural/device and other official rules below; do not silently broaden them.

### Narrow coherent pattern and objective imaging

H1-WHERE-PATTERN / H1-WHERE-IMAGING. “Abdominal pain radiating to back, concern nephrolithiasis” earns Where=1 by the explicit coherent GU pattern. Radiation alone or a weak association is not the same exception. “Possible developing obstruction, scattered air-fluid levels on KUB” earns Where=1 from documented bowel-localizing findings. Do not infer additional imaging facts. [O-P084–090]

### Procedural/device context

H1-WHERE-CONTEXT / H1-WHERE-DEVICE. Without competing unlocalized pain, specific linked procedural/device context may localize a suspected complication: spine surgery history + evaluate abscess, or cholecystectomy + biliary drain + evaluate abscess. Colon-cancer history + evaluate abscess does not localize an otherwise unlocalized abscess.

Device type/site can localize an active device problem. The exact official generic-nephrostomy-evaluation example earns Where=1 but What=0 and Why=0; do not extend that exact example to every generic device mention. Device presence beside abdominal pain does not establish pain location. Explicit pain-at-device-site wording can supply meaningful location; uncertain relations are flagged. [O-P091–098, O-P193–206]

### Other localization permissions and limits

H1-WHERE-OTHER / H1-WHERE-EXCLUDE. Imaging intended to locate an inherently unknown-location process, such as foreign-body ingestion, may earn Where. Bare trauma mechanism does not localize without a body region. Rectal bleeding/BRBPR/hematochezia localize unless a competing unlocalized symptom triggers the hierarchy. Positive Murphy sign or McBurney point localizes. [O-P097–101]

GU and GI terms are assessed through these official principles; neither every GI word nor a closed legacy list controls the new version. UTI localizes explicitly. Unambiguous urinary-system symptoms may be ordinary applications of the general localization principle, not new exceptions. Uncertain generic GIB localization and unlisted exceptions remain visible for adjudication; do not silently carry forward an older automatic outcome.

## 5. Why — diagnostic purpose

H1-WHY-SUSPECTED, H1-WHY-SOURCE, H1-WHY-DEVICE, H1-WHY-REASSESS, H1-WHY-COMPLICATION, H1-WHY-PATHWAY, H1-WHY-TRAUMA, H1-WHY-EXCLUDE. [O-P103–117]

Why=1 for a specific suspected diagnosis/differential; explicit source/cause question; specific device question (position/obstruction/leak/malfunction/infection); reassessment of known abnormality for persistence, progression, resolution, extent or response; linked specific complication; or a defined diagnostically specific syndrome/sign/pathway (renal colic, Murphy, complicated UTI/diverticulitis, McBurney). Trauma mechanism communicates evaluation for injury after eligibility is independently established. A coherent active complication sequence may communicate purpose without the words “concern for”, but only narrowly and reproducibly.

Why=0 for symptoms/findings alone, established diagnosis merely restated without a purpose, generic “evaluate further”, “rule out any abnormality/acute process”, “evaluate surgical site/nephrostomy tubes”, or unanchored “concern for complications”. Sepsis or UTI alone lacks Why; “source of sepsis” supplies a purpose. Coffee-ground emesis plus dark stools alone does not invent a diagnostic question; pulsatile mass plus hypotension does not document AAA concern.

An irreducible-hernia automatic Why exception and “LLQ, divertic” automatic expansion are **historical-only pending confirmation**. A clear explicit concern still qualifies normally. Never silently expand ambiguous shorthand or import outside clinical knowledge. Decisions [research reference omitted from this distribution]

## 6. Scoring, pending adjudication and reporting

H1-OUTPUT / H1-ADJUDICATION / H1-GOV-HISTORY. Each W is binary; Total is the arithmetic sum of the four Ws, and completeness remains >=3 Ws. Cross-version score differences are not intervention effects. Record minimal indication evidence and applicable H1 identifiers under the [output schema](HARMONIZED_V1/OUTPUT_SCHEMA.json).

The [canonical adjudication pathway](ADJUDICATION_AND_CALIBRATION.md#current-fail-closed-pathway) owns provisional uncertainty, flags, completeness finalization, independent reviews and separate final decisions. The [cohort dictionary](COHORTS_AND_DATA_DICTIONARY.md) owns eligibility and denominator accounting. These linked requirements are mandatory parts of this authority; they are not optional summaries.

This documentation does not authorize any audit run. Later rescoring requires explicit separate authorization, original-label retention and a separately versioned output. The baseline protocol [research reference omitted from this distribution] owns baseline-only use and later validation/activation gates.
