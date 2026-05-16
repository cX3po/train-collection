/**
 * tests/test_trains_smoke.spec.mjs
 *
 * Tier 1.5 smoke test for the family-deployed trains app
 * (axiom-train-collection.service on 127.0.0.1:8502).
 *
 * Verifies the post-bundle-loop state:
 *  1. Sign-in page renders
 *  2. Family sign-in works
 *  3. Six tabs render
 *  4. AX chat input is present
 *  5. Clear All requires two-step confirmation (Bundle 3)
 *
 * AX backend (CLI subprocess) is not invoked from the test — that would
 * burn a real model call per CI run for a feature with one user.
 *
 * Run from ~/axiom (which has playwright installed):
 *   npx playwright test ~/train-collection/tests/test_trains_smoke.spec.mjs --reporter=list
 */

import { test, expect } from '@playwright/test';

const BASE_URL = process.env.TRAINS_BASE_URL || 'http://127.0.0.1:8502';
const APP_LOAD_TIMEOUT = 20_000;

test.describe('Trains app — post-bundle smoke', () => {
  test('sign-in page renders', async ({ page }) => {
    await page.goto(BASE_URL);
    await expect(page.getByText('Welcome to the Train Collection')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    await expect(page.getByRole('textbox', { name: 'Your name' })).toBeVisible();
    // Sign In button is conditional on a non-empty name — see sign-in flow test for that path.
  });

  test('sign-in flow → main UI with six tabs', async ({ page }) => {
    await page.goto(BASE_URL);
    const nameInput = page.getByRole('textbox', { name: 'Your name' });
    await nameInput.fill('SmokeTest', { timeout: APP_LOAD_TIMEOUT });
    await nameInput.press('Enter');  // Streamlit re-runs only after commit
    const signIn = page.getByRole('button', { name: 'Sign In', exact: true });
    await expect(signIn).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    await signIn.click();

    // Wait for main app — title or sidebar "Signed in as"
    await expect(page.getByText('Signed in as:')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    await expect(page.getByText('SmokeTest').first()).toBeVisible();

    // Six tabs by name
    for (const tabName of ['Collection', 'Scan Items', 'Ask AX', 'Sell Items', 'Stats', 'Import/Export']) {
      await expect(page.getByRole('tab', { name: tabName })).toBeVisible();
    }
  });

  test('Ask AX tab — chat input renders', async ({ page }) => {
    await page.goto(BASE_URL);
    const nameInput = page.getByRole('textbox', { name: 'Your name' });
    await nameInput.fill('SmokeTest', { timeout: APP_LOAD_TIMEOUT });
    await nameInput.press('Enter');  // Streamlit re-runs only after commit
    const signIn = page.getByRole('button', { name: 'Sign In', exact: true });
    await expect(signIn).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    await signIn.click();
    await expect(page.getByText('Signed in as:')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });

    await page.getByRole('tab', { name: 'Ask AX' }).click();
    await expect(page.getByText('Ask AX About Trains')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    await expect(page.getByPlaceholder(/Ask AX about trains/)).toBeVisible();
  });

  test('Clear All requires confirmation (Bundle 3)', async ({ page }) => {
    await page.goto(BASE_URL);
    const nameInput = page.getByRole('textbox', { name: 'Your name' });
    await nameInput.fill('SmokeTest', { timeout: APP_LOAD_TIMEOUT });
    await nameInput.press('Enter');  // Streamlit re-runs only after commit
    const signIn = page.getByRole('button', { name: 'Sign In', exact: true });
    await expect(signIn).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    await signIn.click();
    await expect(page.getByText('Signed in as:')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });

    await page.getByRole('tab', { name: 'Import/Export' }).click();
    await expect(page.getByText('Import / Export')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });

    // The "Clear All" button only renders when total > 0. If the DB is empty,
    // there's nothing to test — pass (the no-button state is also safe).
    const clearAllBtn = page.getByRole('button', { name: 'Clear All', exact: true });
    if (await clearAllBtn.count() === 0) {
      test.info().annotations.push({ type: 'note', description: 'DB empty — Clear All not rendered, nothing to confirm.' });
      return;
    }

    await clearAllBtn.first().click();

    // Bundle 3 contract: clicking "Clear All" must NOT delete; it must show a
    // confirmation step with "Yes, delete everything" and "Cancel".
    await expect(page.getByText(/will permanently delete all/)).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: 'Yes, delete everything' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible();

    // Click Cancel — must NOT delete data.
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByText(/will permanently delete all/)).not.toBeVisible({ timeout: 5000 });
  });
});
