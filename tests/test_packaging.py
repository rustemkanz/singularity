"""Guard the editable-install surface declared in ``pyproject.toml``.

A missing ``py-modules`` entry (for example ``mutation_plans``) produces an ``sg``
console entrypoint that imports fine from the source checkout but raises
``ModuleNotFoundError`` after ``pip install -e .``. These checks fail in CI the
moment a new top-level module or package is added without declaring it.
"""

import tomllib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
# Top-level ``.py`` files that are not part of the importable package surface.
NON_MODULE_ROOT_FILES = {"conftest"}


def _load_setuptools_config() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["tool"]["setuptools"]


class PackagingSurfaceTests(unittest.TestCase):
    def test_py_modules_matches_top_level_modules(self):
        config = _load_setuptools_config()
        declared = set(config["py-modules"])
        discovered = {
            path.stem
            for path in REPO_ROOT.glob("*.py")
            if path.stem not in NON_MODULE_ROOT_FILES
        }
        self.assertEqual(
            declared,
            discovered,
            "pyproject.toml [tool.setuptools] py-modules is out of sync with the "
            "top-level *.py modules in the repository root.",
        )

    def test_declared_packages_are_importable_directories(self):
        config = _load_setuptools_config()
        for package in config["packages"]:
            package_dir = REPO_ROOT / Path(*package.split("."))
            self.assertTrue(package_dir.is_dir(), f"Declared package '{package}' is not a directory.")
            self.assertTrue(
                (package_dir / "__init__.py").is_file(),
                f"Declared package '{package}' has no __init__.py.",
            )

    def test_entrypoint_modules_import_without_the_repo_root_package_context(self):
        # The console entrypoint is ``sg:main``; every module it pulls in must be
        # importable as a bare top-level module, not only as part of the checkout.
        config = _load_setuptools_config()
        import importlib

        for module_name in config["py-modules"]:
            if module_name == "sg":
                continue
            importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()
