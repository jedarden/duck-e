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
    const connectButton = page.locator('#connect-button, button:has-text("Connect"), [data-testid="connect"]');
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
