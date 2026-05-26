// SPEC-5 Wave 1 §1.4: stable, sequenced TC ID generation. The store persists
// per-feature-slug counters under .test_artifacts/tc_sequence.json so re-runs
// keep numbering monotonic across invocations.

import * as fs from "fs";
import * as path from "path";

export const SEQUENCE_FILE = ".test_artifacts/tc_sequence.json";

export type TestType = "HP" | "FUNC" | "BVA" | "NEG";

export function categoryToType(categoryId: string): TestType {
  switch (categoryId) {
    case "happy_path":
      return "HP";
    case "functional_expansion":
      return "FUNC";
    case "boundary_limit":
      return "BVA";
    case "negative_security":
      return "NEG";
    default:
      throw new Error(`Unknown category_id: ${categoryId} — cannot map to TC type`);
  }
}

export function featureNameToSlug(name: string, maxLength = 12): string {
  // "Resume Application Portal" → "RAP" → padded to "RAPESUMAPPL" (≤ maxLength).
  // Algorithm: first letter of each word, then rotate through subsequent letters
  // until target length. All uppercase, alphanumeric only.
  const cleaned = (name ?? "").replace(/[^A-Za-z0-9 ]/g, "").trim();
  if (!cleaned) return "FEATURE";

  const words = cleaned.split(/\s+/);
  let slug = words.map((w) => (w[0] ?? "")).join("").toUpperCase();

  let charIdx = 1;
  while (slug.length < maxLength) {
    let appended = false;
    for (const w of words) {
      if (slug.length >= maxLength) break;
      if (charIdx < w.length) {
        slug += w[charIdx].toUpperCase();
        appended = true;
      }
    }
    if (!appended) break;
    charIdx += 1;
  }

  if (slug.length < 3) slug = slug.padEnd(3, "X");
  return slug.slice(0, maxLength);
}

export class TcSequenceStore {
  readonly path: string;
  private sequence: Record<string, Record<TestType, number>>;

  constructor(workspaceRoot: string, sequenceFile: string = SEQUENCE_FILE) {
    this.path = path.join(workspaceRoot, sequenceFile);
    this.sequence = this.load();
  }

  private load(): Record<string, Record<TestType, number>> {
    try {
      if (fs.existsSync(this.path)) {
        const raw = fs.readFileSync(this.path, "utf8");
        return JSON.parse(raw);
      }
    } catch (e) {
      console.error("tc_sequence read failed:", e);
    }
    return {};
  }

  private save(): void {
    fs.mkdirSync(path.dirname(this.path), { recursive: true });
    fs.writeFileSync(this.path, JSON.stringify(this.sequence, null, 2));
  }

  /** Allocate and persist the next TC ID for a (featureSlug, type) pair. */
  next(featureSlug: string, type: TestType): string {
    if (!this.sequence[featureSlug]) {
      this.sequence[featureSlug] = { HP: 0, FUNC: 0, BVA: 0, NEG: 0 };
    }
    this.sequence[featureSlug][type] += 1;
    const n = this.sequence[featureSlug][type].toString().padStart(3, "0");
    this.save();
    return `TC_${featureSlug}_${type}_${n}`;
  }
}
