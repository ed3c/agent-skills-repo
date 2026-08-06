"""Deterministic anchoring oracle for source-anchored wiki pages.

Establishes path and lexical validity of ``(src: <path> `<quote>`)`` anchors
against a pinned fixture repository, plus OKF v0.1 front-matter validity
(reimplemented from openwiki ``src/okf/frontmatter.ts``). It never establishes
semantic entailment: every verdict carries ``scope: "lexical-only"`` and
``llm_judge_authority: "advisory_only"``. No network, no LLM calls.

Every absence (missing wiki dir, empty wiki, missing fixture repo, unverifiable
fixture HEAD, unparseable anchor, undecodable page) is its own explicit failure
state and is never conflated with a genuine "checked and failed".
"""

from __future__ import annotations

import hashlib
import json
import os.path
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

import yaml

SCHEMA_VERSION = "anchor-oracle-verdict@1"
OKF_STRING_FIELDS = ("type", "title", "description", "resource", "timestamp")

# Anchor grammar from the ed3c anchoring experiment (harness/src/audit_wiki.ts):
# (src: relative/path `verbatim quote`), with an optional :N or :N-M line ref
# suffix on the path.
ANCHOR_RE = re.compile(r"\(src:\s*([^\s`]+)\s+`([^`]+)`\s*\)")
ANCHOR_TOKEN_RE = re.compile(r"\(src:")
# Display-only excerpt for reporting a malformed `(src:` token; detection is
# span-based (a token inside a well-formed anchor's quote is legal quote text).
ANCHOR_FRAGMENT_RE = re.compile(r"\(src:[^)]*\)?")
MISSING_PAREN_RE = re.compile(r"(?:^|[^(])src:\s*[^\s`]+\s+`[^`]+`")
LINE_REF_RE = re.compile(r":L?(\d+)(?:-L?(\d+))?$")

AnchorStatus = Literal[
    "resolved",
    "path_escapes_fixture",
    "symlink_escapes_fixture",
    "file_missing",
    "not_a_regular_file",
    "quote_not_found",
    "invalid_line_ref",
    "line_ref_on_undecodable_file",
    "quote_outside_line_ref",
]


class AnchorOracleError(ValueError):
    """Base class: the oracle could not produce a verdict at all."""


class WikiMissing(AnchorOracleError):
    """The wiki directory does not exist (distinct from an empty wiki)."""


class EmptyWiki(AnchorOracleError):
    """The wiki directory exists but contains no Markdown pages."""


class FixtureMissing(AnchorOracleError):
    """The fixture repository directory does not exist."""


class FixtureShaMismatch(AnchorOracleError):
    """The fixture repository HEAD does not equal the pinned sha."""


class FixtureHeadUnverifiable(AnchorOracleError):
    """The fixture HEAD could not be read (not a git repo, git failed),
    or the fixture directory is not its own repository — its HEAD would
    attest the enclosing repository, not the fixture's bytes."""


class FixtureDirty(AnchorOracleError):
    """The fixture repository has uncommitted changes: HEAD attests
    committed bytes only, so a dirty tree voids the pin."""


class UnreadableInput(AnchorOracleError):
    """A wiki page or anchor target exists but cannot be read (I/O or
    permission failure) — an environment absence, not a lexical verdict."""


class FrontmatterIssue(TypedDict):
    code: str
    message: str
    line: NotRequired[int]


class FrontmatterValidation(TypedDict):
    valid: bool
    issues: list[FrontmatterIssue]


class LineRef(TypedDict):
    start: int
    end: int


class AnchorCheck(TypedDict):
    index: int
    path: str
    source_path: str
    line_ref: LineRef | None
    quote: str
    status: AnchorStatus
    target_sha256: str | None


class MalformedAnchor(TypedDict):
    fragment: str
    reason: str


@dataclass(frozen=True)
class Anchor:
    index: int
    path: str
    source_path: str
    line_ref: LineRef | None
    quote: str


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _issue(code: str, message: str, line: int | None = None) -> FrontmatterIssue:
    issue: FrontmatterIssue = {"code": code, "message": message}
    if line is not None:
        issue["line"] = line
    return issue


