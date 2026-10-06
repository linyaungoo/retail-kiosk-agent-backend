"""Write requirements.lock: the exact installed versions of requirements.txt's runtime
dependency tree, read from the current virtualenv (no network needed).

    python -m scripts.lock_requirements

The Dockerfile installs `-r requirements.txt -c requirements.lock`, so images use the
versions the tests ran against.
"""

import re
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from packaging.requirements import Requirement

HEADER = [
    "# Exact runtime versions tested with this backend. Used as pip constraints",
    "# (pip install -r requirements.txt -c requirements.lock) so builds are reproducible.",
    "# Regenerate after upgrading: python -m scripts.lock_requirements",
]
# Installed only on the developer's OS; never needed in the Linux image.
PLATFORM_ONLY = {"pywin32", "colorama"}


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def resolve(requirements: list[Requirement]) -> dict[str, str]:
    """Walk installed dependency metadata from the roots, honouring extras/markers."""
    found: dict[str, tuple[str, str, frozenset[str]]] = {}  # key -> (name, version, extras)
    queue = [(r.name, frozenset(r.extras)) for r in requirements]
    while queue:
        name, extras = queue.pop()
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            continue  # e.g. Linux-only extras such as uvloop on Windows
        key = _key(name)
        if key in found and extras <= found[key][2]:
            continue
        merged = extras | (found[key][2] if key in found else frozenset())
        found[key] = (dist.metadata["Name"], dist.version, merged)
        for raw in dist.requires or []:
            req = Requirement(raw)
            environments = [{"extra": e} for e in (merged or {""})]
            if req.marker is None or any(req.marker.evaluate(env) for env in environments):
                queue.append((req.name, frozenset(req.extras)))
    return {name: version for key, (name, version, _) in found.items() if key not in PLATFORM_ONLY}


def main() -> None:
    lines = Path("requirements.txt").read_text(encoding="utf-8").splitlines()
    roots = [Requirement(line.split("#", 1)[0]) for line in lines if line.split("#", 1)[0].strip()]
    pinned = resolve(roots)
    body = [
        f"{name}=={version}" for name, version in sorted(pinned.items(), key=lambda i: _key(i[0]))
    ]
    Path("requirements.lock").write_text("\n".join(HEADER + body) + "\n", encoding="utf-8")
    print(f"requirements.lock: {len(body)} packages")


if __name__ == "__main__":
    main()
