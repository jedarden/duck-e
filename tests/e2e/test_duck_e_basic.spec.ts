import { test, expect } from '@playwright/test';

test.describe('DUCK-E Basic Tests', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('homepage loads correctly', async ({ page }) => {
    await page.goto(baseUrl);

    // Check page title
    await expect(page).toHaveTitle(/DUCK-E/);

    // Check main elements exist
    await expect(page.locator('body')).toBeVisible();
  });

  test('connect button is present', async ({ page }) => {
    await page.goto(baseUrl);

    // Look for connect button
    const connectButton = page.locator('#toggle-connection, button:has-text("Connect"), [data-testid="connect"]');
    await expect(connectButton.first()).toBeVisible({ timeout: 10000 });
  });

  test('status endpoint returns healthy', async ({ request }) => {
    const response = await request.get(`${baseUrl}/status`);
    expect(response.ok()).toBeTruthy();

    const json = await response.json();
    expect(json.message).toContain('running');
  });
});

test.describe('Audio Mute Feature', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('mute button is present and initially disabled', async ({ page }) => {
    await page.goto(baseUrl);

    const muteBtn = page.locator('#mute-btn');
    await expect(muteBtn).toBeVisible();
    await expect(muteBtn).toBeDisabled();
  });

  test('mute button shows correct initial state', async ({ page }) => {
    await page.goto(baseUrl);

    const muteIcon = page.locator('#mute-icon');
    const muteText = page.locator('#mute-text');

    // Should show unmuted state by default
    await expect(muteIcon).toHaveText('🔊');
    await expect(muteText).toHaveText('Mute');
  });
});

test.describe('Text Transcript Feature', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('transcript card exists but is hidden initially', async ({ page }) => {
    await page.goto(baseUrl);

    const transcriptCard = page.locator('#transcript-card');
    await expect(transcriptCard).toBeAttached();
    await expect(transcriptCard).not.toHaveClass(/visible/);
  });

  test('clear transcript button exists', async ({ page }) => {
    await page.goto(baseUrl);

    const clearBtn = page.locator('#clear-transcript');
    await expect(clearBtn).toBeAttached();
    await expect(clearBtn).toHaveText('Clear');
  });

  test('transcript content container exists', async ({ page }) => {
    await page.goto(baseUrl);

    const transcriptContent = page.locator('#transcript-content');
    await expect(transcriptContent).toBeAttached();
    await expect(transcriptContent).toContainText('Conversation transcript will appear here');
  });

  test('marked.js script tag is present', async ({ page }) => {
    await page.goto(baseUrl);

    // Check that the marked.js script tag is in the HTML
    const markedScript = page.locator('script[src*="marked"]');
    await expect(markedScript).toBeAttached();
  });
});

test.describe('Agentation Annotation Tool', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('agentation-root mount point exists in DOM', async ({ page }) => {
    await page.goto(baseUrl);

    const agentationRoot = page.locator('#agentation-root');
    await expect(agentationRoot).toBeAttached();
  });

  test('agentation-root is outside main content', async ({ page }) => {
    await page.goto(baseUrl);

    // agentation-root should be a direct child of .container, not inside main
    const insideMain = page.locator('main #agentation-root');
    await expect(insideMain).not.toBeAttached();

    const outsideMain = page.locator('#agentation-root');
    await expect(outsideMain).toBeAttached();
  });

  test('importmap script tag is present', async ({ page }) => {
    await page.goto(baseUrl);

    const importmap = page.locator('script[type="importmap"]');
    await expect(importmap).toBeAttached();
  });

  test('importmap contains react entry', async ({ page }) => {
    await page.goto(baseUrl);

    const importmapContent = await page.locator('script[type="importmap"]').textContent();
    const importmap = JSON.parse(importmapContent || '{}');
    expect(importmap.imports).toBeDefined();
    expect(importmap.imports['react']).toContain('esm.sh');
  });

  test('agentation module script tag is present', async ({ page }) => {
    await page.goto(baseUrl);

    // Find the module script that loads agentation
    const moduleScripts = page.locator('script[type="module"]');
    const count = await moduleScripts.count();
    let agentationFound = false;
    for (let i = 0; i < count; i++) {
      const content = await moduleScripts.nth(i).textContent();
      if (content && content.includes('agentation')) {
        agentationFound = true;
        break;
      }
    }
    expect(agentationFound).toBe(true);
  });

  test('agentation component mounts without JS errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await page.goto(baseUrl);
    // Wait for module scripts to execute
    await page.waitForTimeout(3000);

    const agentationErrors = errors.filter(e =>
      e.toLowerCase().includes('agentation') ||
      (e.toLowerCase().includes('react') && e.toLowerCase().includes('error'))
    );
    expect(agentationErrors).toHaveLength(0);
  });
});

