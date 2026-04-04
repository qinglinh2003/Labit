from __future__ import annotations

from pathlib import Path
import re

from labit.agents.models import CodeSnapshot
from labit.paths import RepoPaths


class CodeMapBuilder:
    def __init__(self, paths: RepoPaths):
        self.paths = paths

    def build_snapshot(self, project: str) -> CodeSnapshot | None:
        code_dir = self.paths.vault_projects_dir / project / "code"
        if not code_dir.exists():
            return None

        readme_excerpt = ""
        for candidate in ("README.md", "readme.md"):
            path = code_dir / candidate
            if path.exists():
                readme_excerpt = self._read_excerpt(path)
                break

        package_roots = sorted(
            str(path.relative_to(self.paths.root))
            for path in code_dir.iterdir()
            if path.is_dir() and not path.name.startswith(".") and (path / "__init__.py").exists()
        )[:5]
        entrypoints = (
            sorted(str(path.relative_to(self.paths.root)) for path in (code_dir / "scripts").rglob("*.py"))[:8]
            if (code_dir / "scripts").exists()
            else []
        )
        config_files = (
            sorted(str(path.relative_to(self.paths.root)) for path in (code_dir / "configs").rglob("*.yaml"))[:8]
            if (code_dir / "configs").exists()
            else []
        )

        notes: list[str] = []
        if (code_dir / "pyproject.toml").exists():
            notes.append(str((code_dir / "pyproject.toml").relative_to(self.paths.root)))
        if (code_dir / "README.md").exists():
            notes.append(str((code_dir / "README.md").relative_to(self.paths.root)))

        return CodeSnapshot(
            project_code_dir=str(code_dir.relative_to(self.paths.root)),
            readme_excerpt=readme_excerpt,
            package_roots=package_roots,
            entrypoints=entrypoints,
            config_files=config_files,
            notes=notes[:8],
        )

    def render_snapshot(self, snapshot: CodeSnapshot, *, readme_chars: int = 1200) -> str:
        return self.render_snapshot_with_relevant(snapshot, relevant_paths=[], readme_chars=readme_chars)

    def render_snapshot_with_relevant(
        self,
        snapshot: CodeSnapshot,
        *,
        relevant_paths: list[str],
        readme_chars: int = 1200,
    ) -> str:
        lines = [f"Code dir: {snapshot.project_code_dir}"]
        if relevant_paths:
            lines.append("Likely relevant files:")
            lines.extend(f"- {item}" for item in relevant_paths[:8])
        if snapshot.package_roots:
            lines.append("Package roots:")
            lines.extend(f"- {item}" for item in snapshot.package_roots)
        if snapshot.entrypoints:
            lines.append("Entrypoints:")
            lines.extend(f"- {item}" for item in snapshot.entrypoints[:6])
        if snapshot.config_files:
            lines.append("Config files:")
            lines.extend(f"- {item}" for item in snapshot.config_files[:6])
        if snapshot.notes:
            lines.append("Project files:")
            lines.extend(f"- {item}" for item in snapshot.notes[:6])
        excerpt = snapshot.readme_excerpt.strip()
        if excerpt:
            lines.append("README excerpt:")
            lines.append(excerpt[:readme_chars].rstrip())
        return "\n".join(lines)

    def build_relevant_paths(self, project: str, *, query: str, max_items: int = 6) -> list[str]:
        snapshot = self.build_snapshot(project)
        if snapshot is None:
            return []

        query_tokens = self._tokenize(query)
        candidates = self._candidate_paths(snapshot)
        ranked: list[tuple[int, str]] = []

        for candidate in candidates:
            score = 0
            candidate_tokens = self._tokenize(candidate)
            basename_tokens = self._tokenize(Path(candidate).name)
            path_overlap = len(query_tokens & candidate_tokens)
            name_overlap = len(query_tokens & basename_tokens)
            score += path_overlap * 4
            score += name_overlap * 2
            if query_tokens and path_overlap == 0 and name_overlap == 0:
                continue
            if "/scripts/" in candidate:
                score += 1
            if "/configs/" in candidate or candidate.endswith(".yaml"):
                score += 1
            ranked.append((score, candidate))

        if not ranked:
            fallback = snapshot.entrypoints[:3] + snapshot.config_files[:2] + snapshot.notes[:2]
            deduped: list[str] = []
            for item in fallback:
                if item and item not in deduped:
                    deduped.append(item)
            return deduped[:max_items]

        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected: list[str] = []
        for _, candidate in ranked:
            if candidate not in selected:
                selected.append(candidate)
            if len(selected) >= max_items:
                break
        return selected

    def _read_excerpt(self, path: Path, *, max_chars: int = 2400) -> str:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        return text[:max_chars].strip()

    def _candidate_paths(self, snapshot: CodeSnapshot) -> list[str]:
        code_dir = self.paths.root / snapshot.project_code_dir
        candidates: list[str] = []
        candidates.extend(snapshot.entrypoints)
        candidates.extend(snapshot.config_files)
        candidates.extend(snapshot.notes)
        candidates.extend(snapshot.package_roots)

        seen = set(candidates)
        for package_root in snapshot.package_roots:
            package_path = self.paths.root / package_root
            if not package_path.exists():
                continue
            for path in list(package_path.rglob("*.py"))[:80]:
                relative = str(path.relative_to(self.paths.root))
                if relative in seen:
                    continue
                candidates.append(relative)
                seen.add(relative)

        if not candidates and code_dir.exists():
            for path in list(code_dir.rglob("*.py"))[:40]:
                relative = str(path.relative_to(self.paths.root))
                if relative in seen:
                    continue
                candidates.append(relative)
                seen.add(relative)
        return candidates

    def _tokenize(self, text: str) -> set[str]:
        return {token for token in re.findall(r"[a-zA-Z0-9]+", text.lower()) if len(token) >= 3}
