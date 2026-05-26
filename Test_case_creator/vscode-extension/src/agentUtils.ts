import * as vscode from 'vscode';
import { ZodSchema } from 'zod';

export class AgentOutputError extends Error {
  constructor(
    public readonly agentName: string,
    public readonly rawOutput: string,
    public readonly detail: string
  ) {
    super(`[${agentName}] output validation failed: ${detail}`);
  }
}

/**
 * Optional pre-validation sanitizer that can be passed to parseAgentOutput.
 *
 * Receives the already-parsed (JSON.parse'd) value, returns the possibly-mutated
 * value plus a count of auto-corrections applied.  A fixedCount > 0 causes a
 * visible advisory to be emitted into the response stream before Zod validation
 * runs.  Sanitizer exceptions are non-fatal: the original parsed value is used.
 */
export type ParseSanitizer = (parsed: unknown) => { data: unknown; fixedCount: number };

/**
 * Pre-validation sanitizer for Test Case Analyst (CategoryProposalSchema) output.
 *
 * The LLM naturally emits bare JSON numbers for `numeric_range` / `date_range`
 * attributes (e.g. `"class_value": 12` instead of `"class_value": "12"`), but
 * CategoryProposalSchema requires all of these fields to be strings.  This
 * sanitizer coerces every affected non-string value to string before Zod runs,
 * so the pipeline continues instead of failing on numeric-range rules.
 *
 * Affected locations per rule_analysis entry:
 *   • equivalence_class_table[*].class_value
 *   • straight_through_case.attribute_values  (all record values)
 *   • linear_expansion_matrix.straight_through_values  (all record values)
 *   • linear_expansion_matrix.rows[*].attribute_values  (all record values)
 */
export const coerceAnalystNumericValues: ParseSanitizer = (parsed: unknown) => {
  if (
    !parsed ||
    typeof parsed !== 'object' ||
    !Array.isArray((parsed as any).rule_analysis)
  ) {
    return { data: parsed, fixedCount: 0 };
  }

  let fixedCount = 0;

  for (const rule of (parsed as any).rule_analysis) {
    if (!rule || typeof rule !== 'object') { continue; }

    // ── equivalence_class_table[*].class_value ───────────────────────────────
    if (Array.isArray(rule.equivalence_class_table)) {
      for (const ec of rule.equivalence_class_table) {
        if (ec && typeof ec.class_value !== 'string' && ec.class_value != null) {
          ec.class_value = String(ec.class_value);
          fixedCount++;
        }
      }
    }

    // ── straight_through_case.attribute_values ───────────────────────────────
    const stVals = rule.straight_through_case?.attribute_values;
    if (stVals && typeof stVals === 'object') {
      for (const key of Object.keys(stVals)) {
        if (typeof stVals[key] !== 'string' && stVals[key] != null) {
          stVals[key] = String(stVals[key]);
          fixedCount++;
        }
      }
    }

    // ── linear_expansion_matrix ──────────────────────────────────────────────
    const mat = rule.linear_expansion_matrix;
    if (mat && typeof mat === 'object') {

      // straight_through_values
      const stv = mat.straight_through_values;
      if (stv && typeof stv === 'object') {
        for (const key of Object.keys(stv)) {
          if (typeof stv[key] !== 'string' && stv[key] != null) {
            stv[key] = String(stv[key]);
            fixedCount++;
          }
        }
      }

      // rows[*].attribute_values
      if (Array.isArray(mat.rows)) {
        for (const row of mat.rows) {
          if (!row || typeof row !== 'object') { continue; }
          const av = row.attribute_values;
          if (av && typeof av === 'object') {
            for (const key of Object.keys(av)) {
              if (typeof av[key] !== 'string' && av[key] != null) {
                av[key] = String(av[key]);
                fixedCount++;
              }
            }
          }
        }
      }
    }
  }

  return { data: parsed, fixedCount };
};

