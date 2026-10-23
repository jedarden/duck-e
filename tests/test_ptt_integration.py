"""
Tests for Push-to-Talk (PTT) feature integration.

Verifies the PTT controls, state management, localStorage persistence,
keyboard shortcuts, and voice change interaction work correctly.
"""
import json
import re
import pytest
from pathlib import Path


CHAT_HTML = Path(__file__).parent.parent / "app" / "website_files" / "templates" / "chat.html"
MAIN_JS = Path(__file__).parent.parent / "app" / "website_files" / "static" / "main.js"


@pytest.fixture(scope="module")
def chat_html_content():
    return CHAT_HTML.read_text()


@pytest.fixture(scope="module")
def main_js_content():
    return MAIN_JS.read_text()


class TestPTTHTMLStructure:
    """Tests for PTT UI elements in chat.html."""

    def test_ptt_controls_container_exists(self, chat_html_content):
        assert 'id="ptt-controls"' in chat_html_content, "PTT controls container must exist"
        assert 'class="ptt-controls"' in chat_html_content, "PTT controls class must exist"

    def test_ptt_toggle_exists(self, chat_html_content):
        assert 'id="ptt-toggle"' in chat_html_content, "PTT toggle checkbox must exist"
        assert 'class="ptt-toggle"' in chat_html_content, "PTT toggle class must exist"

    def test_ptt_button_exists(self, chat_html_content):
        # Check for the button element with proper attributes
        assert '<button id="ptt-btn"' in chat_html_content, "PTT button must exist"
        assert 'ptt-btn' in chat_html_content, "PTT button class must exist"

    def test_ptt_button_has_mic_icon(self, chat_html_content):
        assert '🎤' in chat_html_content, "PTT button must have microphone icon"

    def test_ptt_button_has_hold_text(self, chat_html_content):
        assert 'Hold to Talk' in chat_html_content, "PTT button must have 'Hold to Talk' label"

    def test_ptt_hint_exists(self, chat_html_content):
        assert 'class="ptt-hint"' in chat_html_content, "PTT hint must exist"
        assert 'Spacebar' in chat_html_content, "PTT hint must mention Spacebar"

    def test_ptt_controls_hidden_by_default(self, chat_html_content):
        # PTT controls should have display: none by default
        assert 'id="ptt-controls" class="ptt-controls" style="display: none;"' in chat_html_content, \
            "PTT controls must be hidden by default"

    def test_inline_ptt_controls_exist(self, chat_html_content):
        assert 'id="ptt-toggle-inline"' in chat_html_content, "Inline PTT toggle must exist"
        assert 'id="ptt-btn-inline"' in chat_html_content, "Inline PTT button must exist"

    def test_ptt_css_styles_defined(self, chat_html_content):
        assert '.ptt-controls' in chat_html_content, "PTT controls CSS must be defined"
        assert '.ptt-toggle' in chat_html_content, "PTT toggle CSS must be defined"
        assert '.ptt-btn' in chat_html_content, "PTT button CSS must be defined"
        assert '.ptt-hint' in chat_html_content, "PTT hint CSS must be defined"


