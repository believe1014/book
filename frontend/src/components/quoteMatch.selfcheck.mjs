// Minimal self-check: `node quoteMatch.selfcheck.mjs` — exits non-zero on failure.
import assert from 'node:assert'
import { findQuoteBlockIndex, normalizeWs } from './quoteMatch.js'

const blocks = ['第一段開頭', '這是  第二段\n有換行與   空白', '第三段結尾']

// exact + whitespace-insensitive match
assert.strictEqual(findQuoteBlockIndex(blocks, '這是 第二段 有換行與 空白'), 1)
// leading/trailing whitespace on the quote is ignored
assert.strictEqual(findQuoteBlockIndex(blocks, '  第三段結尾  '), 2)
// substring match (only first ~80 chars used as needle)
assert.strictEqual(findQuoteBlockIndex(blocks, '第一段'), 0)
// not found → -1 (content changed since the comment was written)
assert.strictEqual(findQuoteBlockIndex(blocks, '不存在的引文'), -1)
// empty / whitespace-only quote → -1
assert.strictEqual(findQuoteBlockIndex(blocks, '   '), -1)
assert.strictEqual(normalizeWs('  a\n\t b '), 'a b')

console.log('quoteMatch self-check: OK')