def _invalid(
    code: str, message: str, line: int | None = None
) -> FrontmatterValidation:
    return {"valid": False, "issues": [_issue(code, message, line)]}


# Mirrors the reference's `maxAliasCount: 100` resource-exhaustion guard.
_MAX_ALIAS_EXPANSION = 100


def _node_size(node: yaml.Node, seen: set[int] | None = None) -> int:
    """Count the nodes an alias expands to (each shared node counted once)."""
    if seen is None:
        seen = set()
    if id(node) in seen:
        return 0
    seen.add(id(node))
    if isinstance(node, yaml.SequenceNode):
        return 1 + sum(_node_size(child, seen) for child in node.value)
    if isinstance(node, yaml.MappingNode):
        return 1 + sum(
            _node_size(key, seen) + _node_size(value, seen)
            for key, value in node.value
        )
    return 1


class _OkfCoreLoader(yaml.SafeLoader):
    """SafeLoader restricted to the reference's YAML parse options.

    frontmatter.ts parses with ``{schema: "core", uniqueKeys: true,
    maxAliasCount: 100}``: YAML 1.2 core scalar resolution (no timestamp/date
    resolver, no yes/no/on/off booleans, no sexagesimal numbers), duplicate
    mapping keys rejected, and alias expansion capped against resource
    exhaustion.
    """

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._alias_expansion = 0

    def compose_node(
        self, parent: yaml.Node | None, index: object
    ) -> yaml.Node:
        if self.check_event(yaml.events.AliasEvent):
            node = super().compose_node(parent, index)
            self._alias_expansion += _node_size(node)
            if self._alias_expansion > _MAX_ALIAS_EXPANSION:
                raise yaml.YAMLError(
                    "excessive alias expansion: aliases expand to more than"
                    f" {_MAX_ALIAS_EXPANSION} nodes"
                )
            return node
        return super().compose_node(parent, index)


# YAML 1.2 core-schema implicit resolvers replace SafeLoader's YAML 1.1 set
# (which would resolve timestamps, yes/no/on/off booleans and sexagesimals).
_OkfCoreLoader.yaml_implicit_resolvers = {}
_OkfCoreLoader.add_implicit_resolver(
    "tag:yaml.org,2002:null",
    re.compile(r"^(?:~|null|Null|NULL|)$"),
    ["~", "n", "N", ""],
)
_OkfCoreLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)
_OkfCoreLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9A-Fa-f]+)$"),
    list("-+0123456789"),
)
_OkfCoreLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(
        r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
        r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"
    ),
    list("-+0123456789."),
)


def _construct_core_int(loader: yaml.Loader, node: yaml.ScalarNode) -> int:
    value = loader.construct_scalar(node)
    if value.startswith("0o"):
        return int(value[2:], 8)
    if value.startswith("0x"):
        return int(value[2:], 16)
    return int(value, 10)


def _construct_core_float(loader: yaml.Loader, node: yaml.ScalarNode) -> float:
    value = loader.construct_scalar(node)
    lowered = value.lower()
    if lowered.endswith(".inf"):
        return float("-inf") if lowered.startswith("-") else float("inf")
    if lowered.endswith(".nan"):
        return float("nan")
    return float(value)


# Core-schema constructors: SafeLoader's YAML 1.1 int constructor would treat
# a leading zero as octal ("019" -> error) instead of core-schema decimal.
_OkfCoreLoader.add_constructor("tag:yaml.org,2002:int", _construct_core_int)
_OkfCoreLoader.add_constructor("tag:yaml.org,2002:float", _construct_core_float)


