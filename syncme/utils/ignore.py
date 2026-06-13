import pathspec


def build_ignore(patterns: list[str]) -> pathspec.PathSpec:
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def is_ignored(spec: pathspec.PathSpec, rel: str) -> bool:
    return spec.match_file(rel)


def is_dir_ignored(spec: pathspec.PathSpec, rel: str) -> bool:
    """Return True if a directory should be skipped entirely.

    Checks both 'vendor' and 'vendor/' forms so that gitignore patterns with
    or without a trailing slash both prune the directory correctly.
    """
    return spec.match_file(rel) or spec.match_file(rel.rstrip("/") + "/")
