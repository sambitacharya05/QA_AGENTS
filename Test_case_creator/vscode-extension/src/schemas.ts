import { z } from 'zod';

// ============================================================================
// 2.1 Module Analyzer (module_analyzer.agent.md) Output Schema
// ============================================================================
export const ModuleAnalyzerOutputSchema = z.object({
  is_module_specific: z.boolean(),
  detected_module: z.string(),
  matched_rule_ids: z.array(z.string()).min(1, 'At least one rule ID required'),
  reasoning: z.string().min(1),
  // SPEC-5 Wave 1: graph-derived feature metadata (M2/T8 "Claims" mislabel fix).
  // Optional for back-compat — old analyzer outputs without these fields parse.
  feature_name: z.string().optional(),
  area_path: z.string().optional(),
  is_llm_fallback: z.boolean().optional(),
  prioritized_rule_ids: z.array(z.string()).optional()
});

// ============================================================================
// 2.1a Rule Analysis Schemas (Specs 08–11)
// Dependency order: 1→2→3→4→5→6→7 (no forward references)
// ============================================================================

// 1. SkippedMethodologySchema — a methodology evaluated and found inapplicable
export const SkippedMethodologySchema = z.object({
  name: z.enum(['EP', 'BVA', 'StateTransition', 'LinearExpansion', 'RBAC']),
  reason: z.string().min(10, 'Skipped methodology reason must be descriptive (min 10 chars)')
});

// 2. RuleAttributeSchema — a single testable input/condition/state for a rule
export const RuleAttributeSchema = z.object({
  name: z.string().min(1),          // snake_case identifier (e.g., "subscription_mode")
  display_name: z.string().min(1),  // Human-readable header label (e.g., "Subscription Mode")
  type: z.enum(['enum', 'numeric_range', 'date_range', 'boolean']),
  possible_values: z.array(z.string()).min(1),
  source: z.enum(['explicit', 'implicit'])
});

// 3. EquivalenceClassEntrySchema — one row in the EC Table for a specific attribute
export const EquivalenceClassEntrySchema = z.object({
  attribute: z.string().min(1),    // Must match a RuleAttribute.name in the same rule_analysis entry
  class_label: z.string().min(1),  // Human-readable label (e.g., "Trial Mode", "Just Below Boundary")
  class_value: z.string().min(1),  // Concrete test input value (e.g., "trial", "59", "17")
  partition: z.enum([
    'valid',
    'invalid',
    'boundary_low',
    'boundary_high',
    'boundary_at'
  ]),
  expected_outcome: z.string().min(10, 'Expected outcome must be specific — minimum 10 characters'),
  is_straight_through: z.boolean(),
  // SPEC-5 Wave 1: verbatim error message captured for ADD_VERBATIM_ERROR_ASSERTION
  // remediation (gap SPEC-4 §6.1).
  verbatim_assertion: z.string().nullable().optional()
});

// 4. StraightThroughCaseSchema — the Happy Path baseline derived from EC table ST entries
export const StraightThroughCaseSchema = z.object({
  description: z.string().min(1),
  attribute_values: z.record(z.string(), z.string()).refine(
    (vals) => Object.keys(vals).length > 0,
    'ST case must have at least one attribute value'
  ),
  expected_outcome: z.string().min(10)
});

// 5. LinearExpansionRowSchema — one row in the Linear Expansion Matrix (one planned test case)
export const LinearExpansionRowSchema = z.object({
  test_number: z.number().int().positive(),
  is_straight_through: z.boolean(),
  varied_attribute: z.string().nullable(), // null for ST row; attribute name or "RBAC" for others
  attribute_values: z.record(z.string(), z.string()).refine(
    (vals) => Object.keys(vals).length > 0,
    'Matrix row must include at least one attribute value'
  ),
  expected_outcome: z.string().min(10),
  maps_to_scenario_title: z.string().min(1),
  // SPEC-5 Wave 1 + SPEC-4 §6.2 cross-agent contract fields. Optional so
  // existing payloads parse without modification; analyst sets them when needed.
  requires_stateful_precondition: z.boolean().optional(),
  stateful_precondition_description: z.string().nullable().optional(),
  intentional_multi_violation: z.boolean().optional()
});

