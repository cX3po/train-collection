/**
 * tests/test_bundle12.spec.mjs
 *
 * Bundle 12 verification — price seed:
 *  1. App starts cleanly with the seed file present (no traceback on load)
 *  2. Import area still renders (seed lookup wired into import path)
 *
 * The functional lookup is verified at the unit level in the build console
 * (lookup_price_seed('Lionel','2333') → Santa Fe F3 AA Set, $400-$1500;
 * _apply_price_seed → $950 with combined note). This spec covers the UI
 * surface — proving the app still renders after the seed integration.
 *
 * Run from ~/axiom:
 *   npx playwright test ~/train-collection/tests/test_bundle12.spec.mjs --reporter=list
 */

import { test, expect } from '@playwright/test';

const BASE_URL = process.env.TRAINS_BASE_URL || 'http://127.0.0.1:8502';
const APP_LOAD_TIMEOUT = 20_000;

test.describe('Bundle 12 — price seed', () => {
  test('app loads + no traceback after seed integration', async ({ page }) => {
    await page.goto(`${BASE_URL}/?user=SmokeTest`);
    await expect(page.getByText('Signed in as:')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    const tracebackVisible = await page.getByText(/Traceback \(most recent call last\)/).isVisible().catch(() => false);
    expect(tracebackVisible).toBe(false);
  });

  test('Import/Export tab renders the Import section without error', async ({ page }) => {
    await page.goto(`${BASE_URL}/?user=SmokeTest`);
    await expect(page.getByText('Signed in as:')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    await page.getByRole('tab', { name: 'Import/Export' }).click();
    await expect(page.getByText('Import / Export')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    await expect(page.getByText('Import Items')).toBeVisible();
    // Paste-data textarea + Import button
    await expect(page.getByRole('button', { name: 'Import', exact: true })).toBeVisible();
  });
});