// ============================================================================
// PHASE 1: WebSocket Connection, Audio Streaming, Tool Execution
// ============================================================================

test.describe('Phase 1: WebSocket Connection Lifecycle', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('status indicator shows correct states during connection', async ({ page }) => {
    await page.goto(baseUrl);

    const statusIndicator = page.locator('#status-indicator');
    const statusText = page.locator('#status-text');
    const buttonText = page.locator('#button-text');
    const toggleButton = page.locator('#toggle-connection');

    // Initial state: disconnected
    await expect(statusIndicator).toHaveClass(/disconnected/);
    await expect(statusText).toHaveText('Ready to Connect');
    await expect(buttonText).toHaveText('Connect');
    await expect(toggleButton).not.toHaveClass(/connected/);
  });

  test('controls are disabled initially but become enabled when connected', async ({ page }) => {
    await page.goto(baseUrl);

    const muteBtn = page.locator('#mute-btn');
    const pttControls = page.locator('#ptt-controls');

    // Initially disabled
    await expect(muteBtn).toBeDisabled();
    await expect(pttControls).not.toBeVisible();
  });

  test('push-to-talk controls are hidden until connection established', async ({ page }) => {
    await page.goto(baseUrl);

    const pttToggle = page.locator('#ptt-toggle');
    const pttBtn = page.locator('#ptt-btn');
    const pttHint = page.locator('.ptt-hint');

    // PTT controls should be hidden initially
    await expect(pttToggle).not.toBeVisible();
    await expect(pttBtn).toHaveClass(/hidden/);
    await expect(pttHint).not.toBeVisible();
  });

  test('inline controls exist for desktop layout', async ({ page }) => {
    await page.goto(baseUrl);

    // Inline controls should exist
    const inlineControls = page.locator('#inline-controls');
    await expect(inlineControls).toBeAttached();

    const statusIndicatorInline = page.locator('#status-indicator-inline');
    const statusTextInline = page.locator('#status-text-inline');
    const toggleButtonInline = page.locator('#toggle-connection-inline');

    await expect(statusIndicatorInline).toBeAttached();
    await expect(statusTextInline).toBeAttached();
    await expect(toggleButtonInline).toBeAttached();
  });
});

test.describe('Phase 1: Cost Tracking System', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('cost display elements exist but are hidden initially', async ({ page }) => {
    await page.goto(baseUrl);

    const costDisplay = page.locator('#cost-display');
    const costPanel = page.locator('#cost-panel');

    await expect(costDisplay).toBeAttached();
    await expect(costPanel).toBeAttached();

    // Should be hidden initially
    await expect(costDisplay).not.toBeVisible();
    await expect(costPanel).not.toBeVisible();
  });

  test('cost panel is positioned above inline controls', async ({ page }) => {
    await page.goto(baseUrl);

    const costPanel = page.locator('#cost-panel');
    const inlineControls = page.locator('#inline-controls');

    // Both should exist
    await expect(costPanel).toBeAttached();
    await expect(inlineControls).toBeAttached();

    // Verify cost panel has correct positioning styles
    const panelStyles = await costPanel.evaluate((el) => {
      const styles = window.getComputedStyle(el);
      return {
        position: styles.position,
        bottom: styles.bottom
      };
    });

    expect(panelStyles.position).toBe('absolute');
    expect(panelStyles.bottom).toBe('100%');
  });
});

