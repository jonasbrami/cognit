from cognit.mcp.server import do_file_diff

SECTIONS = {
    "src/a.py": "diff --git a/src/a.py b/src/a.py\n@@ -1 +1 @@\n-x\n+y\n",
    "src/b.py": "diff --git a/src/b.py b/src/b.py\n@@ -1 +1 @@\n-p\n+q\n",
}


def test_exact_path():
    assert "x\n+y" in do_file_diff("src/a.py", SECTIONS)


def test_basename_fallback():
    assert "p\n+q" in do_file_diff("b.py", SECTIONS)


def test_unknown_path_lists_changed_files():
    out = do_file_diff("nope.py", SECTIONS)
    assert "No changed file matches" in out and "src/a.py" in out and "src/b.py" in out


def test_ambiguous_basename_not_guessed():
    sections = {"x/a.py": "AAA", "y/a.py": "BBB"}
    out = do_file_diff("a.py", sections)
    assert "No changed file matches" in out


def test_suffix_match_respects_path_boundary():
    sections = {"src/a.py": "AAA", "src/ya.py": "BBB"}
    out = do_file_diff("a.py", sections)
    assert out == "AAA"  # matches src/a.py only, NOT src/ya.py


def test_repo_relative_suffix_match():
    sections = {"src/cognit/foo.py": "FOO"}
    assert do_file_diff("cognit/foo.py", sections) == "FOO"


def test_empty_and_whitespace_path_no_match():
    sections = {"only.py": "X"}
    assert "No changed file matches" in do_file_diff("", sections)
    assert do_file_diff(" only.py ", sections) == "X"  # stripped
