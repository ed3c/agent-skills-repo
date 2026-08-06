# Agent Skills portability boundary

This repository keeps its historical authoring format under `skills/` and publishes a
deterministic Agent Skills interchange format under `dist/agent-skills/`.

## Source and distribution roles

```text
skills/<source_id>/
├── skills.md                 # repository-native source
├── cases.json                # optional behavior checks explicitly used by the source
├── references/               # optional behavior references
├── scripts/                  # optional executable helpers
└── assets/                   # optional static resources
          │
          │ python scripts/export_agent_skills.py --write
          ▼
dist/agent-skills/<portable-name>/
├── SKILL.md                  # Agent Skills frontmatter + byte-identical source body
├── cases.json                # copied when present
├── references/               # copied byte-for-byte
├── scripts/                  # copied byte-for-byte
├── assets/                   # copied byte-for-byte
└── export-manifest.json      # digests, lifecycle, upstream pin, and loss report
```

The external name maps underscores to hyphens. `repo_wiki_verified` therefore becomes
`repo-wiki-verified`. A collision such as `foo_bar` and `foo-bar` fails the export instead of
silently overwriting one skill.

## Conformance is not effectiveness

The export adds the required Agent Skills `name` and `description` frontmatter plus license,
compatibility, source digest, and lifecycle metadata. It does not promote a skill:

- `autoresearch_composer` remains `production-seed-candidate` and non-routable; its repository lifecycle record still requires human admission.
- `gemini_interactions` remains `quarantined` and non-routable.
- `repo_wiki_verified` remains `pending-qualification` and non-routable.

A successful `skills-ref validate` result proves format conformance only. Qualification and Arena
ranking require separate execution evidence.

## Determinism and loss accounting

The source artifact digest covers `skills.md`, optional `cases.json`, and all regular files under
`references/`, `scripts/`, and `assets/`. Symlinks, special files, path escapes, malformed source
IDs, and ambiguous section structure fail closed.

The portable digest covers `SKILL.md` and copied behavior resources. `export-manifest.json` is
excluded because a manifest cannot digest itself. Every manifest records:

- source and portable artifact digests;
- exact portable file hashes;
- source lifecycle and routability;
- the pinned upstream Agent Skills repository and commit;
- each additive or byte-preserving transformation;
- an explicit loss list, empty only when behavior content is preserved.

`dist/agent-skills/registry.json` binds all source and portable identities into one deterministic
registry digest.

## Commands

Regenerate checked-in exports after an intentional source change:

```sh
python scripts/export_agent_skills.py --write
```

Verify that checked-in exports are byte-identical to a fresh generation and that their manifests,
digests, lifecycle bindings, and local Agent Skills constraints remain valid:

```sh
python scripts/export_agent_skills.py --check
```

CI additionally installs the upstream Apache-2.0 `skills-ref` validator from the commit pinned in
`data/agent-skills/export-policy.json` and runs:

```sh
skills-ref validate dist/agent-skills/autoresearch-composer
skills-ref validate dist/agent-skills/gemini-interactions
skills-ref validate dist/agent-skills/repo-wiki-verified
```

Changing the upstream commit is a policy change. It creates a new conformance envelope and requires
review of generated artifacts and validator behavior.
