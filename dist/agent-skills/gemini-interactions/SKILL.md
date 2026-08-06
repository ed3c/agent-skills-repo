---
name: "gemini-interactions"
description: "Generate or audit Gemini Interactions API code when the task asks for that API. Use when: User asks for Gemini Interactions API implementation, migration, or compliance review."
license: "MIT"
compatibility: "Portable Agent Skills export from agent-skills-repo. Host access is limited to the bundled instructions and resources; conformance does not imply qualification."
metadata:
  ed3c-export-schema: "agent-skills-export@1"
  ed3c-lifecycle-status: "quarantined"
  ed3c-production-routable: "false"
  ed3c-source-artifact-digest: "sha256:848b8570b2ddcef4657a872bd83f0b644676b02f0d81b769893e5b97f3a2d963"
  ed3c-source-skill-id: "gemini_interactions"
  ed3c-source-version: "unversioned"
---
# Gemini Interactions

WHY: Generate or audit Gemini Interactions API code when the task asks for that API.

HOW: Use cases.json checks, prefer current Interactions method names, and load references/deploy_guide.md only for deployment details.

WHEN: User asks for Gemini Interactions API implementation, migration, or compliance review.

WHEN NOT: Angular UI components, Vue components, static data extraction, or unrelated cloud deployment.
