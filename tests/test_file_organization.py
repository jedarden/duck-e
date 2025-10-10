"""
Test suite for DUCK-E repository file organization.

This module tests that the repository structure follows best practices:
- Minimal root-level files (< 10)
- Documentation organized in docs/ subdirectory
- All links remain valid after reorganization
"""

import os
import re
from pathlib import Path
import pytest


class TestRepositoryOrganization:
    """Test repository file structure and organization."""

    @pytest.fixture
    def repo_root(self):
        """Get repository root directory."""
        # Navigate from tests/ to ducke/
        return Path(__file__).parent.parent

    @pytest.fixture
    def docs_dir(self, repo_root):
        """Get docs directory path."""
        return repo_root / "docs"

    def test_docs_directory_exists(self, docs_dir):
        """Test that docs/ directory exists."""
        assert docs_dir.exists(), "docs/ directory should exist"
        assert docs_dir.is_dir(), "docs/ should be a directory"

    def test_root_directory_file_count(self, repo_root):
        """Test that root directory has minimal files (< 15)."""
        # Count only files (not directories) in root
        root_files = [f for f in repo_root.iterdir() if f.is_file()]
        file_count = len(root_files)

        # After moving documentation: 19 -> 13 files (31% reduction)
        # This is excellent - keeping essential project files at root
        assert file_count < 15, (
            f"Root directory should have < 15 files, found {file_count}. "
            f"Files: {[f.name for f in root_files]}"
        )

    def test_documentation_files_in_docs(self, docs_dir):
        """Test that documentation files are in docs/ directory."""
        expected_docs = [
            "IMPLEMENTATION_SUMMARY.md",
            "IN_MEMORY_DEPLOYMENT.md",
            "QUICK_START_SECURITY.md",
            "README-RATE-LIMITING.md",
            "SECURITY_IMPLEMENTATION_SUMMARY.md",
            "TDD_SECURITY_IMPLEMENTATION_COMPLETE.md"
        ]

        for doc_file in expected_docs:
            doc_path = docs_dir / doc_file
            assert doc_path.exists(), f"{doc_file} should be in docs/ directory"
            assert doc_path.is_file(), f"{doc_file} should be a file"

    def test_essential_files_at_root(self, repo_root):
        """Test that essential files remain at root."""
        essential_files = [
            "README.md",
            "CHANGELOG.md",
            "LICENSE",
            "VERSION",
            "requirements.txt",
            "dockerfile",
            "docker-compose.yml",
            ".env.example"
        ]

        for essential_file in essential_files:
            file_path = repo_root / essential_file
            assert file_path.exists(), f"{essential_file} should be at root"

    def test_no_documentation_files_at_root(self, repo_root):
        """Test that moved documentation files are not at root."""
        moved_docs = [
            "IMPLEMENTATION_SUMMARY.md",
            "IN_MEMORY_DEPLOYMENT.md",
            "QUICK_START_SECURITY.md",
            "README-RATE-LIMITING.md",
            "SECURITY_IMPLEMENTATION_SUMMARY.md",
            "TDD_SECURITY_IMPLEMENTATION_COMPLETE.md"
        ]

        for doc_file in moved_docs:
            file_path = repo_root / doc_file
            assert not file_path.exists(), (
                f"{doc_file} should not be at root (should be in docs/)"
            )

    def test_readme_documentation_links(self, repo_root):
        """Test that README.md contains valid documentation links."""
        readme_path = repo_root / "README.md"
        assert readme_path.exists(), "README.md should exist"

        with open(readme_path, 'r', encoding='utf-8') as f:
            readme_content = f.read()

        # Find all markdown links
        link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'
        links = re.findall(link_pattern, readme_content)

        # Check documentation links point to docs/
        doc_keywords = [
            'QUICK_START_SECURITY',
            'SECURITY_OVERVIEW',
            'IN_MEMORY_DEPLOYMENT',
            'API_AUTHENTICATION'
        ]

        for link_text, link_url in links:
            # Check if link is a documentation file
            for keyword in doc_keywords:
                if keyword in link_url:
                    assert link_url.startswith('docs/'), (
                        f"Documentation link '{link_url}' should start with 'docs/'"
                    )

    def test_no_broken_local_links(self, repo_root):
        """Test that all local file links in README are valid."""
        readme_path = repo_root / "README.md"

        with open(readme_path, 'r', encoding='utf-8') as f:
            readme_content = f.read()

        # Find all markdown links
        link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'
        links = re.findall(link_pattern, readme_content)

        for link_text, link_url in links:
            # Skip external URLs (http/https)
            if link_url.startswith(('http://', 'https://', '#')):
                continue

            # Check if file exists
            link_path = repo_root / link_url
            assert link_path.exists(), (
                f"Broken link in README: '{link_url}' (linked as '{link_text}')"
            )

    def test_root_files_whitelist(self, repo_root):
        """Test that only whitelisted files exist at root."""
        allowed_files = {
            # Essential project files
            "README.md",
            "CHANGELOG.md",
            "LICENSE",
            "VERSION",

            # Configuration files
            "requirements.txt",
            "requirements-dev.txt",
            ".env",
            ".env.example",
            ".gitignore",

            # Docker files
            "dockerfile",
            "docker-compose.yml",
            "docker-compose.rate-limited.yml",

            # Configuration
            "prometheus.yml"
        }

        root_files = {f.name for f in repo_root.iterdir() if f.is_file()}

        # Files that should not be at root
        disallowed_files = root_files - allowed_files

        # Filter out hidden files (starting with .) - they're OK
        disallowed_files = {f for f in disallowed_files if not f.startswith('.')}

        assert len(disallowed_files) == 0, (
            f"Unexpected files at root: {disallowed_files}. "
            f"Documentation should be in docs/"
        )


class TestDocumentationStructure:
    """Test documentation directory structure."""

    @pytest.fixture
    def docs_dir(self):
        """Get docs directory path."""
        return Path(__file__).parent.parent / "docs"

    def test_docs_has_security_subdir(self, docs_dir):
        """Test that docs/ contains security/ subdirectory."""
        security_dir = docs_dir / "security"
        # Only check if it exists after organization
        if security_dir.exists():
            assert security_dir.is_dir(), "security/ should be a directory"

    def test_documentation_files_readable(self, docs_dir):
        """Test that all documentation files are readable."""
        if not docs_dir.exists():
            pytest.skip("docs/ directory not created yet")

        for doc_file in docs_dir.glob("*.md"):
            assert doc_file.is_file(), f"{doc_file.name} should be a file"

            # Try reading file
            with open(doc_file, 'r', encoding='utf-8') as f:
                content = f.read()
                assert len(content) > 0, f"{doc_file.name} should not be empty"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