// 6. LinearExpansionMatrixSchema — complete matrix for a single business rule
export const LinearExpansionMatrixSchema = z
  .object({
    column_headers: z.array(z.string()).min(1),
    straight_through_values: z.record(z.string(), z.string()),
    rows: z.array(LinearExpansionRowSchema).min(1),
    formula_applied: z.string().min(1),
    total_tests_derived: z.number().int().positive()
  })
  .superRefine((matrix, ctx) => {
    // Invariant 1: Exactly one ST row
    const stRows = matrix.rows.filter((r) => r.is_straight_through);
    if (stRows.length !== 1) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['rows'],
        message: `Linear expansion matrix must have exactly one straight-through row. Found: ${stRows.length}`
      });
    }

    // Invariant 2: ST row must be test_number 1 with null varied_attribute
    const row1 = matrix.rows.find((r) => r.test_number === 1);
    if (row1 && (!row1.is_straight_through || row1.varied_attribute !== null)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['rows', 0],
        message: 'Row 1 must be the straight-through row with varied_attribute: null'
      });
    }

    // Invariant 3: total_tests_derived must match rows.length
    if (matrix.total_tests_derived !== matrix.rows.length) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['total_tests_derived'],
        message: `total_tests_derived (${matrix.total_tests_derived}) must equal rows.length (${matrix.rows.length})`
      });
    }

    // Invariant 4: maps_to_scenario_title must be unique across all rows
    const titles = matrix.rows.map((r) => r.maps_to_scenario_title);
    const uniqueTitles = new Set(titles);
    if (uniqueTitles.size !== titles.length) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['rows'],
        message: 'All maps_to_scenario_title values must be unique across matrix rows'
      });
    }
  });

// 7. RuleAnalysisSchema — top-level per-rule analysis block (all Phase 0–6 outputs)
export const RuleAnalysisSchema = z
  .object({
    rule_id: z.string().min(1),
    rule_type: z.enum([
      'binary_flag',
      'numeric_threshold',
      'lifecycle',
      'multi_attribute',
      'role_access'
    ]),
    classification_reasoning: z.string().min(20),
    applicable_methodologies: z
      .array(z.enum(['EP', 'BVA', 'StateTransition', 'LinearExpansion', 'RBAC']))
      .min(1, 'At least one methodology must be applicable'),
    skipped_methodologies: z.array(SkippedMethodologySchema),
    attributes: z.array(RuleAttributeSchema).min(1),
    equivalence_class_table: z.array(EquivalenceClassEntrySchema).min(2),
    straight_through_case: StraightThroughCaseSchema,
    linear_expansion_matrix: LinearExpansionMatrixSchema,
    // SPEC-5 Wave 1: analyst dedup + consistency tracking surface (optional
    // for back-compat with existing payloads).
    consistency_warnings: z.array(z.string()).optional(),
    dedup_notes: z.array(z.string()).optional(),
    classification_override: z.boolean().optional()
  })
  .superRefine((analysis, ctx) => {
    // Cross-field: skipped methodologies must not appear in applicable_methodologies
    const applicableSet = new Set(analysis.applicable_methodologies);
    for (const skipped of analysis.skipped_methodologies) {
      if (applicableSet.has(skipped.name)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['skipped_methodologies'],
          message: `Methodology "${skipped.name}" appears in both applicable_methodologies and skipped_methodologies. These are mutually exclusive.`
        });
      }
    }

    // Cross-field: every EC table attribute must reference a known attribute name
    const knownAttrNames = new Set(analysis.attributes.map((a) => a.name));
    for (const ecEntry of analysis.equivalence_class_table) {
      if (!knownAttrNames.has(ecEntry.attribute)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['equivalence_class_table'],
          message: `EC entry references unknown attribute "${ecEntry.attribute}". It must match a name in attributes[].`
        });
      }
    }

    // Cross-field: each attribute must have exactly one is_straight_through: true entry
    for (const attr of analysis.attributes) {
      const stCount = analysis.equivalence_class_table.filter(
        (e) => e.attribute === attr.name && e.is_straight_through
      ).length;
      if (stCount !== 1) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['equivalence_class_table'],
          message: `Attribute "${attr.name}" must have exactly one is_straight_through: true EC entry. Found: ${stCount}`
        });
      }
    }

    // Cross-field: ST case attribute_values keys must all be known attribute names
    for (const key of Object.keys(analysis.straight_through_case.attribute_values)) {
      if (!knownAttrNames.has(key)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['straight_through_case', 'attribute_values'],
          message: `ST case references unknown attribute "${key}"`
        });
      }
    }
  });

// ============================================================================
// 2.2 Test Case Analyst (test_case_analyst.agent.md) Category Proposal Schema
// ============================================================================
export const TestScenarioSchema = z.object({
  title: z.string().min(1),
  qa_technique: z.string().min(1),
  target_requirement: z.string().min(1),
  description: z.string().min(1)
});