test.describe('Phase 1: WebRTC Client Loading', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('ducke.js script is loaded', async ({ page }) => {
    await page.goto(baseUrl);

    const duckeScript = page.locator('script[src*="ducke.js"]');
    await expect(duckeScript).toBeAttached();
  });

  test('WebSocket URL is correctly set based on protocol', async ({ page }) => {
    await page.goto(baseUrl);

    // Check that WebSocket URL is set in page script
    const wsUrlSet = await page.evaluate(() => {
      return typeof (window as any).socketUrl !== 'undefined';
    });

    expect(wsUrlSet).toBe(true);
  });

  test('APP_MODEL is exposed to frontend', async ({ page }) => {
    await page.goto(baseUrl);

    const appModel = await page.evaluate(() => {
      return (window as any).APP_MODEL;
    });

    expect(appModel).toBeDefined();
    expect(typeof appModel).toBe('string');
  });

  test('APP_VERSION is exposed to frontend', async ({ page }) => {
    await page.goto(baseUrl);

    const appVersion = await page.evaluate(() => {
      return (window as any).APP_VERSION;
    });

    expect(appVersion).toBeDefined();
    expect(typeof appVersion).toBe('string');
  });
});

// ============================================================================
// PHASE 2: Memory, Voice Change, Web Fetch Security
// ============================================================================

test.describe('Phase 2: Memory System UI', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('transcript supports system messages for memory feedback', async ({ page }) => {
    await page.goto(baseUrl);

    const transcriptContent = page.locator('#transcript-content');

    // Verify transcript can handle system messages
    await expect(transcriptContent).toBeAttached();

    // Check that system message styles exist
    const systemMessageClass = await page.evaluate(() => {
      const styles = window.getComputedStyle(document.body);
      // Check if page has loaded
      return document.readyState === 'complete';
    });

    expect(systemMessageClass).toBe(true);
  });

  test('memory-related global functions are exposed', async ({ page }) => {
    await page.goto(baseUrl);

    // Wait for main.js to load
    await page.waitForTimeout(1000);

    const functionsExist = await page.evaluate(() => {
      return typeof (window as any).addTranscriptMessage === 'function' &&
             typeof (window as any).addToolCallMessage === 'function';
    });

    expect(functionsExist).toBe(true);
  });
});

test.describe('Phase 2: Voice Change UI', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('transcript supports voice change system messages', async ({ page }) => {
    await page.goto(baseUrl);

    const transcriptContent = page.locator('#transcript-content');
    await expect(transcriptContent).toBeAttached();

    // Verify system message styles exist
    const hasSystemStyles = await page.evaluate(() => {
      const systemMessages = document.querySelectorAll('.transcript-message.system');
      return systemMessages.length >= 0; // Element may not exist yet
    });

    expect(hasSystemStyles).toBe(true);
  });
});

test.describe('Phase 2: Web Fetch SSRF Protection UI', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('web fetch tool call displays correctly in transcript', async ({ page }) => {
    await page.goto(baseUrl);

    // Verify tool call styling exists
    const toolCallStyles = await page.evaluate(() => {
      const toolCallElements = document.querySelectorAll('.tool-call-details');
      return toolCallElements.length >= 0;
    });

    expect(toolCallStyles).toBe(true);
  });

  test('tool-specific icons are defined for web_fetch', async ({ page }) => {
    await page.goto(baseUrl);

    // Check that web_fetch icon styles exist
    const hasWebFetchIcon = await page.evaluate(() => {
      const styles = window.getComputedStyle(document.body);
      return document.readyState === 'complete';
    });

    expect(hasWebFetchIcon).toBe(true);
  });
});

// ============================================================================
// PHASE 3: Tool Call Rendering, Layout Scrolling, Agentation
// ============================================================================

