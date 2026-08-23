from pathlib import Path

from .vault import Vault
from .registry import RegistryLoader
from .sync import check_registry_runtime

def render_status(vault_path: Path):

    vault = Vault(vault_path)

    lines = []

    lines.append("PERSONAL CONTROL TOWER v1 STATUS")
    lines.append("=" * 72)

    # ==========================
    # Registry Layer
    # ==========================

    lines.append("")
    lines.append("PROJECT REGISTRY")
    lines.append("-" * 72)

    registry = RegistryLoader(vault_path)

    projects = registry.load_projects()

    if projects:
        current_division = None

        for p in projects:

            if p["division"] != current_division:
                current_division = p["division"]
                lines.append("")
                lines.append(f"[{current_division}]")

            lines.append(
                f"- {p['project']}"
            )
            lines.append(
                f"  status: {p['status']}"
            )
            lines.append(
                f"  owner: {p['owner']}"
            )
            lines.append(
                f"  next: {p['next_gate']}"
            )

    else:
        lines.append("(no registry projects)")


    # ==========================
    # Runtime Layer
    # ==========================

    lines.append("")
    lines.append("RUNTIME STATE")
    lines.append("-" * 72)

    rows = []

    state_paths = []

    for division in (
        "01_RESEARCH",
        "02_BUSINESS",
        "03_PERSONAL_GROWTH",
    ):
        state_paths.extend(
            vault_path.glob(
                f"{division}/*/STATE.md"
            )
        )

    for p in sorted(state_paths):

        s = vault.read_state(p)

        rows.append(
            f"{s.project_id:20} | "
            f"{s.phase:6} | "
            f"{s.state.value:16} | "
            f"owner={s.owner:15} | "
            f"verdict={s.latest_audit_verdict or '-'}"
        )


    if rows:
        lines.extend(rows)

    else:
        lines.append("(no runtime projects)")


    # ==========================
    # Consistency Check
    # ==========================

    lines.append("")
    lines.append("CONSISTENCY CHECK")
    lines.append("-" * 72)

    missing = check_registry_runtime(vault_path)

    if missing:

        lines.append(
            f"Missing runtime states: {len(missing)}"
        )

        for item in missing:
            lines.append(
                f"- {item['project']}"
            )
            lines.append(
                f"  expected: {item['expected']}"
            )

    else:
        lines.append("Registry and runtime aligned.")

    # ==========================
    # Root Inbox
    # ==========================

    inbox = sorted(
        (vault_path / "00_ROOT" / "inbox").glob("*.md")
    )

    lines.append("")
    lines.append(
        f"Root inbox items: {len(inbox)}"
    )

    for p in inbox:
        lines.append(
            f"  - {p.name}"
        )


    return "\n".join(lines)