class TestPTTJavaScriptState:
    """Tests for PTT state management in main.js."""

    def test_ptt_state_variable_exists(self, main_js_content):
        assert 'isPushToTalk' in main_js_content, "isPushToTalk state variable must exist"

    def test_ptt_state_initialized(self, main_js_content):
        # Should be initialized to false
        assert 'let isPushToTalk = false' in main_js_content or \
               'let isPushToTalk=false' in main_js_content, \
               "isPushToTalk must be initialized to false"

    def test_ptt_storage_key_defined(self, main_js_content):
        assert 'PTT:' in main_js_content or 'PTT =' in main_js_content, \
               "PTT storage key must be defined in STORAGE_KEYS"
        assert "'duck-e-ptt'" in main_js_content or '"duck-e-ptt"' in main_js_content, \
               "PTT localStorage key must be 'duck-e-ptt'"

    def test_load_ptt_state_function_exists(self, main_js_content):
        assert 'loadPushToTalkState' in main_js_content, \
               "loadPushToTalkState function must exist"

    def test_save_ptt_state_function_exists(self, main_js_content):
        assert 'savePushToTalkState' in main_js_content, \
               "savePushToTalkState function must exist"

    def test_load_ptt_state_reads_localstorage(self, main_js_content):
        # Function should read from localStorage
        func_match = re.search(
            r'const loadPushToTalkState\s*=\s*\(\)\s*=>\s*\{([^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert func_match, "Could not find loadPushToTalkState function"
        func_body = func_match.group(1)
        assert 'localStorage.getItem' in func_body, \
               "loadPushToTalkState must read from localStorage"

    def test_save_ptt_state_writes_localstorage(self, main_js_content):
        # Function should write to localStorage
        func_match = re.search(
            r'const savePushToTalkState\s*=\s*\([^)]*\)\s*=>\s*\{([^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert func_match, "Could not find savePushToTalkState function"
        func_body = func_match.group(1)
        assert 'localStorage.setItem' in func_body, \
               "savePushToTalkState must write to localStorage"


class TestPTTUIUpdates:
    """Tests for PTT UI update functions."""

    def test_update_ptt_ui_function_exists(self, main_js_content):
        assert 'updatePTTUI' in main_js_content, "updatePTTUI function must exist"

    def test_update_ptt_ui_shows_button_when_enabled(self, main_js_content):
        # Should remove 'hidden' class and set display: flex
        func_match = re.search(
            r'const updatePTTUI\s*=\s*\([^)]*\)\s*=>\s*\{([^}]+;[^}]*)\}',
            main_js_content,
            re.DOTALL
        )
        assert func_match, "Could not find updatePTTUI function"
        func_body = func_match.group(1)
        # Check for logic that shows button when enabled
        assert 'enabled' in func_body.lower(), "updatePTTUI must handle enabled state"
        assert 'remove' in func_body.lower() or 'add' in func_body.lower(), \
               "updatePTTUI must manipulate CSS classes"

    def test_update_ptt_ui_hides_button_when_disabled(self, main_js_content):
        func_match = re.search(
            r'const updatePTTUI\s*=\s*\([^)]*\)\s*=>\s*\{([^}]+;[^}]*)\}',
            main_js_content,
            re.DOTALL
        )
        assert func_match, "Could not find updatePTTUI function"
        func_body = func_match.group(1)
        # Should add 'hidden' class or set display: none
        assert 'hidden' in func_body or "'none'" in func_body or '"none"' in func_body, \
               "updatePTTUI must hide button when disabled"

    def test_update_ptt_ui_updates_toggle_checkbox(self, main_js_content):
        func_match = re.search(
            r'const updatePTTUI\s*=\s*\([^)]*\)\s*=>\s*\{([^}]+;[^}]*)\}',
            main_js_content,
            re.DOTALL
        )
        assert func_match, "Could not find updatePTTUI function"
        func_body = func_match.group(1)
        assert '.checked' in func_body, "updatePTTUI must update toggle checkbox state"

    def test_update_ptt_ui_handles_hint_visibility(self, main_js_content):
        func_match = re.search(
            r'const updatePTTUI\s*=\s*\([^)]*\)\s*=>\s*\{([^}]+;[^}]*)\}',
            main_js_content,
            re.DOTALL
        )
        assert func_match, "Could not find updatePTTUI function"
        func_body = func_match.group(1)
        assert 'pttHint' in func_body or 'ptt-hint' in func_body, \
               "updatePTTUI must handle hint visibility"


class TestPTTToggleBehavior:
    """Tests for PTT toggle functionality."""

    def test_toggle_push_to_talk_function_exists(self, main_js_content):
        assert 'togglePushToTalk' in main_js_content, \
               "togglePushToTalk function must exist"

    def test_toggle_push_to_talk_flips_state(self, main_js_content):
        func_match = re.search(
            r'const togglePushToTalk\s*=\s*\(\)\s*=>\s*\{([^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert func_match, "Could not find togglePushToTalk function"
        func_body = func_match.group(1)
        assert 'isPushToTalk = !isPushToTalk' in func_body or \
               'isPushToTalk=!isPushToTalk' in func_body, \
               "togglePushToTalk must flip isPushToTalk state"

    def test_toggle_push_to_talk_saves_state(self, main_js_content):
        func_match = re.search(
            r'const togglePushToTalk\s*=\s*\(\)\s*=>\s*\{([^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert func_match, "Could not find togglePushToTalk function"
        func_body = func_match.group(1)
        assert 'savePushToTalkState' in func_body or 'localStorage.setItem' in func_body, \
               "togglePushToTalk must persist state to localStorage"

    def test_toggle_push_to_talk_updates_ui(self, main_js_content):
        func_match = re.search(
            r'const togglePushToTalk\s*=\s*\(\)\s*=>\s*\{([^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert func_match, "Could not find togglePushToTalk function"
        func_body = func_match.group(1)
        assert 'updatePTTUI' in func_body, "togglePushToTalk must update UI"

    def test_toggle_push_to_talk_disables_mic_when_enabled(self, main_js_content):
        # Check that togglePushToTalk controls microphone when PTT is enabled
        # The function should call setMicrophoneEnabled(false) when entering PTT mode
        assert 'togglePushToTalk' in main_js_content, "togglePushToTalk must exist"
        # Find the section where PTT is enabled and mic is disabled
        ptt_enable_match = re.search(
            r'if\s*\([^)]*isPushToTalk[^)]*\)\s*\{[^}]*setMicrophoneEnabled\s*\(\s*false\s*\)',
            main_js_content,
            re.DOTALL
        )
        assert ptt_enable_match, "togglePushToTalk must disable microphone when PTT is enabled"

    def test_ptt_toggle_event_listener_attached(self, main_js_content):
        # Should attach change event listener to toggle checkbox
        assert 'addEventListener("change"' in main_js_content or \
               "addEventListener('change'" in main_js_content, \
               "PTT toggle must have event listener"
        assert 'togglePushToTalk' in main_js_content, \
               "PTT toggle event must call togglePushToTalk"


class TestPTTHoldToTalk:
    """Tests for PTT hold-to-talk functionality."""

    def test_start_talking_function_exists(self, main_js_content):
        assert 'startTalking' in main_js_content, "startTalking function must exist"

    def test_stop_talking_function_exists(self, main_js_content):
        assert 'stopTalking' in main_js_content, "stopTalking function must exist"

    def test_start_talking_enables_microphone(self, main_js_content):
        func_match = re.search(
            r'const startTalking\s*=\s*\(\)\s*=>\s*\{([^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert func_match, "Could not find startTalking function"
        func_body = func_match.group(1)
        assert 'enabled' in func_body.lower() or 'true' in func_body, \
               "startTalking must enable microphone"

    def test_stop_talking_disables_microphone(self, main_js_content):
        func_match = re.search(
            r'const stopTalking\s*=\s*\(\)\s*=>\s*\{([^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert func_match, "Could not find stopTalking function"
        func_body = func_match.group(1)
        assert 'false' in func_body, "stopTalking must disable microphone"

    def test_start_talking_checks_ptt_mode(self, main_js_content):
        func_match = re.search(
            r'const startTalking\s*=\s*\(\)\s*=>\s*\{([^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert func_match, "Could not find startTalking function"
        func_body = func_match.group(1)
        assert 'isPushToTalk' in func_body, \
               "startTalking must check isPushToTalk state"

    def test_stop_talking_checks_ptt_mode(self, main_js_content):
        func_match = re.search(
            r'const stopTalking\s*=\s*\(\)\s*=>\s*\{([^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert func_match, "Could not find stopTalking function"
        func_body = func_match.group(1)
        assert 'isPushToTalk' in func_body, \
               "stopTalking must check isPushToTalk state"

    def test_ptt_button_has_mouse_events(self, main_js_content):
        # PTT button should have mousedown/mouseup event listeners
        assert 'mousedown' in main_js_content, "PTT button must have mousedown event"
        assert 'mouseup' in main_js_content or 'mouseleave' in main_js_content, \
               "PTT button must have mouseup/mouseleave event"

    def test_ptt_button_has_touch_events(self, main_js_content):
        # PTT button should have touchstart/touchend event listeners
        assert 'touchstart' in main_js_content, "PTT button must have touchstart event"
        assert 'touchend' in main_js_content or 'touchcancel' in main_js_content, \
               "PTT button must have touchend/touchcancel event"


class TestPTTKeyboardShortcuts:
    """Tests for PTT keyboard shortcuts."""

    def test_spacebar_keydown_listener_exists(self, main_js_content):
        assert 'keydown' in main_js_content, "Must have keydown event listener"
        assert '"Space"' in main_js_content or "'Space'" in main_js_content, \
               "Must listen for Space key"

    def test_spacebar_keyup_listener_exists(self, main_js_content):
        assert 'keyup' in main_js_content, "Must have keyup event listener"
        assert '"Space"' in main_js_content or "'Space'" in main_js_content, \
               "Must listen for Space key"

    def test_spacebar_checks_ptt_mode(self, main_js_content):
        # Find the keydown handler
        keydown_match = re.search(
            r'addEventListener\(["\']keydown["\']\s*,\s*\([^)]*\)\s*=>\s*\{([^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert keydown_match, "Could not find keydown event listener"
        handler = keydown_match.group(1)
        # Check if it verifies PTT mode before calling startTalking
        assert 'isPushToTalk' in handler, "Keydown handler must check isPushToTalk"

    def test_spacebar_checks_connection_status(self, main_js_content):
        keydown_match = re.search(
            r'addEventListener\(["\']keydown["\']\s*,\s*\([^)]*\)\s*=>\s*\{([^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert keydown_match, "Could not find keydown event listener"
        handler = keydown_match.group(1)
        assert 'connectionStatus' in handler, "Keydown handler must check connection status"

    def test_spacebar_prevents_default(self, main_js_content):
        keydown_match = re.search(
            r'addEventListener\(["\']keydown["\']\s*,\s*\([^)]*\)\s*=>\s*\{([^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert keydown_match, "Could not find keydown event listener"
        handler = keydown_match.group(1)
        assert 'preventDefault' in handler, "Spacebar handler must prevent default scrolling"

    def test_spacebar_checks_no_repeat(self, main_js_content):
        keydown_match = re.search(
            r'addEventListener\(["\']keydown["\']\s*,\s*\([^)]*\)\s*=>\s*\{([^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert keydown_match, "Could not find keydown event listener"
        handler = keydown_match.group(1)
        assert 'repeat' in handler or '!e.repeat' in handler or "e.repeat" in handler, \
               "Spacebar handler must check for repeat events to avoid auto-repeat"


class TestPTTVoiceChangeInteraction:
    """Tests for PTT behavior during voice changes."""

    def test_voice_changed_message_handler_exists(self, main_js_content):
        assert 'ducke.voice_changed' in main_js_content, \
               "Must handle ducke.voice_changed message"

    def test_voice_changed_adds_transcript_message(self, main_js_content):
        # Find the voice_changed handler
        voice_match = re.search(
            r'else if\s*\([^)]*type[^)]*["\']ducke\.voice_changed["\'][^)]*\)\s*\{([^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert voice_match, "Could not find voice_changed handler"
        handler = voice_match.group(1)
        assert 'addTranscriptMessage' in handler or 'system' in handler, \
               "Voice change must add transcript message"

    def test_connection_applies_ptt_state(self, main_js_content):
        # When connecting, PTT state should be applied
        # Look for the section in toggleConnection where connectionStatus is set to true
        conn_match = re.search(
            r'connectionStatus\s*=\s*true[^}]+(?:if\s*\([^)]*isPushToTalk[^)]*\)[^{]*\{[^}]*setMicrophoneEnabled[^}]*\}[^}]*)',
            main_js_content,
            re.DOTALL
        )
        assert conn_match, "Could not find connection-established PTT state application"
        # After connection, if PTT is enabled, mic should be disabled
        section = conn_match.group(0)
        assert 'setMicrophoneEnabled' in section, \
               "Connection must apply PTT microphone state"

    def test_ptt_state_preserved_across_reconnect(self, main_js_content):
        # PTT state should NOT be reset on connection
        # It's stored in isPushToTalk variable which persists
        assert 'isPushToTalk = false' not in main_js_content.split('toggleConnection')[1].split('}')[0:100][0], \
               "PTT state must not be reset on connection (should persist from variable)"


class TestPTTConnectionState:
    """Tests for PTT behavior with connection state."""

    def test_ptt_controls_shown_when_connected(self, main_js_content):
        # Find the updateUI function and check if it shows PTT controls when connected
        # Look for: pttControls.style.display = 'flex' when status is connected
        conn_match = re.search(
            r'pttControls\.style\.display\s*=\s*["\']flex["\']',
            main_js_content
        )
        assert conn_match, "PTT controls must be set to display: flex when connected"

        # Verify this is in the connected state section
        # Find the general vicinity of this code
        vicinity = main_js_content[max(0, conn_match.start() - 500):conn_match.end() + 200]
        assert 'connected' in vicinity.lower(), "PTT display: flex should be in connected state logic"

    def test_ptt_controls_hidden_when_disconnected(self, main_js_content):
        update_match = re.search(
            r'const updateUI\s*=\s*[^{]+\{([^}]+disconnected[^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert update_match, "Could not find updateUI function"
        disconnected_section = update_match.group(1)
        assert 'ptt-controls' in disconnected_section or 'pttControls' in disconnected_section, \
               "PTT controls must be hidden when disconnected"
        assert "'none'" in disconnected_section or '"none"' in disconnected_section, \
               "PTT controls display must be none when disconnected"

    def test_ptt_controls_disabled_while_connecting(self, main_js_content):
        update_match = re.search(
            r'const updateUI\s*=\s*[^{]+\{([^}]+connecting[^}]+)\}',
            main_js_content,
            re.DOTALL
        )
        assert update_match, "Could not find updateUI function"
        connecting_section = update_match.group(1)
        assert 'ptt-controls' in connecting_section or 'pttControls' in connecting_section, \
               "PTT controls must be hidden while connecting"


class TestPTTInitialState:
    """Tests for PTT initial state loading."""

    def test_ptt_state_loaded_on_init(self, main_js_content):
        # Should load PTT state from localStorage on page load
        assert 'loadPushToTalkState()' in main_js_content or \
               'loadPushToTalkState ();' in main_js_content or \
               'loadPushToTalkState()' in main_js_content, \
               "PTT state must be loaded on initialization"

    def test_ptt_state_applied_to_ui_on_init(self, main_js_content):
        # Should apply loaded PTT state to UI after page load
        # Check that PTT state is loaded somewhere in the file
        assert 'loadPushToTalkState' in main_js_content, "PTT state must be loaded function"
        # Check that updatePTTUI is called with loaded state
        init_match = re.search(
            r'isPushToTalk\s*=\s*loadPushToTalkState[^}]+updatePTTUI\s*\(\s*isPushToTalk\s*\)',
            main_js_content,
            re.DOTALL
        )
        assert init_match, "Loaded PTT state must be applied to UI via updatePTTUI"

    def test_mute_state_loaded_before_ptt(self, main_js_content):
        # Both mute and PTT states should be loaded on init
        dom_match = re.search(
            r'isMuted\s*=\s*loadMuteState[^}]+isPushToTalk\s*=\s*loadPushToTalkState',
            main_js_content,
            re.DOTALL
        )
        assert dom_match, "Both mute and PTT states must be loaded on initialization"


class TestPTTIntegrationScenarios:
    """Integration test scenarios for PTT."""

    def test_ptt_workflow_complete(self, main_js_content):
        """Test that complete PTT workflow is implemented:
        1. Toggle PTT on
        2. Button appears
        3. Hold button to talk
        4. Release to stop
        """
        # Check all components exist
        assert 'togglePushToTalk' in main_js_content
        assert 'updatePTTUI' in main_js_content
        assert 'startTalking' in main_js_content
        assert 'stopTalking' in main_js_content

    def test_ptt_persistence_workflow(self, main_js_content):
        """Test that PTT state persists correctly:
        1. Load from localStorage on init
        2. Save to localStorage on toggle
        3. Apply state on connection
        """
        assert 'loadPushToTalkState' in main_js_content
        assert 'savePushToTalkState' in main_js_content
        # Check that loaded state is applied after connection
        conn_match = re.search(
            r'connectionStatus\s*=\s*true[^}]+isPushToTalk[^}]+\}',
            main_js_content,
            re.DOTALL
        )
        assert conn_match, "PTT state must be applied after connection"

    def test_ptt_with_mute_interaction(self, main_js_content):
        """Test that PTT and mute modes interact correctly."""
        # Both should have separate storage keys
        assert 'Muted:' in main_js_content or 'Muted =' in main_js_content, \
               "Mute storage key must exist"
        assert 'PTT:' in main_js_content or 'PTT =' in main_js_content, \
               "PTT storage key must exist"

        # Both should be loaded on init
        assert 'loadMuteState' in main_js_content
        assert 'loadPushToTalkState' in main_js_content

        # PTT should respect mute state when leaving PTT mode
        toggle_match = re.search(
            r'const togglePushToTalk[^}]+\}[^}]+else[^}]+\{[^}]+\}',
            main_js_content,
            re.DOTALL
        )
        if toggle_match:
            # Check that leaving PTT mode restores mute state
            func_body = toggle_match.group(0)
            assert 'isMuted' in func_body, \
                   "Leaving PTT mode should restore mute state"
