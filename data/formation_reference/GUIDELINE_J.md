# Labelling guideline J: does this article belong to this storyline?

Reference set for `storyline-formation-causal-review` (design.md D5). Written before any row is labelled. It does not
change afterwards unless the reliability gate fails.

## The question

For every row, answer one question:

> **Does this article belong to this storyline?**

A storyline is **one story**: one negotiation, one military campaign, one election, one court case, one crisis. It
develops over days or weeks. It is narrower than a theme. "Russia–Ukraine war" is a theme. "Talks on the US peace plan
for Ukraine" is a storyline. So is "Ukrainian strikes on Russian refineries".

## What you see

- **Article**: its title, source and the first lines of its text.
- **Storyline**: its title and the titles of its 3 most recent articles **as they were when the decision was taken**.
  The storyline title is the title of its first article, not an edited one. Judge mostly from the recent articles.

## Labels

| Label | Use it when |
|---|---|
| `yes` | The article reports a new development, reaction or angle of the same story the recent articles follow. |
| `no` | It is a different story, even if it shares the country, the actors or the theme. |
| `unsure` | You genuinely cannot tell after reading both. Use it sparingly: these rows are dropped from scoring. |

## Rules

1. **Same story, not same topic.** Two articles about Iran are `no` if one is about the nuclear talks and the other
   about protests in Tehran.
2. **Developments count.** A reaction, a consequence or a follow-up of the same event is `yes`: a ceasefire that
   follows the strikes of the recent articles, or a court ruling on the case they report.
3. **Time does not decide.** A story that resumes after a pause is still the same story: answer `yes` if it is the same
   situation.
4. **Do not guess the algorithm.** The file shows no scores and no strata on purpose.
5. **Mixed storylines.** If the recent articles mix two stories, answer `yes` if the article fits the story most of
   them follow, `no` otherwise.
6. **Some rows appear twice.** Answer them independently, and do not scroll back to check.
7. **`unsure` is for genuine 50/50 cases.** If more than about 1 row in 10 ends up `unsure`, stop and say so: the
   guideline needs another example.

## Worked examples (illustrative, not from the data)

1. **`yes`.** Article "Kyiv rejects clause on Donbas in revised US plan". Storyline recent articles: "US envoy presents
   28-point plan to Zelensky", "European leaders propose changes to US peace plan", "Rubio: progress in Geneva talks".
2. **`no`.** Article "Ukrainian drones hit refinery in Saratov". Same storyline as above: same war, but a different
   story (strikes, not talks).
3. **`yes`.** Article "Israel's cabinet approves second phase of Gaza truce". Recent articles about the ceasefire talks in
   Cairo, two weeks earlier.
4. **`no`.** Article "Romania's central bank holds rate at 6.50%". Storyline about the Romanian presidential election
   rerun: same country, different story.

## Practicalities

- File: `labels_J_<date>.csv`. Fill the `label` column only. Do not edit, sort or delete rows.
- About 380 rows. Split them across sessions and stop at a row boundary.
- Do not open the `key_J_<date>.csv` file while labelling: it holds the stratum of each row.
- Reliability check on the repeated rows: Cohen's κ ≥ 0.6 and raw agreement ≥ 85%. If it fails, this guideline is
  revised and the rows are relabelled before any result is read.
