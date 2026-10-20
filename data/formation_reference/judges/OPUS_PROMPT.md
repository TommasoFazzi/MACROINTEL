# Prompt for the Opus judge (paste into a NEW conversation, nothing else before it)

You are labelling a research dataset. Attached are a guideline and a CSV file.
Apply the guideline strictly and judge every row on its own, without comparing it with other rows.
Titles and text may be in any language; judge the story, not the wording.

Fill the `label` column for every row with exactly one of:
- J file: `yes`, `no` or `unsure`
- S file: `same`, `different` or `unsure`

Return the complete CSV with only two columns, `row_id,label`, one line per row and every row_id present.
Do not skip rows, do not summarise, do not add commentary.
If the file is long, work in blocks of 50 rows and continue until the last row_id.

Attach: GUIDELINE_J.md + opus_input_J_20261016.csv (or GUIDELINE_S.md + opus_input_S_20261016.csv).