export const ProposedCategorySchema = z.object({
  category_id: z.string().min(1),
  category_name: z.string().min(1),
  description: z.string().min(1),
  // UPDATED: candidate_count is now derived from linear_expansion_matrix.total_tests_derived
  // for the corresponding category rows. LLM must NOT estimate this value independently.
  candidate_count: z.number().int().positive(),
  total_conditions_count: z.number().int().positive(), // Target for Condition Coverage %
  test_scenarios: z.array(TestScenarioSchema).min(1)
});

export const CategoryProposalSchema = z.object({
  target_rules: z.array(z.string()).min(1),
  rule_analysis: z
    .array(RuleAnalysisSchema)
    .min(1, 'At least one rule_analysis entry is required — one per targeted business rule'),
  proposed_categories: z.array(ProposedCategorySchema).min(1)
});

// ============================================================================
// 2.3 Test Case Generator (test_generator.agent.md) Output Schema
// ============================================================================
export const TestStepSchema = z.object({
  step_number: z.number().int().positive(),
  action: z.string().min(1),
  expected: z.string().min(1),
  // SPEC-5 Wave 1: verbatim assertion text for Step 2+ ADD_VERBATIM_ERROR_ASSERTION.
  verbatim_assertion: z.string().nullable().optional()
});

// SPEC-5 Wave 1 (A6 breaking change): tc_id is mandatory and regex-validated.
// Format: TC_<FEATURE_SLUG>_<HP|FUNC|BVA|NEG>_<NNN>. Pre-allocated by
// TcSequenceStore in the extension; the generator agent must never invent its own.
export const TC_ID_PATTERN = /^TC_[A-Z0-9]+_(HP|FUNC|BVA|NEG)_\d{3}$/;

export const TestCaseSchema = z.object({
  title: z.string().min(1),
  category_id: z.string().min(1),
  target_rule_id: z.string().min(1),
  steps: z.array(TestStepSchema).min(1),
  // SPEC-5 Wave 1 (A6): mandatory regex-validated TC ID.
  tc_id: z.string().regex(TC_ID_PATTERN, 'tc_id must match TC_<FEATURE>_<HP|FUNC|BVA|NEG>_<NNN>'),
  target_feature_id: z.string().min(1)
});

const ASSERTION_PATTERN =
  /\b(assert|verify|status[\s-]?code|responds with|returns|validates|should\s+return|expect)\b/i;

export const TestCasesArraySchema = z
  .array(TestCaseSchema)
  .min(1)
  .superRefine((cases, ctx) => {
    cases.forEach((tc, i) => {
      const step1 = tc.steps.find((s) => s.step_number === 1);
      if (!step1) return; // Missing step 1 is a separate issue
      if (
        ASSERTION_PATTERN.test(step1.action) ||
        ASSERTION_PATTERN.test(step1.expected)
      ) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: [i, "steps", 0, "action"],
          message: `"${tc.title}": Step 1 must not contain active assertions. Move assertions to Step 2+.`,
        });
      }
    });
  });

// ============================================================================
// 2.4 Test Verifier (test_verifier.agent.md) Gap Log Schema
// ============================================================================

