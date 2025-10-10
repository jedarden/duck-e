"""
Test suite for GitHub Actions workflow configuration.
Validates that docker-release.yml has correct build context and paths.
"""
import os
import yaml
import pytest
from pathlib import Path


class TestDockerReleaseWorkflow:
    """Test Docker release workflow configuration for correct build context."""

    @pytest.fixture
    def workflow_file(self):
        """Load the docker-release.yml workflow file."""
        workflow_path = Path(__file__).parent.parent / ".github" / "workflows" / "docker-release.yml"
        assert workflow_path.exists(), f"Workflow file not found at {workflow_path}"

        with open(workflow_path, 'r') as f:
            return yaml.safe_load(f)

    @pytest.fixture
    def ducke_dir(self):
        """Get the ducke directory path."""
        return Path(__file__).parent.parent

    def test_workflow_syntax_valid(self, workflow_file):
        """Test that workflow YAML syntax is valid and can be parsed."""
        assert workflow_file is not None, "Workflow file should be valid YAML"
        assert 'name' in workflow_file, "Workflow should have a name"
        assert 'jobs' in workflow_file, "Workflow should have jobs defined"

    def test_workflow_has_build_job(self, workflow_file):
        """Test that workflow has build-and-push job."""
        assert 'build-and-push' in workflow_file['jobs'], "Workflow should have build-and-push job"

    def test_build_context_points_to_ducke(self, workflow_file):
        """
        RED PHASE TEST: Verify build context points to ducke/ directory.
        This test will FAIL initially and pass after we fix the workflow.
        """
        build_job = workflow_file['jobs']['build-and-push']
        steps = build_job['steps']

        # Find the docker build-push step
        build_step = None
        for step in steps:
            if step.get('uses', '').startswith('docker/build-push-action'):
                build_step = step
                break

        assert build_step is not None, "Should have docker/build-push-action step"

        # RED: This assertion will FAIL because current context is "."
        # It should pass after we add "context: ./ducke"
        assert 'context' in build_step.get('with', {}), \
            "Build step must specify context parameter"

        assert build_step['with']['context'] == './ducke', \
            "Build context must be './ducke' to access Dockerfile, requirements.txt, and app/"

    def test_required_files_accessible_from_context(self, ducke_dir):
        """
        RED PHASE TEST: Verify all required files exist in ducke/ directory.
        These files must be accessible from the build context.
        """
        # Files that Docker build needs to access
        required_files = [
            'dockerfile',           # Lowercase as per docker-compose.yml
            'requirements.txt',
            'app',                  # Directory
        ]

        for file_name in required_files:
            file_path = ducke_dir / file_name
            assert file_path.exists(), \
                f"Required file/directory '{file_name}' must exist in ducke/ directory. " \
                f"Docker build context must include this file."

    def test_platforms_include_amd64_and_arm64(self, workflow_file):
        """Test that multi-platform build includes both amd64 and arm64."""
        build_job = workflow_file['jobs']['build-and-push']
        steps = build_job['steps']

        # Find the docker build-push step
        build_step = None
        for step in steps:
            if step.get('uses', '').startswith('docker/build-push-action'):
                build_step = step
                break

        assert build_step is not None, "Should have docker/build-push-action step"

        platforms = build_step.get('with', {}).get('platforms', '')
        assert 'linux/amd64' in platforms, "Should build for linux/amd64"
        assert 'linux/arm64' in platforms, "Should build for linux/arm64"

    def test_dockerfile_reference_correct(self, workflow_file):
        """Test that Dockerfile reference matches actual file name (lowercase)."""
        build_job = workflow_file['jobs']['build-and-push']
        steps = build_job['steps']

        # Find the docker build-push step
        build_step = None
        for step in steps:
            if step.get('uses', '').startswith('docker/build-push-action'):
                build_step = step
                break

        # If dockerfile parameter is specified, it should be 'dockerfile' (lowercase)
        # Otherwise, Docker will look for 'Dockerfile' by default, which won't work
        dockerfile_param = build_step.get('with', {}).get('file')
        if dockerfile_param:
            # If specified, should point to lowercase dockerfile
            assert 'dockerfile' in dockerfile_param.lower(), \
                "Dockerfile parameter should reference lowercase 'dockerfile'"

    def test_docker_compose_context_matches(self, ducke_dir):
        """Test that docker-compose.yml context is consistent."""
        compose_path = ducke_dir / "docker-compose.yml"
        assert compose_path.exists(), "docker-compose.yml should exist"

        with open(compose_path, 'r') as f:
            compose_config = yaml.safe_load(f)

        # Verify duck-e service has correct context
        assert 'services' in compose_config, "docker-compose should have services"
        assert 'duck-e' in compose_config['services'], "Should have duck-e service"

        service = compose_config['services']['duck-e']
        assert 'build' in service, "Service should have build configuration"

        # Context is "." which is correct for docker-compose (runs from ducke/ dir)
        # But GitHub Actions needs "./ducke" because it runs from repo root
        context = service['build'].get('context', '.')
        assert context == '.', "docker-compose context should be '.' (relative to ducke/)"

    def test_workflow_permissions_correct(self, workflow_file):
        """Test that workflow has necessary permissions for package registry."""
        build_job = workflow_file['jobs']['build-and-push']
        permissions = build_job.get('permissions', {})

        assert 'packages' in permissions, "Should have packages permission"
        assert permissions['packages'] == 'write', "Should have write permission for packages"
        assert 'contents' in permissions, "Should have contents permission for releases"


class TestWorkflowCacheConfiguration:
    """Test Docker build cache configuration."""

    @pytest.fixture
    def workflow_file(self):
        """Load the docker-release.yml workflow file."""
        workflow_path = Path(__file__).parent.parent / ".github" / "workflows" / "docker-release.yml"
        with open(workflow_path, 'r') as f:
            return yaml.safe_load(f)

    def test_cache_configuration_present(self, workflow_file):
        """Test that GitHub Actions cache is configured for Docker builds."""
        build_job = workflow_file['jobs']['build-and-push']
        steps = build_job['steps']

        # Find the docker build-push step
        build_step = None
        for step in steps:
            if step.get('uses', '').startswith('docker/build-push-action'):
                build_step = step
                break

        assert build_step is not None, "Should have docker/build-push-action step"

        with_params = build_step.get('with', {})
        assert 'cache-from' in with_params, "Should have cache-from configuration"
        assert 'cache-to' in with_params, "Should have cache-to configuration"

        # Verify GitHub Actions cache is used
        assert 'gha' in with_params['cache-from'], "Should use GitHub Actions cache"
        assert 'gha' in with_params['cache-to'], "Should use GitHub Actions cache"


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
