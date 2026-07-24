---
name: lite-song-generator
description: Generate or locally repair one complete 4x4 Chinese mountain-song lyric in a persistent Case session.
---

# Lite Song Generator

Create one integrated Chinese mountain-song lyric from the supplied reference
material, immutable golden line, style, and constraints. Internally follow the
business requirements of `shan-song-event`, `shan-song-arc`,
`shan-song-compose`, and, for an authorized local repair,
`shan-song-line-revise`.

The lyric must contain four stanzas of four non-empty lines. The immutable
golden line must be the complete line 9 and line 13 and nowhere else. Do not
use Chinese or English punctuation. Do not duplicate a non-golden line or use
forbidden terms.

For a repair turn, the runtime supplies the previous complete lyric,
`allowed_lines`, and `locked_lines`. Change only allowed lines. Every locked
line must remain byte-for-byte identical. Return the entire latest lyric.

Return exactly this contract:

```text
# GenerationResult v1
SUMMARY:
one short plain-text summary
LYRIC:
line 1
line 2
line 3
line 4

line 5
line 6
line 7
line 8

line 9
line 10
line 11
line 12

line 13
line 14
line 15
line 16
```

Use exactly one blank line between stanzas and no blank line inside a stanza.
No heading, code fence, line number, JSON, commentary, or trailing text is
allowed outside these fields.

For this Lite runtime the displayed version line is required. Copy these
literal structural lines without translating, changing case, or renaming:
`# GenerationResult v1`, `SUMMARY:`, and `LYRIC:`. The leading `#` is the
protocol version marker, not an optional prose heading.

`SUMMARY:` and `LYRIC:` must each occupy a line by themselves. Put the summary
on the following line. Put the first lyric line only after the standalone
`LYRIC:` line. Insert one empty line after lyric lines 4, 8, and 12.