def _construct_unique_mapping(
    loader: _OkfCoreLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if isinstance(key, (dict, list)):
            raise yaml.constructor.ConstructorError(
                None, None, "unhashable mapping key", key_node.start_mark
            )
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate mapping key: {key!r}", key_node.start_mark
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_OkfCoreLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def validate_okf_frontmatter(content: str) -> FrontmatterValidation:
    """Parse and validate OKF front matter while tolerating producer extensions.

    Reimplements ``validateOkfFrontmatter`` from openwiki
    ``src/okf/frontmatter.ts``: the file must begin with a ``---`` line, close
    the block with an exact ``---`` line, parse as a YAML mapping under the
    reference's parse options (YAML 1.2 core schema, unique keys, alias
    expansion capped at 100 nodes), declare ``type``, and every present OKF
    string field must be a non-empty string. Unknown producer extension fields
    are tolerated.
    """
    lines = re.split(r"\r?\n", content)
    if lines[0] != "---":
        return _invalid(
            "missing_opening_delimiter", "File must begin with `---`.", 1
        )
    try:
        closing_line = lines.index("---", 1)
    except ValueError:
        return _invalid(
            "missing_closing_delimiter",
            "Opening front matter has no closing `---` delimiter.",
        )

    try:
        fields = yaml.load(  # noqa: S506 - SafeLoader subclass
            "\n" + "\n".join(lines[1:closing_line]), Loader=_OkfCoreLoader
        )
    except yaml.YAMLError as error:
        return _invalid("invalid_yaml", str(error))
    if not isinstance(fields, dict):
        return _invalid("invalid_yaml_root", "Front matter must be a YAML mapping.")

    issues: list[FrontmatterIssue] = []
    if "type" not in fields:
        issues.append(_issue("missing_type", "Required field `type` is missing."))
    for field in OKF_STRING_FIELDS:
        if field in fields and (
            not isinstance(fields[field], str) or not fields[field].strip()
        ):
            issues.append(
                _issue(
                    f"invalid_{field}",
                    f"Field `{field}` must be a non-empty string.",
                )
            )
    if "tags" in fields:
        tags = fields["tags"]
        if not isinstance(tags, list) or any(
            not isinstance(tag, str) or not tag.strip() for tag in tags
        ):
            issues.append(
                _issue(
                    "invalid_tags",
                    "Field `tags` must be a YAML list of non-empty strings.",
                )
            )
    if issues:
        return {"valid": False, "issues": issues}
    return {"valid": True, "issues": []}


def _split_line_ref(path_token: str) -> tuple[str, LineRef | None]:
    match = LINE_REF_RE.search(path_token)
    if match is None or not path_token[: match.start()]:
        return path_token, None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) is not None else start
    return path_token[: match.start()], {"start": start, "end": end}


def extract_anchors(text: str) -> tuple[list[Anchor], list[MalformedAnchor]]:
    """Find every well-formed anchor plus every malformed anchor attempt.

    A malformed attempt (``src:`` without its opening parenthesis, or a
    ``(src:`` token that does not begin a well-formed anchor) is reported
    explicitly; it is never silently dropped. A ``(src:`` token or ``)``
    inside a well-formed anchor's backtick quote is legal quote text
    (the quote grammar is ``[^`]+``) and is never reported as malformed.
    """
    parsed = list(ANCHOR_RE.finditer(text))
    spans = [match.span() for match in parsed]

    malformed: list[MalformedAnchor] = []
    pos = 0
    while (match := MISSING_PAREN_RE.search(text, pos)) is not None:
        # The leading (?:^|[^(]) may consume one char before the `src:` token;
        # a token inside a well-formed anchor's quote is legal quote text.
        src_position = match.start() + match.group(0).index("src:")
        if any(start <= src_position < end for start, end in spans):
            # Advance past the `src:` token only: the skipped match may span
            # beyond the anchor, and consuming it whole would swallow a later
            # genuine bare attempt starting inside that span.
            pos = src_position + len("src:")
            continue
        malformed.append(
            {
                "fragment": match.group(0).strip()[:120],
                "reason": (
                    "anchor missing its opening parenthesis: must start with"
                    " `(src:`"
                ),
            }
        )
        pos = match.end()
    for token in ANCHOR_TOKEN_RE.finditer(text):
        position = token.start()
        if any(start <= position < end for start, end in spans):
            continue
        fragment = ANCHOR_FRAGMENT_RE.match(text, position)
        assert fragment is not None  # ANCHOR_FRAGMENT_RE starts with `(src:`
        malformed.append(
            {
                "fragment": fragment.group(0)[:120],
                "reason": (
                    "malformed anchor: expected `(src: <path> `quote`)`"
                ),
            }
        )

    anchors: list[Anchor] = []
    for index, match in enumerate(parsed):
        path_token = match.group(1)
        source_path, line_ref = _split_line_ref(path_token)
        anchors.append(
            Anchor(
                index=index,
                path=path_token,
                source_path=source_path,
                line_ref=line_ref,
                quote=match.group(2),
            )
        )
    return anchors, malformed