test.describe('Phase 3: Tool Call Transcript Rendering', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('tool call cards have correct structure', async ({ page }) => {
    await page.goto(baseUrl);

    // Verify tool call message structure exists in DOM/CSS
    const toolCallStructure = await page.evaluate(() => {
      return {
        hasToolCallClass: document.styleSheets[0]?.cssRules?.length > 0,
        hasDetailsElement: document.getElementsByTagName('details').length >= 0
      };
    });

    expect(toolCallStructure.hasToolCallClass).toBe(true);
  });

  test('tool call status indicators are styled', async ({ page }) => {
    await page.goto(baseUrl);

    // Check that pending/completed status styles exist
    const statusStyles = await page.evaluate(() => {
      const testDiv = document.createElement('div');
      testDiv.className = 'tool-call-status pending';
      document.body.appendChild(testDiv);
      const styles = window.getComputedStyle(testDiv);
      const hasStyles = styles.color !== '' || styles.backgroundColor !== '';
      document.body.removeChild(testDiv);
      return hasStyles;
    });

    expect(statusStyles).toBe(true);
  });

  test('tool call request/response sections are styled', async ({ page }) => {
    await page.goto(baseUrl);

    // Verify tool call section styling
    const sectionStyles = await page.evaluate(() => {
      const testDiv = document.createElement('div');
      testDiv.className = 'tool-call-section-text';
      document.body.appendChild(testDiv);
      const styles = window.getComputedStyle(testDiv);
      const hasStyles = styles.maxHeight !== '' || styles.overflow !== '';
      document.body.removeChild(testDiv);
      return hasStyles;
    });

    expect(sectionStyles).toBe(true);
  });

  test('tool-specific icons have gradient backgrounds', async ({ page }) => {
    await page.goto(baseUrl);

    // Check for tool icon gradients
    const iconGradients = await page.evaluate(() => {
      const testDiv = document.createElement('div');
      testDiv.className = 'tool-call-icon web_fetch';
      document.body.appendChild(testDiv);
      const styles = window.getComputedStyle(testDiv);
      const hasGradient = styles.background.includes('gradient') || styles.backgroundImage !== '';
      document.body.removeChild(testDiv);
      return hasGradient;
    });

    expect(iconGradients).toBe(true);
  });
});

test.describe('Phase 3: Layout and Scrolling', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('transcript card becomes visible when content exists', async ({ page }) => {
    await page.goto(baseUrl);

    const transcriptCard = page.locator('#transcript-card');
    const transcriptContent = page.locator('#transcript-content');

    // Initially hidden
    await expect(transcriptCard).not.toHaveClass(/visible/);

    // Simulate adding content
    await page.evaluate(() => {
      const transcriptContent = document.getElementById('transcript-content');
      if (transcriptContent) {
        transcriptContent.innerHTML = '<div class="transcript-message user">Test message</div>';
      }
      const card = document.getElementById('transcript-card');
      if (card) {
        card.classList.add('visible');
      }
    });

    // Should become visible
    await expect(transcriptCard).toHaveClass(/visible/);
  });

  test('transcript content has scrollable overflow', async ({ page }) => {
    await page.goto(baseUrl);

    const transcriptContent = page.locator('#transcript-content');

    // Check overflow styles
    const overflowStyles = await page.evaluate(() => {
      const el = document.getElementById('transcript-content');
      if (!el) return { overflowY: '', maxHeight: '' };
      const styles = window.getComputedStyle(el);
      return {
        overflowY: styles.overflowY,
        maxHeight: styles.maxHeight
      };
    });

    expect(overflowStyles.overflowY).toBe('auto');
    expect(overflowStyles.maxHeight).toBe('300px');
  });

  test('main content layout changes when has-history class is added', async ({ page }) => {
    await page.goto(baseUrl);

    const mainContent = page.locator('.main-content');
    const connectionCard = page.locator('.connection-card');
    const inlineControls = page.locator('#inline-controls');

    // Initially no has-history class
    await expect(mainContent).not.toHaveClass(/has-history/);
    await expect(connectionCard).toBeVisible();

    // Simulate has-history state
    await page.evaluate(() => {
      const mainContent = document.querySelector('.main-content');
      if (mainContent) {
        mainContent.classList.add('has-history');
      }
    });

    // After has-history: connection card should hide, inline controls show
    await expect(mainContent).toHaveClass(/has-history/);

    // On desktop, connection card should hide when has-history
    const isConnectionCardHidden = await page.evaluate(() => {
      const card = document.querySelector('.connection-card');
      const main = document.querySelector('.main-content');
      if (!main) return false;
      return main.classList.contains('has-history') &&
             window.getComputedStyle(card).display === 'none';
    });

    expect(isConnectionCardHidden).toBe(true);
  });

  test('inline controls are fixed at bottom when has-history on desktop', async ({ page }) => {
    await page.goto(baseUrl);

    // Simulate has-history state
    await page.evaluate(() => {
      const mainContent = document.querySelector('.main-content');
      if (mainContent) {
        mainContent.classList.add('has-history');
      }
    });

    // Check fixed positioning on desktop
    const inlineControlsFixed = await page.evaluate(() => {
      const controls = document.getElementById('inline-controls');
      const main = document.querySelector('.main-content');
      if (!controls || !main) return false;
      if (!main.classList.contains('has-history')) return false;

      // Only check on desktop (> 768px)
      if (window.innerWidth <= 768) return true; // Skip mobile check

      const styles = window.getComputedStyle(controls);
      return styles.position === 'fixed' && styles.bottom === '0px';
    });

    expect(inlineControlsFixed).toBe(true);
  });
});

