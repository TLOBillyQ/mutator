"""Decide which lines the tests already exercise.

Java uses JaCoCo source lines. Go uses statement profiles. Clojure prefers
Cloverage's per-line form counts, then LCOV. The other languages use LCOV.
A file that does not appear in a report has no coverage data.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from mutator.crapper_link import ensure_crapper


def _candidates(normalize, source_path: str) -> list[str]:
    absolute = normalize(str(Path(source_path).resolve())) if source_path else ""
    candidates = []
    for value in (normalize(source_path), absolute):
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _exact(normalized: dict, candidates: list[str]):
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def _suffix_hit(key: str, candidates: list[str], suffix_match) -> bool:
    for candidate in candidates:
        if suffix_match(key, candidate) or suffix_match(candidate, key):
            return True
    return False


def _by_suffix(normalized: dict, candidates: list[str], suffix_match):
    for key, value in normalized.items():
        if _suffix_hit(key, candidates, suffix_match):
            return value
    return None


def _lookup(index: dict, source_path: str):
    if not index:
        return None
    crapper = ensure_crapper()
    normalize = crapper.coverage.normalize_path
    normalized = {normalize(key): value for key, value in index.items()}
    candidates = _candidates(normalize, source_path)
    found = _exact(normalized, candidates)
    if found is not None:
        return found
    return _by_suffix(normalized, candidates, crapper.coverage.suffix_match)


def _covered_from_pairs(lines: dict[int, tuple[int, int]] | None) -> set[int] | None:
    if lines is None:
        return None
    return {number for number, (covered, _total) in lines.items() if covered > 0}


def _jacoco_paths(root: Path) -> list[Path]:
    paths = [root / "target" / "site" / "jacoco" / "jacoco.xml"]
    paths.extend(root.glob("*/target/site/jacoco/jacoco.xml"))
    paths.extend(root.glob("*/*/target/site/jacoco/jacoco.xml"))
    return paths


def _line_number(line) -> int | None:
    try:
        number = int(line.get("nr") or "0")
        instructions = int(line.get("ci") or "0")
        branches = int(line.get("cb") or "0")
    except ValueError:
        return None
    if instructions > 0 or branches > 0:
        return number
    return None


def _source_lines(source) -> set[int]:
    covered = set()
    for line in source.findall("line"):
        number = _line_number(line)
        if number is not None:
            covered.add(number)
    return covered


def _package_lines(xml_root) -> dict[str, set[int]]:
    found: dict[str, set[int]] = {}
    for package in xml_root.iter("package"):
        package_name = (package.get("name") or "").strip("/")
        for source in package.findall("sourcefile"):
            filename = source.get("name") or ""
            key = f"{package_name}/{filename}" if package_name else filename
            found[key] = _source_lines(source)
    return found


def _read_jacoco(path: Path, crapper):
    text = path.read_text(encoding="utf-8", errors="replace")
    cleaned = crapper.coverage._DOCTYPE.sub("", text)
    try:
        return ET.fromstring(cleaned)
    except ET.ParseError:
        return None


def _jacoco_index(root: Path) -> dict[str, set[int]]:
    crapper = ensure_crapper()
    found: dict[str, set[int]] = {}
    seen: set[Path] = set()
    for path in _jacoco_paths(root):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        xml_root = _read_jacoco(path, crapper)
        if xml_root is None:
            continue
        found.update(_package_lines(xml_root))
    return found


def _go_lines(profile, source_path: str) -> set[int] | None:
    if not profile:
        return None
    crapper = ensure_crapper()
    segments = crapper.coverage._profile_segments(profile, source_path)
    if segments is None:
        return None
    covered: set[int] = set()
    for start, end, _statements, hits in segments:
        if hits <= 0:
            continue
        for line in range(start, end + 1):
            covered.add(line)
    return covered


def covered_lines(root: Path, source_path: Path, language: str) -> set[int] | None:
    """Lines with a hit, or None when this file is absent from coverage."""

    crapper = ensure_crapper()
    path = source_path.resolve().as_posix()
    if language == "java":
        return _lookup(_jacoco_index(root), path)
    bundle = crapper.coverage.load_bundle(root)
    if language == "go":
        return _go_lines(bundle.go_profile, path)
    if language == "clojure":
        html = _covered_from_pairs(_lookup(bundle.form_html, path))
        if html is not None:
            return html
    return _covered_from_pairs(_lookup(bundle.lcov, path))
