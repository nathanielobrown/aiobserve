-- The numbers behind one compaction's NavTree row: where the window stood either side of the
-- drop, and what asked for it.
--
-- A compaction is made of no api calls, so there is no spend to print here and no model to name
-- one against: the boundary record carries its own two token counts (`docs/schema.md`), and
-- they are the whole of what it has. `view_compactions.sql` reads the same columns for the
-- markers a timeline interleaves; this answers one of them, for the popover its row fetches.
SELECT
    k.pre_tokens,
    k.post_tokens,
    -- What the drop gave back. Derived in the query rather than in the template, so the number
    -- the popover prints is one the citation beside it can be read for.
    k.pre_tokens - k.post_tokens AS freed,
    -- Cut the way the marker's own column is, so the popover and the row say one thing.
    substr(k.trigger, 1, $chip_chars) AS trigger
FROM live_compactions k
WHERE k.session_id = $session_id AND k.source = $source AND k.id = $compaction_id;
