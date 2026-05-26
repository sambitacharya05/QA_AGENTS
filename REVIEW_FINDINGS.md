# Review Findings — Graph & Test Generation Flow

**Reviewed:** 2026-05-26
**Inputs:** `review documents/req/` (BRD + Excel field spec), `review documents/qaf-java-framework/` (existing QAF automation), `review documents/.context_builder/` (graph + blueprint + provenance), `review documents/.test_artifacts/` (generated tests + coverage report)
**Pipeline under review:** requirement docs → graph (`context_builder`) → test artifacts (`test_case_creator`)

---

## TL;DR

- **Graph semantic content:** strong. The LLM extraction correctly paraphrases FR-01..FR-05 and Excel field rules into rule nodes.
- **Graph structural traceability:** broken. The edge layer does not deliver what `edge_policy.json` promises; tests, page objects, rules, endpoints, and schemas are not connected to each other.
- **Generated tests:** correct on what they cover but cover ~4 of ~21 meaningful rules. Several tests are ambiguous or hallucinated. Coverage report's "100%" headline is misleading (small denominator + diverged verification loop).

---

## 1. Inventory of what was reviewed

| Artifact | Path |
|---|---|
| BRD | `review documents/req/BRD_Resume_Application.docx` |
| Excel field spec | `review documents/req/Field_Requirements_C2P_Resume.xlsx` |
| QAF feature files | `review documents/qaf-java-framework/src/test/resources/features/*.feature` (3 files, ~25 outlines / 40+ examples) |
| QAF page object | `review documents/qaf-java-framework/src/main/java/com/hdfclife/pages/ResumeApplicationPage.java` |
| QAF step defs | `review documents/qaf-java-framework/src/test/java/com/hdfclife/stepdefs/ResumeApplicationSteps.java` |
| QAF locators / config | `review documents/qaf-java-framework/src/test/resources/config/locators.properties`, `application.properties` |
| Graph | `review documents/.context_builder/graph.json` (98 nodes / 73 edges) |
| Edge policy | `review documents/.context_builder/edge_policy.json` |
| Blueprint | `review documents/.context_builder/blueprint.json` |
| Provenance | `review documents/.context_builder/provenance.json` |
| Generated tests | `review documents/.test_artifacts/test_cases_20260526_053821z.csv` (33 cases, 72 steps) |
| Coverage report | `review documents/.test_artifacts/coverage_report_20260526_053821z.md` |

---

## 2. Graph — node inventory (98 nodes)

| Type | Count | Notes |
|---|---:|---|
| `product_feature` | 6 | 3 feature-file Features + 3 BRD sections (Executive Summary, User Personas, NFR). **FR-01..FR-05 are not separately nodalized.** |
| `test_scenario` | 63 | Scenario Outlines expanded one-node-per-Example-row. 25 distinct names → 63 nodes. |
| `business_rule` | 7 | 4 strong semantic rules + 3 placeholder "tabular grid" nodes (one each for CSV / Excel / BRD NFR table). |
| `validation_rule` | 4 | DOB format, DOB not future, mobile 10-digit, OTP 6-digit. |
| `eligibility_rule` | 1 | DOB age 18–65. |
| `ui_business_rule` | 1 | Send OTP enablement. |
| `security_rule` | 5 | Resend limits, OTP expiry, invalid OTP message, invisible captcha, TLS 1.3. |
| `api_endpoint` | 3 | POST send / verify / resend (inferred). |
| `data_model` | 2 | Identification payload + OTP verification payload (inferred). |
| `ui_page_object` | 1 | `ResumeApplicationPage` — orphan. |
| `code_component` | 2 | TestRunner, ResumeApplicationSteps. |
| `test_utility` | 3 | TestDataReader, ConfigManager, CommonActions. |

## 3. Graph — edge inventory (73 edges)

```
Realized:  TESTS = 56 (all test_scenario → product_feature)
           PART_OF = 17 (all test_scenario → product_feature)
Declared in edge_policy.json but never emitted:
           TESTS (test_scenario → business_rule / api_endpoint) = 0
           IMPLEMENTS = 0
           VALIDATES = 0
           USES_MODEL = 0
```

This is the central failure: `edge_policy.json` declares `TESTS` for `(test_scenario, business_rule)` and `(test_scenario, api_endpoint)`, and declares `IMPLEMENTS` / `VALIDATES` / `USES_MODEL` for code/page/api↔rule/model — and none of those are realized.