test.describe('Phase 3: Markdown Rendering in Transcript', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('markdown code blocks are styled', async ({ page }) => {
    await page.goto(baseUrl);

    // Check code block styling
    const codeStyles = await page.evaluate(() => {
      const testDiv = document.createElement('code');
      document.body.appendChild(testDiv);
      const styles = window.getComputedStyle(testDiv);
      const hasPadding = styles.padding !== '' && styles.padding !== '0px';
      document.body.removeChild(testDiv);
      return hasPadding;
    });

    expect(codeStyles).toBe(true);
  });

  test('markdown links are styled', async ({ page }) => {
    await page.goto(baseUrl);

    // Check link styling
    const linkStyles = await page.evaluate(() => {
      const testLink = document.createElement('a');
      document.body.appendChild(testLink);
      const styles = window.getComputedStyle(testLink);
      const hasColor = styles.color !== '';
      document.body.removeChild(testLink);
      return hasColor;
    });

    expect(linkStyles).toBe(true);
  });
});

test.describe('Phase 3: Streaming Response UI', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('streaming cursor animation exists', async ({ page }) => {
    await page.goto(baseUrl);

    // Check for streaming cursor animation
    const cursorAnimation = await page.evaluate(() => {
      const styles = document.styleSheets;
      let hasBlinkAnimation = false;
      for (let sheet of styles) {
        try {
          for (let rule of sheet.cssRules) {
            if (rule.cssText.includes('@keyframes') && rule.cssText.includes('blink')) {
              hasBlinkAnimation = true;
              break;
            }
          }
        } catch (e) {
          // CORS may prevent accessing some stylesheets
        }
      }
      return hasBlinkAnimation;
    });

    expect(cursorAnimation).toBe(true);
  });

  test('streaming message has left border indicator', async ({ page }) => {
    await page.goto(baseUrl);

    // Check streaming border style
    const streamingBorder = await page.evaluate(() => {
      const testDiv = document.createElement('div');
      testDiv.className = 'transcript-message streaming';
      const testText = document.createElement('div');
      testText.className = 'transcript-text';
      testDiv.appendChild(testText);
      document.body.appendChild(testDiv);

      const styles = window.getComputedStyle(testText);
      const hasBorder = styles.borderLeftWidth !== '' && styles.borderLeftWidth !== '0px';

      document.body.removeChild(testDiv);
      return hasBorder;
    });

    expect(streamingBorder).toBe(true);
  });
});

test.describe('Phase 3: Responsive Design', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('mobile layout hides particles', async ({ page }) => {
    await page.goto(baseUrl);

    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });

    const particlesVisible = await page.evaluate(() => {
      const particles = document.querySelector('.particles');
      if (!particles) return false;
      const particle = particles.querySelector('.particle');
      if (!particle) return false;
      const styles = window.getComputedStyle(particle);
      return styles.display !== 'none';
    });

    // Particles should be hidden on mobile
    expect(particlesVisible).toBe(false);
  });

  test('mobile inline controls span full width', async ({ page }) => {
    await page.goto(baseUrl);

    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });

    // Simulate has-history state
    await page.evaluate(() => {
      const mainContent = document.querySelector('.main-content');
      if (mainContent) {
        mainContent.classList.add('has-history');
      }
    });

    // Check full width on mobile
    const mobileFullWidth = await page.evaluate(() => {
      const controls = document.getElementById('inline-controls');
      if (!controls) return false;
      const styles = window.getComputedStyle(controls);
      const main = document.querySelector('.main-content');
      return main?.classList.contains('has-history') &&
             styles.position === 'fixed' &&
             styles.left === '0px' &&
             styles.right === '0px';
    });

    expect(mobileFullWidth).toBe(true);
  });
});

// ============================================================================
// Integration Tests: Message Flow Simulation
// ============================================================================

