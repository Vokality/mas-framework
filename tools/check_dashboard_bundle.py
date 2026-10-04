"""Verify the committed dashboard build and its packaged server wheel."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from zipfile import ZipFile

_FILES = ("index.html", "assets/dashboard.js", "assets/dashboard.css")


class _Options(argparse.Namespace):
    rebuild: bool
    wheel: list[Path]


def read_bundle(directory: Path) -> dict[str, bytes]:
    """Require exactly the files understood by the memory-only asset server."""
    found = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file()
    }
    if found != set(_FILES):
        raise ValueError(f"Unexpected dashboard bundle files: {sorted(found)}")
    contents = {name: (directory / name).read_bytes() for name in _FILES}
    if any(not body for body in contents.values()):
        raise ValueError("Dashboard bundle contains an empty file")
    return contents


def main() -> None:
    """Check source freshness after a rebuild and optional wheel contents."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--wheel", type=Path, nargs="+", default=[])
    args = _Options()
    parser.parse_args(namespace=args)
    repository = Path(__file__).resolve().parents[1]
    directory = repository / "packages/mas-server/src/mas_server/dashboard_assets"
    contents = read_bundle(directory)
    if args.rebuild:
        subprocess.run(
            ["npm", "run", "build"], cwd=repository / "dashboard", check=True
        )
        rebuilt = read_bundle(directory)
        changed = [name for name in _FILES if contents[name] != rebuilt[name]]
        if changed:
            raise ValueError(
                "Dashboard bundle is stale; rebuild and commit: " + ", ".join(changed)
            )
    for wheel in args.wheel:
        with ZipFile(wheel) as archive:
            prefix = "mas_server/dashboard_assets/"
            packaged = {
                name.removeprefix(prefix)
                for name in archive.namelist()
                if name.startswith(prefix) and not name.endswith("/")
            }
            if packaged != set(_FILES):
                raise ValueError(f"Dashboard assets missing or unexpected in {wheel}")
            if "mas_server/dashboard.html" in archive.namelist():
                raise ValueError(f"Legacy dashboard included in {wheel}")
            for name, body in contents.items():
                if archive.read(prefix + name) != body:
                    raise ValueError(f"Stale dashboard asset in {wheel}: {name}")
    print("Dashboard bundle verified")


if __name__ == "__main__":
    main()
