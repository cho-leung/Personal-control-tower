"""Compatibility shim for legacy editable installs.

Modern build frontends use ``pyproject.toml``.  In an offline environment whose
pip predates PEP 660 and which lacks the declared build requirements, use
``python setup.py develop --no-deps`` after installing the runtime dependency.
"""

from pathlib import Path

from setuptools import find_packages, setup


ROOT = Path(__file__).parent


setup(
    name="personal-control-tower-bus",
    version="3.0.0a3",
    description=(
        "Local-first, Root-governed CLI control tower for projects, "
        "agents, tasks, and auditable handoffs"
    ),
    long_description=(ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    python_requires=">=3.9",
    packages=find_packages(include=("control_tower", "control_tower.*")),
    install_requires=["PyYAML>=6,<7"],
    entry_points={
        "console_scripts": [
            "control-tower=control_tower.cli:main",
        ],
    },
)
