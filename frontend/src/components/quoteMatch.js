// Fuzzy locating of an anchored comment excerpt within chapter text.
// Pure (no DOM/React) so it can be self-checked in Node — see quoteMatch.selfcheck.mjs.

// Collapse all runs of whitespace to a single space.
export function normalizeWs(s) {
  return (s || '').replace(/\s+/g, ' ').trim()
}

// Index of the first block whose text contains the quote (first ~80 chars,
// whitespace-normalized), or -1 when it can no longer be located.
export function findQuoteBlockIndex(blockTexts, quote) {
  const needle = normalizeWs(quote).slice(0, 80)
  if (!needle) return -1
  return blockTexts.findIndex((t) => normalizeWs(t).includes(needle))
}