def _is_inside(root: Path, candidate: Path) -> bool:
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return False
    return str(relative) != "."


def check_anchor(fixture_repo: Path | str, anchor: Anchor) -> AnchorCheck:
    """Resolve one anchor against the fixture repository, fail-closed.

    Check order: lexical containment (traversal / absolute paths rejected
    before touching the filesystem), existence, symlink containment, regular
    file, verbatim quote in the file's bytes, then the optional line ref.
    """
    fixture = Path(fixture_repo)
    check: AnchorCheck = {
        "index": anchor.index,
        "path": anchor.path,
        "source_path": anchor.source_path,
        "line_ref": anchor.line_ref,
        "quote": anchor.quote,
        "status": "resolved",
        "target_sha256": None,
    }

    def failed(status: AnchorStatus) -> AnchorCheck:
        check["status"] = status
        return check

    # os.path-style normalization without resolving symlinks; an absolute
    # anchor path replaces the fixture root and is then rejected as an escape.
    normalized = Path(os.path.normpath(str(fixture / anchor.source_path)))
    if not _is_inside(Path(os.path.normpath(str(fixture))), normalized):
        return failed("path_escapes_fixture")
    if not normalized.exists():
        return failed("file_missing")
    fixture_real = Path(os.path.realpath(str(fixture)))
    target_real = Path(os.path.realpath(str(normalized)))
    if not _is_inside(fixture_real, target_real):
        return failed("symlink_escapes_fixture")
    if not target_real.is_file():
        return failed("not_a_regular_file")

    try:
        data = target_real.read_bytes()
    except OSError as exc:
        raise UnreadableInput(
            f"anchor target cannot be read: {target_real}: {exc}"
        ) from exc
    check["target_sha256"] = _sha256(data)
    if anchor.line_ref is not None:
        start, end = anchor.line_ref["start"], anchor.line_ref["end"]
        if start < 1 or end < start:
            return failed("invalid_line_ref")
    if anchor.quote.encode("utf-8") not in data:
        return failed("quote_not_found")

    if anchor.line_ref is not None:
        start, end = anchor.line_ref["start"], anchor.line_ref["end"]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return failed("line_ref_on_undecodable_file")
        segment = "\n".join(text.split("\n")[start - 1 : end])
        if anchor.quote not in segment:
            return failed("quote_outside_line_ref")
    return check


def evaluate_page(
    fixture_repo: Path | str, case_id: str, page_bytes: bytes
) -> dict[str, object]:
    """Produce one case record: front matter + every anchor, with diagnostics.

    ``passed`` requires valid OKF front matter, no malformed anchor attempts,
    at least one anchor, and every anchor resolved. Every failure is named in
    ``failures`` with the anchor index, path, and failing check.
    """
    failures: list[str] = []
    checks: dict[str, object]
    try:
        text = page_bytes.decode("utf-8")
    except UnicodeDecodeError:
        checks = {"page_sha256": _sha256(page_bytes), "page_decodable": False}
        failures.append("page_not_utf8")
    else:
        frontmatter = validate_okf_frontmatter(text)
        anchors, malformed = extract_anchors(text)
        anchor_checks = [
            check_anchor(fixture_repo, anchor) for anchor in anchors
        ]
        checks = {
            "page_sha256": _sha256(page_bytes),
            "page_decodable": True,
            "frontmatter": frontmatter,
            "anchors": anchor_checks,
            "malformed_anchors": malformed,
            "anchor_total": len(anchor_checks),
            "anchor_resolved": sum(
                1 for item in anchor_checks if item["status"] == "resolved"
            ),
        }
        if not frontmatter["valid"]:
            failures.extend(
                f"frontmatter:{issue['code']}" for issue in frontmatter["issues"]
            )
        failures.extend(
            f"malformed_anchor: {item['fragment']}" for item in malformed
        )
        if not anchor_checks:
            failures.append("no_anchors")
        failures.extend(
            f"anchor[{item['index']}] {item['path']}: {item['status']}"
            for item in anchor_checks
            if item["status"] != "resolved"
        )

    record = {
        "case_id": case_id,
        "checks": checks,
        "failures": failures,
        "passed": not failures,
    }
    return {**record, "evidence_digest": _sha256(canonical_bytes(record))}


