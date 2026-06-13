import pathspec


def build_ignore(patterns: list[str]) -> pathspec.PathSpec:
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def is_ignored(spec: pathspec.PathSpec, rel: str) -> bool:
    return spec.match_file(rel)
