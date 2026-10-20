// ── Oracle citation preprocessing ─────────────────────────────────────────────
// Converts [1], [2] in an Oracle answer to `__CITE__N__` code markers that the
// markdown code component in OracleMessage intercepts and renders as badges. The
// marker is deliberately unique — the LLM would never generate it naturally (unlike
// `[[1]]`, which could appear in code samples). `offset` shifts the numbers so a
// follow-up answer points at its own slice of the session-wide sources list.

export function preprocessCitations(text: string, offset: number): string {
  // Separate adjacent citations [1][6] → [1] [6] before wrapping in backtick markers,
  // otherwise consecutive `` `...``...` `` spans merge and break the markdown parser.
  // The lookahead does not consume the next "[", so runs of any length ([2][3][22])
  // are fully separated — a consuming pattern only split the first pair.
  const spaced = text.replace(/\](?=\[\d+\])/g, '] ');
  return spaced.replace(/\[(\d+)\]/g, (_, n) => `\`__CITE__${parseInt(n, 10) + offset}__\``);
}
