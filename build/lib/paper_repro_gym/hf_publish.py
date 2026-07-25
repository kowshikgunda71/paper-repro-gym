"""Optional Hugging Face publishing for a reproduction bundle.

A reproduction's evidence (claim/result matrix, manifest, provenance, logs) is
DATA, so it publishes naturally as a Hugging Face **dataset** repo with a
dataset card. When the paper has an arXiv id, the card tags it so the dataset
links from the paper's page in the HF papers ecosystem.

This is the ONLY part of the gym that needs a third-party dependency
(`huggingface_hub`), imported lazily here so the core stays standard-library
only. It uploads the same evidence-only, secret-scanned directory that
`gym publish` produces — never the paper's artifacts.
"""

from __future__ import annotations

import re
from pathlib import Path

_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})")


def arxiv_id_from(paper_id: str, paper_url: str) -> str | None:
    """Only claim an arXiv id when the source is actually arXiv — otherwise a
    DOI like 10.1080/00031305.1973... would false-match the YYMM.NNNNN shape."""
    for s in (paper_id or "", paper_url or ""):
        if "arxiv" not in s.lower():
            continue
        m = _ARXIV_RE.search(s)
        if m:
            return m.group(1)
    return None


def dataset_card_frontmatter(citation: dict, verdict: str, arxiv_id: str | None) -> str:
    """YAML front matter that makes the README a valid HF dataset card."""
    tags = ["reproducibility", "paper-reproduction", "paper-repro-gym"]
    if verdict:
        tags.append(f"verdict-{verdict.lower()}")
    lines = ["---", "license: mit", "tags:"]
    lines += [f"  - {t}" for t in tags]
    if arxiv_id:
        lines.append(f"arxiv: {arxiv_id}")
    title = citation.get("title") or citation.get("paper_id") or "paper"
    lines.append(f'pretty_name: "Reproduction of {title}"')
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def add_card_frontmatter(readme_path: Path, frontmatter: str) -> None:
    """Prepend HF front matter to an existing README (idempotent)."""
    text = readme_path.read_text(encoding="utf-8")
    if text.lstrip().startswith("---"):
        return  # already carded
    readme_path.write_text(frontmatter + text, encoding="utf-8")


def publish_dataset(folder: Path, repo_id: str, *, private: bool = False,
                    token: str | None = None) -> str:
    """Create (if needed) and upload to hf://datasets/<repo_id>. Lazily imports
    huggingface_hub; raises a clear error if it is not installed / not logged in.
    Returns the repo URL."""
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is not installed. Install it (userspace, no sudo):\n"
            "  pip install --user huggingface_hub\n"
            "then log in once: huggingface-cli login") from exc

    api = HfApi(token=token)
    # Fail early with a helpful message if there is no auth.
    try:
        api.whoami()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Not authenticated to Hugging Face. Run `huggingface-cli login` "
            "(paste your write token at the prompt — never in chat).") from exc

    url = api.create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=True)
    api.upload_folder(folder_path=str(folder), repo_id=repo_id, repo_type="dataset",
                      commit_message="Reproduction evidence (paper-repro-gym)")
    return str(url)