/**
 * Robustly extracts the outermost JSON object or array from raw LLM output.
 *
 * Handles the full set of common LLM output patterns that break JSON.parse:
 *  1. Raw JSON with no wrapper (ideal case — fast path)
 *  2. JSON wrapped in ```json … ``` or ``` … ``` code fences
 *  3. Preamble text before the JSON (e.g. a reasoning summary, "Here is the JSON:")
 *  4. Postamble text after the JSON close brace (e.g. "I hope this helps.")
 *  5. Multiple code blocks — the FIRST complete { … } or [ … ] is extracted
 *
 * Strategy:
 *  a. Strip ALL occurrences of code-fence opening/closing markers.
 *  b. Find the first '{' or '[' character.
 *  c. Walk forward with a depth counter, correctly ignoring brace characters
 *     that appear inside JSON string literals (including escaped quotes).
 *  d. Return the slice from the opening bracket to the balanced close.
 *  e. If the JSON is truncated (depth never reaches 0), return everything from
 *     the opening bracket so JSON.parse still produces a meaningful error.
 *
 * @internal — exported only for unit testing
 */
export function extractJsonPayload(raw: string): string {
  // (a) Remove ALL markdown code-fence markers in one pass.
  //     Handles both opening variants (```json / ```) and bare closing ```.
  const stripped = raw
    .replace(/```(?:json)?\s*/g, '')
    .replace(/```/g, '')
    .trim();

  // (b) Find the first JSON opening character.
  const startIndex = stripped.search(/[{[]/);
  if (startIndex === -1) {
    return stripped; // No JSON-like content — let JSON.parse report the error
  }

  const openChar = stripped[startIndex];
  const closeChar = openChar === '{' ? '}' : ']';

  // (c) Walk the string with brace-depth counting, respecting string literals.
  let depth = 0;
  let inString = false;
  let escape = false;

  for (let i = startIndex; i < stripped.length; i++) {
    const ch = stripped[i];

    if (escape) { escape = false; continue; }
    if (ch === '\\' && inString) { escape = true; continue; }
    if (ch === '"') { inString = !inString; continue; }
    if (inString) { continue; }

    if (ch === openChar) { depth++; }
    else if (ch === closeChar) {
      depth--;
      if (depth === 0) {
        // (d) Found the balanced close — return exactly the JSON payload.
        return stripped.slice(startIndex, i + 1);
      }
    }
  }

  // (e) JSON was truncated before a balanced close was found.
  //     Return from the opening bracket so JSON.parse still emits a useful error.
  return stripped.slice(startIndex);
}

/**
 * Attempts to repair a single structural JSON defect caused by the LLM
 * omitting a closing `]` for an array. Handles two distinct patterns:
 *
 * **Pattern A — sibling key follows array** (`errorPos` char is `:`):
 *   The LLM wrote a sibling object key directly after the last array element,
 *   forgetting the `]` that closes the array first.
 *   ```
 *   Before:  [..., {last},"siblingKey": ...}
 *   After:   [..., {last}],"siblingKey": ...}
 *   ```
 *   Strategy: walk back past the key name to the comma before it; insert `]`
 *   immediately before that comma.
 *
 *   This covers `linear_expansion_matrix.rows[]` in the Test Case Analyst
 *   and `detected_gaps[]` in the Test Verifier.
 *
 * **Pattern B — parent object closes before array** (`errorPos` char is `}`):
 *   The LLM closed the PARENT object while the current array was still open.
 *   ```
 *   Before:  "steps": [{...}, {last}}
 *   After:   "steps": [{...}, {last}]}
 *   ```
 *   Strategy: insert `]` immediately before the `}` at `errorPos`.
 *
 *   This covers `steps[]` inside each test case in the Test Generator.
 *
 * In both cases the caller loops: do NOT re-verify with JSON.parse inside
 * this function — there may be multiple defects to repair.
 *
 * Returns `null` when neither heuristic applies (unexpected char at
 * errorPos, or no valid insert position is found).
 *
 * @internal — exported only for unit testing
 */
export function repairMissingArrayClose(text: string, errorPos: number): string | null {
  const ch = text[errorPos];

  // ── Pattern B: parent object `}` is inside an unclosed array ─────────────
  // Insert `]` directly before the stray `}` to close the array first.
  if (ch === '}') {
    return text.slice(0, errorPos) + ']' + text.slice(errorPos);
  }

  // ── Pattern A: sibling key `:` is inside an unclosed array ───────────────
  // Walk back past the key name to the comma that precedes it; insert `]`
  // before that comma to close the array.
  if (ch !== ':') { return null; }

  let p = errorPos - 1;

  // Step back over the closing `"` of the key name
  while (p > 0 && text[p] !== '"') { p--; }
  // Step back through the key name chars to its opening `"`
  p--;
  while (p > 0 && text[p] !== '"') { p--; }
  // Move one char before the opening quote (should be comma or whitespace)
  p--;

  // Skip any whitespace between the comma and the key
  while (p > 0 && (text[p] === ' ' || text[p] === '\n' || text[p] === '\r' || text[p] === '\t')) {
    p--;
  }

  // Verify we landed on a comma — anything else means this heuristic doesn't apply
  if (text[p] !== ',') { return null; }

  // Insert `]` immediately before the comma to close the orphaned array
  return text.slice(0, p) + ']' + text.slice(p);
}

/**
 * Extracts JSON payload from raw LLM output, parses it, optionally sanitizes
 * the result, and validates it against a Zod schema.
 *
 * Two-stage resilience strategy before giving up:
 *  1. `extractJsonPayload` — strips code fences and preamble/postamble text.
 *  2. `repairMissingArrayClose` loop — fixes any `]` that the LLM omitted
 *     before a sibling key inside a `linear_expansion_matrix`. Applied
 *     iteratively until the JSON is valid or no more repairs are possible.
 *     An advisory is emitted to the stream for each bracket inserted.
 *
 * @param sanitizer  Optional pre-validation transform.  Use to auto-correct
 *                   well-known LLM formatting errors (e.g. Step 1 assertions)
 *                   so the pipeline can continue instead of crashing.
 */
export async function parseAgentOutput<T>(
  raw: string,
  schema: ZodSchema<T>,
  agentName: string,
  stream: vscode.ChatResponseStream,
  sanitizer?: ParseSanitizer
): Promise<T> {
  // 1. Robustly extract the JSON payload from the LLM response.
  //    Handles markdown code fences, preamble text, postamble text, and
  //    reasoning summaries that the model may prepend or append despite
  //    explicit instructions to emit raw JSON only.
  const cleaned = extractJsonPayload(raw);

  // 2. Parse JSON — with iterative structural repair for the most common
  //    LLM defect: omitting the `]` that closes a `rows` array before the
  //    sibling `formula_applied` / `total_tests_derived` key.
  let parsed: unknown;
  let text = cleaned;
  let repairCount = 0;
  const MAX_REPAIRS = 20; // One per rule × safety headroom

  while (repairCount <= MAX_REPAIRS) {
    try {
      parsed = JSON.parse(text);
      break; // Successfully parsed
    } catch (err: any) {
      // Extract the error position from the native JSON.parse message.
      const posMatch = (err.message as string).match(/position (\d+)/);
      if (!posMatch) {
        // No position info — structural repair cannot help
        stream.markdown(`\n> ❌ **${agentName}** returned non-JSON. Raw output logged.\n`);
        throw new AgentOutputError(agentName, raw, 'JSON.parse failure');
      }

      const errorPos = parseInt(posMatch[1], 10);
      const repaired = repairMissingArrayClose(text, errorPos);

      if (!repaired) {
        // Heuristic doesn't apply — give up and surface the original error
        stream.markdown(`\n> ❌ **${agentName}** returned non-JSON. Raw output logged.\n`);
        throw new AgentOutputError(agentName, raw, 'JSON.parse failure');
      }

      repairCount++;
      text = repaired;
    }
  }

  if (repairCount > 0) {
    stream.markdown(
      `\n> ⚠️ **${agentName}** auto-repaired **${repairCount}** missing \`]\` bracket(s) in the JSON response (LLM omitted array-close before sibling keys).\n`
    );
  }

  // 3. Optional pre-validation sanitizer (e.g. auto-fix Step 1 assertion violations)
  //    Runs between JSON.parse and Zod so correctable LLM formatting errors don't
  //    crash the pipeline.  A non-zero fixedCount emits an advisory to the stream.
  if (sanitizer) {
    try {
      const { data, fixedCount } = sanitizer(parsed);
      if (fixedCount > 0) {
        stream.markdown(
          `\n> ⚠️ **${agentName}** auto-corrected **${fixedCount}** test case(s): ` +
          `Step 1 contained active assertions and was split into a setup step + assertion step.\n`
        );
      }
      parsed = data;
    } catch {
      // Sanitizer failure is non-fatal — fall through to Zod with the original data
    }
  }

  // 4. Validate against Zod schema
  const result = schema.safeParse(parsed);
  if (!result.success) {
    const issues = result.error.issues
      .map(i => `\`${i.path.join('.')}\`: ${i.message}`)
      .join('; ');
    stream.markdown(`\n> ❌ **${agentName}** schema violation — ${issues}\n`);
    throw new AgentOutputError(agentName, raw, issues);
  }

  return result.data;
}
