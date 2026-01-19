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

  test('marked.js library is loaded', async ({ page }) => {
    await page.goto(baseUrl);
    
    // Check that marked is available in the window
    const markedLoaded = await page.evaluate(() => {
      return typeof (window as any).marked !== 'undefined';
    });
    expect(markedLoaded).toBe(true);
  });
});
