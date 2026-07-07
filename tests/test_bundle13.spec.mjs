/**
 * tests/test_bundle13.spec.mjs
 *
 * Bundle 13 verification — Codex pre-family-test fixes:
 *  1. App loads cleanly after the helpers changed (no traceback)
 *  2. Scan tab still renders the upload widget (regression check)
 *  3. Focus banner appears for a non-existent ID without throwing
 *
 * Unit-tested separately (CLI smoke during build):
 *   • lookup_price_seed("MTH", "20-9999") → None (false-positive fix)
 *   • lookup_price_seed("MTH", "20-3030") → exact match
 *   • lookup_price_seed("Lionel", "2333-100") → prefix match to 2333
 *   • _safe_photo_path("/etc/passwd") → None (containment)
 *
 * Run from ~/axiom:
 *   npx playwright test ~/train-collection/tests/test_bundle13.spec.mjs --reporter=list
 */

import { test, expect } from '@playwright/test';

const BASE_URL = process.env.TRAINS_BASE_URL || 'http://127.0.0.1:8502';
const APP_LOAD_TIMEOUT = 20_000;

test.describe('Bundle 13 — Codex pre-family-test fixes', () => {
  test('app still loads after helper refactors', async ({ page }) => {
    await page.goto(`${BASE_URL}/?user=SmokeTest`);
    await expect(page.getByText('Signed in as:')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    const tracebackVisible = await page.getByText(/Traceback \(most recent call last\)/).isVisible().catch(() => false);
    expect(tracebackVisible).toBe(false);
  });

  test('Scan Items tab renders without regression', async ({ page }) => {
    await page.goto(`${BASE_URL}/?user=SmokeTest`);
    await expect(page.getByText('Signed in as:')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    await page.getByRole('tab', { name: 'Scan Items' }).click();
    await expect(page.getByText('Scan & Identify Trains')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    // The "Clear scan / start over" button must NOT be visible without state.
    await expect(
      page.getByRole('button', { name: 'Clear scan / start over' })
    ).not.toBeVisible({ timeout: 3_000 });
  });

  test('?focus=999999 (non-existent) shows graceful warning, no traceback', async ({ page }) => {
    await page.goto(`${BASE_URL}/?user=SmokeTest&focus=999999`);
    await expect(page.getByText('Signed in as:')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    // For non-existent IDs the app prints a warning, not a traceback.
    await expect(
      page.getByText(/no matching row exists|may have been deleted/)
    ).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    const tracebackVisible = await page.getByText(/Traceback \(most recent call last\)/).isVisible().catch(() => false);
    expect(tracebackVisible).toBe(false);
  });

  test('?focus=garbage (invalid int) does not crash the app', async ({ page }) => {
    await page.goto(`${BASE_URL}/?user=SmokeTest&focus=not-a-number`);
    await expect(page.getByText('Signed in as:')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    const tracebackVisible = await page.getByText(/Traceback \(most recent call last\)/).isVisible().catch(() => false);
    expect(tracebackVisible).toBe(false);
  });
});