---

## 4. Findings — severity-ranked

### BLOCKER (graph is not fit for traceability)

| # | Finding | Evidence |
|---|---|---|
| B1 | No `test_scenario → rule/endpoint/schema` edges exist. | All 56 `TESTS` edges target `product_feature`. |
| B2 | No `IMPLEMENTS` / `VALIDATES` / `USES_MODEL` edges at all, despite being declared in `edge_policy.json`. | `Counter(relationship for e in payload.edges) = {TESTS: 56, PART_OF: 17}` |
| B3 | 16 of 63 `test_scenario` nodes are orphans (no outgoing edges). | Edge analysis. |
| B4 | `ui_page_object` (`ResumeApplicationPage`) is completely orphaned. | 0 edges in/out. |
| B5 | `locators.properties` and `application.properties` parsed but produced 0 nodes / 0 edges. | `provenance.json`: `node_ids: []` for both files. **Critical — these hold canonical UI IDs and rule constants** (`app.number.min.length=8`, `mobile.valid.start.digits=6,7,8,9`, `dob.min.age=18`, `otp.timer.seconds=30`, `otp.max.resend.attempts=3`, `otp.session.lock.minutes=15`). |
| B6 | Coverage report's "100% Rule Coverage" applies to only 6 chosen rules, of which 2 are tabular placeholders. Actual coverage of meaningful rule nodes is ~4/21 (~19%). | `coverage_report_20260526_053821z.md` matrix. |

### HIGH

| # | Finding | Evidence |
|---|---|---|
| H1 | Excel field spec collapsed into one generic `business_rule` node ("Excel Sheet: Field_Requirements_C2P_Resume"). The 10 per-field rows (FLD_APP_NO, FLD_DOB, FLD_MOBILE, FLD_OTP, BTN_SEND_OTP, SEC_OTP, LNK_RESEND_OTP, TMR_OTP_COOLDOWN, BTN_VERIFY_OTP, MSG_INLINE_ERR) are not decomposed. | Node inventory. Feature files explicitly reference these IDs in their headers. |
| H2 | FR-01..FR-05 are not preserved as node IDs anywhere. | `grep` of node IDs / names. |
| H3 | Rule nodes have no provenance back to the BRD / Excel. | `metadata.source_file` is missing on the 16 semantic rule nodes; `provenance.json` registers only 4 BRD nodes + 1 Excel node. |
| H4 | Critical FR-04 controls not exercised by generated tests: 3-attempt cap, 30-s cooldown, 15-minute session lock. | Coverage report + CSV scan. |
| H5 | All OTP-timer behavior (countdown from 30 to 0, cooldown disabled state) is untested. | Same. |
| H6 | Several generated tests have ambiguous or hallucinated semantics: `flow_type` is a CSV meta-column, not a UI field, but appears as a UI action in 3 tests. | `test_cases_*.csv` rows 16, 18, 20. |
| H7 | `2030-01-01` used as a DOB input conflates "wrong format" and "future date" into one step, producing an ambiguous expected result. | Rows 18 and 28. |
| H8 | Source-document conflict not flagged: page object uses `BTN_PROCEED` (id=`loginBtn`) + Email/AppNo radio toggle; BRD + feature files assume "Send OTP" only and no email login. | `ResumeApplicationPage.java:74` vs. BRD. |

### MEDIUM