def evaluate_wiki(
    wiki_dir: Path | str, fixture_repo: Path | str, *, fixture_sha: str
) -> dict[str, object]:
    """Evaluate every Markdown page under ``wiki_dir`` against the fixture repo.

    Raises ``WikiMissing`` / ``FixtureMissing`` when an input directory is
    absent and ``EmptyWiki`` when the wiki has no pages: an absent input is
    never reported as a verdict.
    """
    wiki = Path(wiki_dir)
    fixture = Path(fixture_repo)
    if not wiki.is_dir():
        raise WikiMissing(f"wiki directory does not exist: {wiki}")
    if not fixture.is_dir():
        raise FixtureMissing(f"fixture repository does not exist: {fixture}")

    pages = sorted(
        path
        for path in wiki.rglob("*.md")
        if path.is_file() and ".git" not in path.relative_to(wiki).parts
    )
    if not pages:
        raise EmptyWiki(f"wiki directory contains no Markdown pages: {wiki}")

    cases = []
    for page in pages:
        try:
            raw = page.read_bytes()
        except OSError as exc:
            raise UnreadableInput(
                f"wiki page cannot be read: {page}: {exc}"
            ) from exc
        cases.append(
            evaluate_page(fixture, page.relative_to(wiki).as_posix(), raw)
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_sha": fixture_sha,
        "scope": "lexical-only",
        "llm_judge_authority": "advisory_only",
        "page_count": len(cases),
        "passed": all(case["passed"] for case in cases),
        "cases": cases,
    }


def _git(fixture: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(fixture), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise FixtureHeadUnverifiable(f"git could not run: {exc}") from exc


def verify_fixture_head(fixture_repo: Path | str, expected_sha: str) -> str:
    """Fail closed unless the fixture repository's git HEAD equals the pin.

    The fixture must be its own repository — ``git rev-parse`` walks up to
    an enclosing repository, whose HEAD attests nothing about the fixture's
    bytes — and its working tree must be clean, because HEAD attests
    committed bytes only.

    Returns the verified HEAD sha. Raises ``FixtureMissing`` when the
    directory is absent, ``FixtureHeadUnverifiable`` when git cannot report
    a HEAD or the fixture is not its own repository, ``FixtureDirty`` when
    the tree has uncommitted changes, and ``FixtureShaMismatch`` when HEAD
    differs from ``expected_sha``.
    """
    fixture = Path(fixture_repo)
    if not fixture.is_dir():
        raise FixtureMissing(f"fixture repository does not exist: {fixture}")
    toplevel = _git(fixture, "rev-parse", "--show-toplevel")
    if toplevel.returncode != 0:
        raise FixtureHeadUnverifiable(
            "fixture is not inside a git repository"
            f" (git rev-parse exited {toplevel.returncode}):"
            f" {toplevel.stderr.strip()}"
        )
    toplevel_path = Path(toplevel.stdout.strip()).resolve()
    if toplevel_path != fixture.resolve():
        raise FixtureHeadUnverifiable(
            "fixture is not its own git repository: its HEAD would attest"
            f" the enclosing repository at {toplevel_path}"
        )
    result = _git(fixture, "rev-parse", "HEAD")
    if result.returncode != 0:
        raise FixtureHeadUnverifiable(
            "fixture HEAD cannot be read"
            f" (git rev-parse exited {result.returncode}):"
            f" {result.stderr.strip()}"
        )
    status = _git(fixture, "status", "--porcelain")
    if status.returncode != 0:
        raise FixtureHeadUnverifiable(
            "fixture tree state cannot be read"
            f" (git status exited {status.returncode}):"
            f" {status.stderr.strip()}"
        )
    if status.stdout.strip():
        raise FixtureDirty(
            "fixture repository has uncommitted changes; HEAD attests"
            f" committed bytes only:\n{status.stdout.strip()}"
        )
    head = result.stdout.strip().lower()
    if head != expected_sha.strip().lower():
        raise FixtureShaMismatch(
            f"fixture HEAD mismatch: HEAD={head} expected={expected_sha}"
        )
    return head
