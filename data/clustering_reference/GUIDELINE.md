# Labelling guideline: storyline pairs

Reference set for the offline clustering validation study
(`clustering-offline-validation-study`, design.md D6). Written before any pair is labelled, and not changed afterwards
unless the reliability gate fails.

## The question

For every row, answer one question:

> **Should these two storylines share the same theme (colour and label) in the narrative graph?**

A theme is a macro-topic a reader would browse as one area of the world news: for example "Iran–US confrontation and
the Gulf", "Russia–Ukraine war", "Chinese economy and trade". It is broader than a single story and narrower than a
whole region or a whole sector.

## Labels

| Label | Use it when |
|---|---|
| `same` | A reader browsing that theme would expect to find both storylines in it. |
| `different` | They belong to different macro-topics, even if they mention the same country or actor. |
| `unsure` | You cannot decide after reading both summaries. Use it sparingly: these rows are dropped from scoring. |

## Rules

1. **Judge the topic, not the words.** Two storylines that both mention "China" are not automatically `same`: a
   Taiwan Strait military drill and a Chinese property-market crisis are different themes.
2. **One shared actor is not enough; a shared situation is.** `same` means the two storylines are parts of one larger
   situation (the same conflict, negotiation, crisis or policy track).
3. **Different angles of one situation are `same`.** The military, diplomatic and economic sides of the same war belong
   to one theme.
4. **Ignore time.** If two storylines are about the same situation at different moments, the answer is `same`.
5. **Do not guess the algorithm.** The file shows no cluster ids and no algorithm names on purpose. Answer only from the
   two titles and summaries.
6. **Some rows appear twice**, with left and right swapped. Answer them independently; do not scroll back to look.
7. **Bridging storylines: judge by the main subject.** In the graph every storyline gets exactly one colour, even when it
   touches two themes (Houthi attacks on Red Sea shipping touch both the Yemen–Iran conflict and global trade). For each
   storyline, ask what its summary is mainly about: military escalation, or freight rates and insurance? A passing
   mention does not count. Answer `same` if the two main subjects belong to one theme, `different` otherwise.
8. **Do not enforce transitivity.** If A goes with B and B with C, A and C may still be `different`: themes have fuzzy
   borders, and a chain of pairs can drift from one theme to another. Judge every row on its own, without checking it
   against earlier rows. Inconsistent-looking chains are information about where the borders are, not mistakes.
9. **`unsure` is for genuine 50/50 cases**: both storylines are equally about two themes, so the main subject cannot be
   decided. If more than about 1 row in 10 ends up `unsure`, stop and say so: the guideline needs another example rather
   than more `unsure` answers.

## Worked examples (illustrative, not from the snapshot)

1. **`same`.** "Iran rejects new IAEA inspection terms" / "US moves second carrier group to the Gulf". Both are parts
   of the Iran–US confrontation; a reader of that theme expects both.
2. **`same`.** "Ukraine strikes Russian refinery in Saratov" / "EU agrees 19th sanctions package on Russian energy".
   Military and economic sides of the same war.
3. **`different`.** "China holds live-fire drills around Taiwan" / "Evergrande creditors reject restructuring plan".
   Same country, different themes (security vs domestic economy).
4. **`different`.** "Romania's central bank holds rate at 6.50%" / "ECB signals pause in rate cuts". Both are monetary
   policy, but one is Romania's domestic economy and the other the euro area; they sit under different themes.

## Practicalities

- File: `labels_<date>.csv`. Fill the `label` column only; do not edit, sort or delete rows.
- About 290 rows, roughly 3.7 hours. Split it across sessions as you like; stop at a row boundary.
- Do not open `pairs_key_<date>.csv` while labelling: it contains the stratum of each row.
- The reliability check uses the repeated rows: Cohen's κ ≥ 0.6 and raw agreement ≥ 85%. If it fails, this guideline
  is revised and the rows are relabelled before any result is read.
