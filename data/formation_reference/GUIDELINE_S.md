# Labelling guideline S: are these two storylines the same story?

Reference set for `storyline-formation-causal-review` (design.md D5). Written before any row is labelled. It does not
change afterwards unless the reliability gate fails.

## The question

For every row, answer one question:

> **Are these two storylines the same story?**

This set measures **duplicates**: one story that the system split into two storylines.

**Same story is narrower than same theme.** The 2026-10-15 theme labelling asked whether two storylines belong under
one colour in the graph. Here the question is whether they are the **same thread**: one negotiation, one campaign, one
election, one case. Two storylines can share a theme and still be different stories.

## What you see

Each side shows a storyline: its title, and either its summary or the titles of up to 5 of its articles.

## Labels

| Label | Use it when |
|---|---|
| `same` | Both follow the same story. Merging them would lose nothing. |
| `different` | They follow different stories, even inside one theme. |
| `unsure` | You genuinely cannot tell. Use it sparingly: these rows are dropped from scoring. |

## Rules

1. **Same story, not same theme.** "US–Iran nuclear talks in Oman" and "IRGC seizes tanker in Hormuz" share a theme.
   They are `different` stories.
2. **Different phases of one story are `same`.** "Gaza ceasefire talks in Cairo" and "Gaza truce: second phase
   approved" are `same`.
3. **Time does not decide.** The same story two weeks apart is still `same`.
4. **Mixed storylines go by their main subject**, as in the theme guideline. A passing mention does not count.
5. **Do not enforce transitivity.** Judge every row on its own.
6. **Some rows appear twice**, with left and right swapped. Answer them independently.
7. **`unsure` is for genuine 50/50 cases.** If more than about 1 row in 10 ends up `unsure`, stop and say so.

## Practicalities

- File: `labels_S_<date>.csv`. Fill the `label` column only. Do not edit, sort or delete rows.
- About 120 rows.
- Do not open the `key_S_<date>.csv` file while labelling.
- Reliability check: Cohen's κ ≥ 0.6 and raw agreement ≥ 85% on the repeated rows.
