// SPEC-5 Wave 1 §1.4: TcSequenceStore / featureNameToSlug / categoryToType.

import * as assert from 'assert';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import {
  categoryToType,
  featureNameToSlug,
  TcSequenceStore,
} from '../../vscode-extension/src/tcIdGenerator';

function makeTmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'tc-seq-'));
}

suite('tcIdGenerator', () => {
  test('categoryToType maps each category_id to the right TC type letter', () => {
    assert.strictEqual(categoryToType('happy_path'), 'HP');
    assert.strictEqual(categoryToType('functional_expansion'), 'FUNC');
    assert.strictEqual(categoryToType('boundary_limit'), 'BVA');
    assert.strictEqual(categoryToType('negative_security'), 'NEG');
  });

  test('categoryToType throws on unknown category_id', () => {
    assert.throws(() => categoryToType('totally_unknown'));
  });

  test('featureNameToSlug builds compact uppercase slug from feature name', () => {
    const slug = featureNameToSlug('Resume Application Portal');
    assert.ok(slug.length >= 3 && slug.length <= 12, `unexpected length: ${slug}`);
    assert.match(slug, /^[A-Z0-9]+$/);
    // First letters of each word are present in order.
    assert.ok(slug.startsWith('RAP'));
  });

  test('TcSequenceStore.next produces monotonic sequenced IDs per (slug, type)', () => {
    const dir = makeTmpDir();
    try {
      const store = new TcSequenceStore(dir);
      assert.strictEqual(store.next('RESUMEAPP', 'HP'), 'TC_RESUMEAPP_HP_001');
      assert.strictEqual(store.next('RESUMEAPP', 'HP'), 'TC_RESUMEAPP_HP_002');
      assert.strictEqual(store.next('RESUMEAPP', 'FUNC'), 'TC_RESUMEAPP_FUNC_001');

      // Persistence: a fresh store reading the same workspace continues the
      // counters where the previous one left off.
      const reopened = new TcSequenceStore(dir);
      assert.strictEqual(reopened.next('RESUMEAPP', 'HP'), 'TC_RESUMEAPP_HP_003');
      assert.strictEqual(reopened.next('RESUMEAPP', 'FUNC'), 'TC_RESUMEAPP_FUNC_002');
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
