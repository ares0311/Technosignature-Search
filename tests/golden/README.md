# Golden UX baselines

`docs/CLI_UX_SPEC.md` section 13 requires stable golden assertions for the
operator surface, and explicitly states:

> Do not require byte-identical timing-dependent animation frames.
> Assert stable semantic elements.

Each `*.txt` file in this directory is therefore a **semantic** baseline, not a
screen capture. Every non-empty, non-comment line is a token that must appear in
the corresponding real surface's output. Lines beginning with `!` are negative
assertions: the token must **not** appear.

`tests/test_hunter_golden_ux.py` runs the real installed surface and checks each
baseline. Because the assertions are token-level, adding a column, restyling a
table, or changing an animation frame does not break them — but losing a
required command, description, field, validation message, stage name, or
non-TTY guarantee does.

## Sibling baselines

`startup_neo.txt` and `startup_exo.txt` exist because the specification names
them for the shared three-Hunter contract. In *this* repository they are
negative controls: they assert that Techno-Hunter identifies itself correctly and
never claims NEO-Hunter or EXO-Hunter identity. NEO and EXO own their own
positive startup baselines in their own repositories; this repository must not
fabricate them.
