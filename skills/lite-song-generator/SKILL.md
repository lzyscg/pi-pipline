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

Return one complete latest lyric with four stanzas of four lines. A short
natural-language introduction is acceptable, but do not include two competing
lyric versions. The middleware owns extraction and serialization; concentrate
on the lyric and the authorized repair scope.