| # | Finding | Evidence |
|---|---|---|
| M1 | Coverage report internally inconsistent: claims 100% while stating `Termination reason: divergence, Initial gap count: 2, Gaps resolved: 0`. | `coverage_report_*.md` Verification Loop History section. |
| M2 | Module / area path mislabeled: `Target Module: Claims`, Area Path `TechInsurance\Claims`. The module is **Resume Application Portal for Click 2 Protect Supreme Plus**. | Report + CSV every row. |
| M3 | Step granularity averages 2.18 — collapses Background + arrange + act + multiple asserts into a single step. QAF features use 4–7 discrete steps per scenario. | CSV scan; QAF feature files. |
| M4 | "Lead lookup —" and "Resume lookup —" clusters mirror each other with the same inputs/assertions (10 cases combined). Redundancy without added coverage. | CSV scan. |
| M5 | Saved-stage redirection tests differentiate only by *expected redirect target*, but the action steps don't set the backend state; they enter the same mobile + DOB + OTP each time. Functionally the same test asserted differently. | CSV rows 10–12, 39–53. |
| M6 | Scenario Outline expansion creates 63 nodes for 25 distinct outlines; same-name duplicates will skew any "rule coverage by scenario count" metric. | Node analysis. |
| M7 | Assertions use generic paraphrases ("lead not found error is shown") instead of the BRD-verbatim error message strings. | CSV vs. BRD Exception section. |
| M8 | Missing NFR rule nodes: Performance (1.5 s @ p95), Accessibility (WCAG 2.1 AA / ARIA / keyboard), Compatibility (Chrome/Safari/Edge/Firefox + Mobile/Tablet/Desktop). | BRD NFR vs. node inventory. |
| M9 | Partial FR-04 capture: `rule_resend_otp_limits` captures cooldown + 3-attempt cap but not the 15-minute session lock. | Rule description text. |

### LOW / INFORMATIONAL

| # | Finding |
|---|---|
| L1 | Inferred `api_endpoint` (3) and `data_model` (2) nodes are a positive — sensible inference even though source docs do not specify them. Currently isolated; will need edges. |
| L2 | Boundary discipline on application-number length tests (7/8/9/14/15/16) is good. |
| L3 | Test data values (`APP12345678`, `9876543210`, `15/06/1990`) align with QAF CSV. |
| L4 | Generated tests do not align with or reference existing QAF scenario IDs (TC-HP-001, TC-VAL-001, TC-OTP-001…) — disjoint parallel suite. |

---

## 5. Specific untested rules (gap list)

These rule nodes exist in the graph but no generated test exercises them:

- `rule_date_of_birth_age_eligibility` (FR-02 / 18–65 bounds)
- `rule_date_of_birth_not_future` (Excel FLD_DOB)
- `rule_date_of_birth_format` (FR-02 / DD/MM/YYYY)
- `rule_mobile_number_validation` (FR-02 / Excel FLD_MOBILE)
- `rule_otp_length_validation` (FR-03 / Excel FLD_OTP)
- `rule_send_otp_enablement` (Excel BTN_SEND_OTP)
- `rule_resend_otp_limits` (FR-04)
- `rule_otp_expiry_handling` (Exception handling)
- `rule_invalid_otp_error` (Exception handling)
- `rule_send_otp_requires_invisible_captcha` (NFR-Security)
- `rule_transport_and_otp_security` (NFR-Security; out of scope for UI tests but should be acknowledged)
- All 3 `api_endpoint` nodes
- Both `data_model` nodes

---

## 6. Recommended fixes (work backlog)

### Graph extraction (`context_builder`)

- **G1 (Blocker fix)** Emit `TESTS(test_scenario → business_rule)` edges. Drive them from: feature-file header comments (`# BRD Reference: FR-01, FR-03, FR-05`), scenario tags (`@TC-VAL-003 @AgeValidation`), and step-text term matching.
- **G2** Emit `IMPLEMENTS` / `VALIDATES` / `USES_MODEL` edges per `edge_policy.json`:
  - `ui_page_object → ui_business_rule` (VALIDATES)
  - `code_component (step defs) → test_scenario` (IMPLEMENTS)
  - `api_endpoint → business_rule` (IMPLEMENTS)
  - `api_endpoint → data_model` (USES_MODEL)
