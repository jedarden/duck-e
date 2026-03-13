"""
Tests for Agentation annotation library integration in chat.html.

Verifies the ESM-based integration works correctly:
- importmap has required React entries
- agentation-root mount point exists
- module script uses correct URL and props
- CSP allows loading from esm.sh
"""
import json
import re
import pytest
from pathlib import Path


CHAT_HTML = Path(__file__).parent.parent / "app" / "website_files" / "templates" / "chat.html"


@pytest.fixture(scope="module")
def chat_html_content():
    return CHAT_HTML.read_text()


class TestImportMap:
    def test_importmap_present(self, chat_html_content):
        assert 'type="importmap"' in chat_html_content

    def test_importmap_has_react(self, chat_html_content):
        # Extract importmap JSON
        match = re.search(r'<script type="importmap">(.*?)</script>', chat_html_content, re.DOTALL)
        assert match, "importmap script not found"
        importmap = json.loads(match.group(1))
        imports = importmap["imports"]
        assert "react" in imports, "react missing from importmap"
        assert "esm.sh/react" in imports["react"]

    def test_importmap_has_react_jsx_runtime(self, chat_html_content):
        match = re.search(r'<script type="importmap">(.*?)</script>', chat_html_content, re.DOTALL)
        importmap = json.loads(match.group(1))
        imports = importmap["imports"]
        assert "react/jsx-runtime" in imports, "react/jsx-runtime missing from importmap"
        assert "esm.sh" in imports["react/jsx-runtime"]

    def test_importmap_has_react_dom(self, chat_html_content):
        match = re.search(r'<script type="importmap">(.*?)</script>', chat_html_content, re.DOTALL)
        importmap = json.loads(match.group(1))
        imports = importmap["imports"]
        assert "react-dom" in imports, "react-dom missing from importmap"
        assert "react-dom/client" in imports, "react-dom/client missing from importmap"

    def test_importmap_before_module_scripts(self, chat_html_content):
        importmap_pos = chat_html_content.find('type="importmap"')
        # Find the first actual <script type="module"> tag (not inside a comment)
        module_match = re.search(r'<script[^>]+type="module"', chat_html_content)
        assert module_match, "No module script found"
        module_pos = module_match.start()
        assert importmap_pos < module_pos, "importmap must appear before module scripts"

    def test_react_versions_consistent(self, chat_html_content):
        match = re.search(r'<script type="importmap">(.*?)</script>', chat_html_content, re.DOTALL)
        importmap = json.loads(match.group(1))
        imports = importmap["imports"]
        # All react entries should use the same version
        react_url = imports.get("react", "")
        jsx_url = imports.get("react/jsx-runtime", "")
        react_dom_url = imports.get("react-dom", "")
        version_match = re.search(r'react@(\d+)', react_url)
        assert version_match, "react URL has no version"
        version = version_match.group(1)
        assert f"react@{version}" in jsx_url, "react/jsx-runtime version mismatch"
        assert f"react-dom@{version}" in react_dom_url, "react-dom version mismatch"


class TestAgentationMountPoint:
    def test_agentation_root_div_exists(self, chat_html_content):
        assert 'id="agentation-root"' in chat_html_content

    def test_agentation_root_outside_main_content(self, chat_html_content):
        main_end = chat_html_content.find("</main>")
        root_pos = chat_html_content.find('id="agentation-root"')
        footer_pos = chat_html_content.find("<footer")
        assert main_end < root_pos < footer_pos, (
            "agentation-root should be between </main> and <footer>"
        )


class TestAgentationModuleScript:
    def _get_agentation_script(self, html):
        # Find the module script that imports Agentation
        match = re.search(
            r'<script type="module">(.*?)</script>',
            html,
            re.DOTALL
        )
        assert match, "agentation module script not found"
        return match.group(1)

    def test_imports_react_bare_specifier(self, chat_html_content):
        script = self._get_agentation_script(chat_html_content)
        assert "from 'react'" in script or 'from "react"' in script

    def test_imports_create_root(self, chat_html_content):
        script = self._get_agentation_script(chat_html_content)
        assert "createRoot" in script

    def test_imports_agentation_from_esm_sh(self, chat_html_content):
        script = self._get_agentation_script(chat_html_content)
        assert "esm.sh/agentation" in script

    def test_agentation_external_react(self, chat_html_content):
        script = self._get_agentation_script(chat_html_content)
        # Ensure react is marked external to avoid dual-instance problem
        assert "external=react" in script

    def test_correct_export_name(self, chat_html_content):
        script = self._get_agentation_script(chat_html_content)
        assert "{ Agentation }" in script or "Agentation }" in script

    def test_on_annotation_add_prop(self, chat_html_content):
        script = self._get_agentation_script(chat_html_content)
        assert "onAnnotationAdd" in script

    def test_on_submit_prop(self, chat_html_content):
        script = self._get_agentation_script(chat_html_content)
        assert "onSubmit" in script

    def test_uses_react_create_element(self, chat_html_content):
        script = self._get_agentation_script(chat_html_content)
        assert "React.createElement" in script or "createElement" in script


class TestCSPAllowsEsmSh:
    def test_security_headers_allow_esm_sh(self):
        from app.middleware.security_headers import SecurityHeadersMiddleware
        from starlette.applications import Starlette
        middleware = SecurityHeadersMiddleware(Starlette())
        csp = middleware._build_csp_header()
        assert "https://esm.sh" in csp, (
            "CSP must allow https://esm.sh for agentation and React ESM modules"
        )

    def test_csp_script_src_includes_esm_sh(self):
        from app.middleware.security_headers import SecurityHeadersMiddleware
        from starlette.applications import Starlette
        middleware = SecurityHeadersMiddleware(Starlette())
        csp = middleware._build_csp_header()
        # Find script-src directive
        directives = {d.split()[0]: d for d in csp.split(";")}
        script_src = directives.get("script-src", "")
        assert "https://esm.sh" in script_src, (
            "script-src must include https://esm.sh"
        )
