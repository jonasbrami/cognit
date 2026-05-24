from cognit.engine.mermaid import distinctness_failure, uniformity_failures


def _opts(*srcs: str) -> dict[str, str]:
    return {k: s for k, s in zip("ABCD", srcs)}


def test_uniform_diagrams_pass() -> None:
    a = "flowchart LR\n  A-->B\n  B-->C"
    b = "flowchart LR\n  A-->C\n  C-->B"
    c = "flowchart LR\n  B-->A\n  A-->C"
    d = "flowchart LR\n  C-->B\n  B-->A"
    assert uniformity_failures(_opts(a, b, c, d)) == []


def test_mixed_header_or_direction_flagged() -> None:
    fails = uniformity_failures(
        _opts(
            "flowchart LR\nA-->B",
            "flowchart TD\nA-->B",  # different direction
            "flowchart LR\nA-->B",
            "flowchart LR\nA-->B",
        )
    )
    assert any("header" in f for f in fails)


def test_size_outlier_flagged() -> None:
    small = "flowchart LR\nA-->B"
    big = "flowchart LR\n" + "\n".join(f"N{i}-->N{i + 1}" for i in range(8))
    fails = uniformity_failures(_opts(small, small, small, big))
    assert any("size" in f.lower() or "line" in f.lower() for f in fails)


def test_under_two_options_is_noop() -> None:
    assert uniformity_failures({"A": "flowchart LR\nA-->B"}) == []


def test_distinctness_flags_four_identical_diagrams():
    src = "flowchart LR\n  A-->B-->C"
    fails = distinctness_failure({"A": src, "B": src, "C": src, "D": src})
    assert fails and "distinct" in fails[0]


def test_distinctness_ignores_whitespace_only_differences():
    a = "flowchart LR\n  A-->B"
    b = "flowchart LR\n    A-->B"  # same diagram, extra indentation
    assert distinctness_failure({"A": a, "B": b})  # treated as identical -> failure


def test_distinctness_passes_when_all_distinct():
    opts = {
        "A": "flowchart LR\n  A-->B-->C",
        "B": "flowchart LR\n  A-->C-->B",
        "C": "flowchart LR\n  B-->A-->C",
        "D": "flowchart LR\n  C-->B-->A",
    }
    assert distinctness_failure(opts) == []