- **G3** Parse `application.properties` into per-key `rule_constant` nodes (or attach as metadata on existing rules) so quantitative constants (`dob.min.age=18`, `otp.timer.seconds=30`, etc.) are queryable.
- **G4** Parse `locators.properties` into per-element `ui_element` nodes carrying the canonical IDs the feature files reference.
- **G5** Decompose the Excel sheet row-by-row into per-field rule nodes with field IDs preserved (FLD_APP_NO, FLD_DOB, FLD_MOBILE, FLD_OTP, BTN_SEND_OTP, SEC_OTP, LNK_RESEND_OTP, TMR_OTP_COOLDOWN, BTN_VERIFY_OTP, MSG_INLINE_ERR).
- **G6** Preserve `FR-01..FR-05` as first-class node IDs anchored to BRD line ranges; wire feature-file `BRD Reference:` headers into edges.
- **G7** De-duplicate Scenario Outline expansions or mark them as one parameterized scenario with a single rule binding.
- **G8** Set `source_file` on every node the LLM produces; refuse to commit rule nodes without provenance.
- **G9** Add nodes (or explicit "out-of-scope" markers) for missing BRD NFRs: Performance 1.5 s @ p95, Accessibility WCAG 2.1 AA, Compatibility (browsers + viewports), FR-04 15-min session lock.
- **G10** Flag source conflicts: when POM contains UI elements that contradict BRD (e.g. `BTN_PROCEED` vs. "Send OTP", email/AppNo radio toggle vs. BRD's identifier-only model), emit a `CONFLICTS_WITH` edge or a diagnostic.

### Test generation (`test_case_creator`)

- **T1 (Blocker fix)** Drive test selection off `TESTS` edges in the graph rather than an internally chosen target list. Once G1 is in place, generate for every rule that has ≥1 incoming `TESTS` edge.
- **T2** Generate the missing coverage explicitly: DOB age 18/65 boundaries, DOB format negatives, DOB future date, mobile invalid lengths + invalid start digits, OTP length + numeric-only, Send OTP gating matrix, OTP timer countdown, resend cooldown, 3-attempt → 15-min session lock, OTP expiry, incorrect-OTP "attempts remaining".
- **T3** Eliminate the `flow_type` hallucination. The CSV `flow_type` column is meta for test classification, not a UI field.
- **T4** Disambiguate DOB inputs: never use a single input that simultaneously violates two rules unless the test explicitly targets multi-failure precedence.
- **T5** Increase step granularity to mirror Gherkin: separate page-open, field entries with blur, button-enablement assertion, click, then per-element assertions.
- **T6** Assert BRD-verbatim error message strings where the BRD specifies them.
- **T7** Reconcile against existing QAF scenarios — either reuse `TC-*` IDs or generate additive tests, not a parallel disjoint set.
- **T8** Fix module / area path: `Resume Application` under `Click 2 Protect Supreme Plus`, not `Claims`.
- **T9** For saved-stage redirection scenarios, include an explicit precondition step that establishes the backend state (e.g., test-data seeding, mock instruction, or stored-session profile), instead of asserting different outcomes from identical inputs.

### Coverage reporting

- **C1** Define the denominator: report coverage as `tested rules / total rule-bearing nodes`, with both numerator and denominator visible. Don't equate "100% of rules we targeted" with "100% rule coverage".
- **C2** When the verification loop diverges, do not claim success in the headline. Either re-run, lower the target scope explicitly, or surface the gaps as the headline.
- **C3** Exclude tabular placeholder nodes (`rule_resume_application_data`, `rule_field_requirements_c2p_resume`, `rule_non_functional_requirements_nfr_table_1`) from the rule denominator, or replace them with their decomposed per-row rule nodes once G3/G4/G5 land.

---

## 7. Suggested execution order

1. **G1** (emit rule-targeting TESTS edges) — unblocks T1 and shifts coverage reporting onto a real denominator.
2. **G3 + G4 + G5** (parse config + Excel into per-key/per-field nodes) — feeds rule constants and field IDs into the graph.
3. **G2** (IMPLEMENTS / VALIDATES / USES_MODEL edges) — connects page object, step defs, endpoints, schemas.
4. **G6 + G8** (FR IDs + provenance) — restores requirement traceability.
5. **T1 + T2** (test generation off TESTS edges + fill the gap list) — produces a credible suite.
6. **T3..T9** + **C1..C3** in parallel — cleanup of hallucinations, granularity, labels, and reporting honesty.
7. **G7, G9, G10** — polish: de-dup outlines, NFR nodes, conflict detection.

---

## 8. Open questions

- Is the QAF framework the **gold-standard test suite** that the generated tests should converge to, or is it a *reference repo* that should be supplemented? The two suites are currently disjoint.
- Should the generator have access to `application.properties` rule constants directly (T2 needs `dob.min.age=18` etc. to write precise boundary cases)?
- The BRD vs. live page divergence (Send OTP vs. PROCEED, email/AppNo radio toggle) — is the BRD authoritative for test generation, or should generation align to the live page contract from the POM?
- Is `flow_type` in `resume_application_data.csv` ever meant to be a UI input, or strictly a TestNG/CSV grouping key? (Recommend documenting this in the CSV schema to prevent future hallucinations.)