// Discriminated union style validation schema for Remediation Actions
export const RemediationActionSchema = z.discriminatedUnion("action_type", [
  z.object({
    action_type: z.literal("ADD_BOUNDARY_TEST"),
    target_field: z.string().min(1),
    correct_value: z.string().min(1),
    new_test_description: z.string().min(1),
  }),
  z.object({
    action_type: z.literal("FIX_STATUS_CODE"),
    target_test_title: z.string().min(1),
    target_step_number: z.number().int().positive(),
    correct_value: z.string().min(1),
  }),
  z.object({
    action_type: z.literal("ADD_STATE_TRANSITION"),
    new_test_description: z.string().min(1),
  }),
  z.object({
    action_type: z.literal("ADD_NEGATIVE_TEST"),
    new_test_description: z.string().min(1),
  }),
  z.object({
    action_type: z.literal("FIX_EXPECTED_OUTCOME"),
    target_test_title: z.string().min(1),
    target_step_number: z.number().int().positive(),
    correct_value: z.string().min(1),
  }),
  z.object({
    action_type: z.literal("ADD_EQUIVALENCE_CLASS"),
    target_field: z.string().min(1),
    new_test_description: z.string().min(1),
  }),
  // ── NEW (Spec 11) ──────────────────────────────────────────────────────────
  // Emitted by Verifier Rubric Point 0: missing straight-through happy path test
  z.object({
    action_type: z.literal("ADD_HAPPY_PATH_TEST"),
    target_rule_id: z.string().min(1),
    new_test_description: z.string().min(1),
  }),
  // Emitted by Verifier Rubric Point 1: a Linear Expansion Matrix row has no generated test
  z.object({
    action_type: z.literal("ADD_LINEAR_EXPANSION_TEST"),
    target_rule_id: z.string().min(1),
    matrix_row_number: z.number().int().positive(),
    varied_attribute: z.string().nullable(), // null = missing ST test; attribute name = missing variant
    new_test_description: z.string().min(1),
  }),
  // ── SPEC-4 Wave 2 / SPEC-5 Wave 2 — 5 new remediation shapes ───────────────
  // Verifier Point 7 — CSV meta-column rejection (H6/T3 fix).
  z.object({
    action_type: z.literal("REMOVE_META_COLUMN_FROM_UI"),
    target_test_title: z.string().min(1),
    offending_columns: z.array(z.string()).min(1),
  }),
  // Verifier Point 8 — single-input rule disambiguation (H7 fix).
  z.object({
    action_type: z.literal("DISAMBIGUATE_INPUT_VALUE"),
    target_field: z.string().min(1),
    rule_predicates_violated: z.array(z.string()).min(2),
    suggested_inputs: z.array(z.string()).min(2),
  }),
  // Verifier Point 9 — missing stateful precondition step (M5/T9 fix).
  z.object({
    action_type: z.literal("ADD_STATEFUL_PRECONDITION"),
    target_test_title: z.string().min(1),
    precondition_text: z.string().min(1),
  }),
  // Verifier Point 10 — matrix row dedup (M4 fix).
  z.object({
    action_type: z.literal("DEDUPE_MATRIX_ROW"),
    target_rule_id: z.string().min(1),
    drop_titles: z.array(z.string()).min(1),
  }),
  // Verifier Point 11 — verbatim error assertion enforcement (M7 fix).
  z.object({
    action_type: z.literal("ADD_VERBATIM_ERROR_ASSERTION"),
    target_test_title: z.string().min(1),
    target_step_number: z.number().int().positive(),
    verbatim_string: z.string().min(1),
  }),
]);

export const DetectedGapSchema = z.object({
  gap_id: z.string().min(1),
  target_rule_id: z.string().min(1),
  severity: z.enum(['High', 'Medium', 'Low']),
  description: z.string().min(1),
  remediation: RemediationActionSchema // Replaces free-form remediation_instruction prose
});

export const GapLogSchema = z
  .object({
    is_compliant: z.boolean(),
    detected_gaps: z.array(DetectedGapSchema)
  })
  .superRefine((data, ctx) => {
    if (!data.is_compliant && data.detected_gaps.length === 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["detected_gaps"],
        message: "Non-compliant result must include at least one detected gap.",
      });
    }
    if (data.is_compliant && data.detected_gaps.length > 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["is_compliant"],
        message: "Compliant result must not include detected gaps.",
      });
    }
  });

// ============================================================================
// 2.5 Inferred TypeScript Type Definitions
// ============================================================================
export type ModuleAnalyzerOutput = z.infer<typeof ModuleAnalyzerOutputSchema>;
// Rule Analysis types (Specs 08–11)
export type SkippedMethodology = z.infer<typeof SkippedMethodologySchema>;
export type RuleAttribute = z.infer<typeof RuleAttributeSchema>;
export type EquivalenceClassEntry = z.infer<typeof EquivalenceClassEntrySchema>;
export type StraightThroughCase = z.infer<typeof StraightThroughCaseSchema>;
export type LinearExpansionRow = z.infer<typeof LinearExpansionRowSchema>;
export type LinearExpansionMatrix = z.infer<typeof LinearExpansionMatrixSchema>;
export type RuleAnalysis = z.infer<typeof RuleAnalysisSchema>;
// Category Proposal types
export type TestScenario = z.infer<typeof TestScenarioSchema>;
export type ProposedCategory = z.infer<typeof ProposedCategorySchema>;
export type CategoryProposal = z.infer<typeof CategoryProposalSchema>;
export type TestStep = z.infer<typeof TestStepSchema>;
export type TestCase = z.infer<typeof TestCaseSchema>;
export type RemediationAction = z.infer<typeof RemediationActionSchema>;
export type DetectedGap = z.infer<typeof DetectedGapSchema>;
export type GapLog = z.infer<typeof GapLogSchema>;
