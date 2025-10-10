"""
Test suite for dependency compatibility verification (TDD approach).

This test file ensures that all required packages can be installed together
without version conflicts, particularly focusing on the pydantic dependency.
"""

import subprocess
import sys
import pytest
from packaging import version
from typing import List, Tuple


class TestDependencyCompatibility:
    """Test dependency compatibility and version constraints."""

    def test_pydantic_version_satisfies_ag2_requirement(self):
        """
        Test that pydantic version satisfies ag2's requirement (>=2.6.1).

        RED Phase: This test should FAIL with pydantic==2.5.3
        GREEN Phase: This test should PASS with pydantic>=2.6.1,<3.0
        """
        try:
            import pydantic
            pydantic_version = version.parse(pydantic.__version__)

            # ag2==0.9.10 requires pydantic>=2.6.1
            min_required_version = version.parse("2.6.1")

            assert pydantic_version >= min_required_version, (
                f"pydantic version {pydantic_version} does not satisfy "
                f"ag2's requirement of >=2.6.1"
            )
        except ImportError:
            pytest.fail("pydantic is not installed")

    def test_pydantic_version_is_v2(self):
        """
        Test that pydantic is version 2.x (not version 1.x or 3.x).

        Ensures we're using Pydantic v2 API which is required by modern packages.
        """
        try:
            import pydantic
            pydantic_version = version.parse(pydantic.__version__)

            assert pydantic_version.major == 2, (
                f"pydantic major version should be 2, got {pydantic_version.major}"
            )
        except ImportError:
            pytest.fail("pydantic is not installed")

    def test_all_required_packages_importable(self):
        """
        Test that all required packages can be imported without errors.

        This ensures that the dependency resolution didn't break any imports.
        """
        required_packages = [
            "autogen",  # ag2 package imports as 'autogen'
            "fastapi",
            "uvicorn",
            "websockets",
            "jinja2",
            "meilisearch",
            "openai",
            "requests",
            "slowapi",
            "prometheus_client",
            "pydantic",
        ]

        failed_imports = []
        for package_name in required_packages:
            try:
                __import__(package_name)
            except ImportError as e:
                failed_imports.append((package_name, str(e)))

        assert not failed_imports, (
            f"Failed to import packages: {failed_imports}"
        )

    def test_pip_check_no_conflicts(self):
        """
        Test that pip check reports no dependency conflicts.

        This runs 'pip check' to verify the environment is consistent.
        """
        result = subprocess.run(
            [sys.executable, "-m", "pip", "check"],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0, (
            f"pip check found dependency conflicts:\n{result.stdout}\n{result.stderr}"
        )

    def test_pydantic_core_functionality(self):
        """
        Test that pydantic's core functionality works correctly.

        This ensures the pydantic version is not just installed, but functional.
        """
        from pydantic import BaseModel, Field

        class TestModel(BaseModel):
            name: str
            value: int = Field(gt=0)

        # Should create instance successfully
        model = TestModel(name="test", value=42)
        assert model.name == "test"
        assert model.value == 42

        # Should validate correctly
        with pytest.raises(Exception):  # Should raise ValidationError
            TestModel(name="test", value=-1)

    def test_ag2_imports_successfully(self):
        """
        Test that ag2 can be imported and its core components are accessible.

        This verifies that ag2 works with the pydantic version.
        Note: ag2 package imports as 'autogen', not 'ag2'.
        """
        try:
            import autogen  # ag2 package imports as 'autogen'
            # Verify autogen has expected attributes
            assert hasattr(autogen, "__version__"), "autogen should have __version__ attribute"
        except ImportError as e:
            pytest.fail(f"Failed to import autogen (ag2): {e}")

    def test_fastapi_with_pydantic_integration(self):
        """
        Test that FastAPI works correctly with the installed pydantic version.

        FastAPI heavily depends on pydantic for data validation.
        """
        from fastapi import FastAPI
        from pydantic import BaseModel

        app = FastAPI()

        class Item(BaseModel):
            name: str
            price: float

        @app.post("/items/")
        async def create_item(item: Item):
            return item

        # Verify the route was created successfully
        assert len(app.routes) > 0, "FastAPI should have routes"


class TestVersionConstraints:
    """Test that version constraints are properly specified."""

    def test_requirements_file_has_pydantic_constraint(self):
        """
        Test that requirements.txt specifies pydantic with proper constraints.

        This ensures we don't accidentally pin to an incompatible version.
        """
        requirements_path = "/workspaces/duck-e/ducke/requirements.txt"

        with open(requirements_path, "r") as f:
            requirements = f.read()

        # Check that pydantic is specified
        assert "pydantic" in requirements.lower(), (
            "pydantic should be specified in requirements.txt"
        )

        # Parse pydantic requirement
        pydantic_line = None
        for line in requirements.split("\n"):
            if line.strip().lower().startswith("pydantic"):
                pydantic_line = line.strip()
                break

        assert pydantic_line is not None, "Could not find pydantic in requirements.txt"

        # Should not be pinned to exact version 2.5.3
        assert "2.5.3" not in pydantic_line, (
            f"pydantic should not be pinned to 2.5.3, found: {pydantic_line}"
        )

        # Should allow versions >= 2.6.1
        assert ">=" in pydantic_line or "~=" in pydantic_line, (
            f"pydantic should use flexible versioning (>= or ~=), found: {pydantic_line}"
        )


class TestRegressionPrevention:
    """Test to prevent regression of the dependency conflict."""

    def test_pydantic_satisfies_all_dependencies(self):
        """
        Test that the pydantic version satisfies all package requirements.

        This is a comprehensive check to prevent future conflicts.
        """
        # Known packages that depend on pydantic
        pydantic_dependent_packages = [
            ("ag2", "2.6.1"),  # ag2 requires pydantic>=2.6.1
            ("fastapi", "2.0.0"),  # fastapi requires pydantic>=2.0.0
        ]

        import pydantic
        pydantic_version = version.parse(pydantic.__version__)

        for package_name, min_version in pydantic_dependent_packages:
            min_required = version.parse(min_version)
            assert pydantic_version >= min_required, (
                f"pydantic {pydantic_version} does not satisfy "
                f"{package_name}'s minimum requirement of {min_version}"
            )
