"""Single source of truth for the CLI version, resolved from installed package
metadata. The distribution version itself comes from the git tag at build time
(hatch-vcs), so there is nothing to bump by hand — cutting a release is a tag push.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cognit")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