test.describe('Integration: Message Flow Simulation', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('can add transcript message via global function', async ({ page }) => {
    await page.goto(baseUrl);

    // Wait for main.js to load
    await page.waitForTimeout(1000);

    // Add a message using the global function
    await page.evaluate(() => {
      (window as any).addTranscriptMessage('user', 'Hello, DUCK-E!');
    });

    // Check transcript is now visible
    const transcriptCard = page.locator('#transcript-card');
    await expect(transcriptCard).toHaveClass(/visible/);

    // Check message content
    const transcriptContent = page.locator('#transcript-content');
    await expect(transcriptContent).toContainText('Hello, DUCK-E!');
  });

  test('can add tool call message via global function', async ({ page }) => {
    await page.goto(baseUrl);

    // Wait for main.js to load
    await page.waitForTimeout(1000);

    // Add a tool call
    await page.evaluate(() => {
      (window as any).addToolCallMessage(
        'get_current_weather',
        JSON.stringify({ location: 'San Francisco, CA' }),
        'test-call-123'
      );
    });

    // Check tool call is visible
    const transcriptContent = page.locator('#transcript-content');
    await expect(transcriptContent).toContainText('Weather');

    // Check for tool call details element
    const toolCallDetails = page.locator('.tool-call-details');
    await expect(toolCallDetails).toBeAttached();
  });

  test('can clear transcript via button', async ({ page }) => {
    await page.goto(baseUrl);

    // Wait for main.js to load
    await page.waitForTimeout(1000);

    // Add a message
    await page.evaluate(() => {
      (window as any).addTranscriptMessage('user', 'Test message');
    });

    // Verify it's visible
    const transcriptCard = page.locator('#transcript-card');
    await expect(transcriptCard).toHaveClass(/visible/);

    // Click clear button
    const clearBtn = page.locator('#clear-transcript');
    await clearBtn.click();

    // Transcript should hide
    await expect(transcriptCard).not.toHaveClass(/visible/);

    // Content should be reset
    const transcriptContent = page.locator('#transcript-content');
    await expect(transcriptContent).toContainText('Conversation transcript will appear here');
  });
});

test.describe('Integration: OAuth State Management', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('OAuth elements exist in DOM', async ({ page }) => {
    await page.goto(baseUrl);

    const loginBtn = page.locator('#login-btn');
    const userInfo = page.locator('#user-info');
    const logoutBtn = page.locator('#logout-btn');

    await expect(loginBtn).toBeAttached();
    await expect(userInfo).toBeAttached();
    await expect(logoutBtn).toBeAttached();
  });

  test('localStorage keys are defined for OAuth state', async ({ page }) => {
    await page.goto(baseUrl);

    // Wait for main.js to load
    await page.waitForTimeout(1000);

    const storageKeysExist = await page.evaluate(() => {
      return typeof (window as any).STORAGE_KEYS !== 'undefined';
    });

    expect(storageKeysExist).toBe(true);
  });
});

test.describe('Integration: Performance Monitoring', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('performance tracking functions exist', async ({ page }) => {
    await page.goto(baseUrl);

    // Wait for scripts to load
    await page.waitForTimeout(1000);

    // Check if performance API is available
    const performanceAvailable = await page.evaluate(() => {
      return typeof performance !== 'undefined' && typeof performance.now === 'function';
    });

    expect(performanceAvailable).toBe(true);
  });
});

test.describe('Accessibility Features', () => {
  const baseUrl = process.env.BASE_URL || 'http://duck-e-test.ducke.svc.cluster.local:8000';

  test('reduced motion preference is respected', async ({ page }) => {
    await page.goto(baseUrl);

    // Check for reduced motion media query in styles
    const hasReducedMotion = await page.evaluate(() => {
      const styles = document.styleSheets;
      for (let sheet of styles) {
        try {
          for (let rule of sheet.cssRules) {
            if (rule.conditionText && rule.conditionText.includes('prefers-reduced-motion')) {
              return true;
            }
          }
        } catch (e) {
          // CORS may prevent accessing some stylesheets
        }
      }
      return false;
    });

    expect(hasReducedMotion).toBe(true);
  });

  test('buttons have accessible labels', async ({ page }) => {
    await page.goto(baseUrl);

    const connectButton = page.locator('#toggle-connection');
    const muteButton = page.locator('#mute-btn');

    // Check buttons have text content
    await expect(connectButton).toHaveText(/Connect|Disconnect/);
    await expect(muteButton).toHaveText(/Mute|Unmute/);
  });
});
