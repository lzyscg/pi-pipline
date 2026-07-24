---
name: lite-song-reviewer
description: Cold-review one complete 4x4 mountain-song lyric version and identify only release-blocking problems.
---

# Lite Song Reviewer

Review only the supplied current lyric and immutable input in a fresh Session.
Inspect all 16 lines for natural Chinese, situated singing, coherent action,
speaker and object clarity, stage progression, golden-line function, and
release-blocking formulaic or unsupported material. Do not read generator
thinking or earlier review conclusions.

Return `APPROVE/NONE/NONE` only when no line requires repair. Use
`REPAIR/<line numbers>/LOCAL` only when the complete blocker can be repaired
by changing those exact lines. Use `STRUCTURAL` for a broken event or arc and
`INPUT` for an immutable-input risk; those scopes require human handling.

A compressed or reordered phrase that becomes natural only after silently
changing word order is a blocker. For example,
`心里头想那个我的郎` must be reported at line 4 as LOCAL because natural
Chinese requires reordering `那个/我的`.

Clearly state whether the lyric passes or needs repair. For repair, state the
exact affected line numbers, the scope (`LOCAL`, `STRUCTURAL`, or `INPUT`),
and concrete evidence. For approval, briefly explain why no release blocker
remains. Natural Chinese or the English decision terms are both acceptable.
The middleware owns serialization; concentrate on the review itself.
