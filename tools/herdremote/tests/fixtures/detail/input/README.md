# Input extraction fixtures

These are synthetic fixtures derived from prompt shapes observed in the
real Claude/Codex/agy captures one directory above. They are not recordings
of injected inputs. No real pane was focused, typed into, or prompted.

They cover empty input, a Codex placeholder, a single-line buffer, CJK and
emoji, blank hard lines, and a literal backslash-n that differs from a hard
newline. Wire escaping, field budgets, cropping and display priority are
owned by `DETAIL-DESIGN.md`; these fixtures specify extraction facts only.

The agy sample includes a repeated prompt marker before autocomplete rows;
its `/statusline ...` entry is a completion candidate, not typed input.

Optional cursor fixtures in `test_detail_input.py` follow the announced
herdr record `{row, col, visible, col_chars?}`. Rows are relative to returned
text; columns count display cells, while `col_chars` counts Unicode scalars.
These metadata cases are synthetic, not a claim that the deployed herdr
already emits them. The fake-socket test returns them verbatim and also
checks a legacy response with no cursor field.
