# Repo Wiki Verified

WHY: A generated repo wiki is worthless when its claims float free of source. This skill binds every factual claim to one pinned fixture commit through a mechanical anchor oracle, so reviewers audit bytes instead of prose confidence.

HOW: Generate the wiki with the pinned openwiki build into OKF v0.1 pages. Anchor every factual claim as (src: path `verbatim quote`). Run scripts/anchor_oracle.py against the pinned fixture sha: exit 0 pass, 2 fail, 3 absence. Repair each named failure, rerun, repeat until pass. Full procedure, repair table, and absence taxonomy live in references/procedure.md.

WHEN: A repo wiki, architecture note, or claim inventory must survive a mechanical source anchor check against one pinned commit before anyone relies on it.

WHEN NOT: When the goal is semantic truth or freshness. A lexical pass only proves each quote exists at the pinned sha; it never proves a claim true, current, or complete. Not for unpinned checkouts, dirty fixture trees, or network-fetched sources.
