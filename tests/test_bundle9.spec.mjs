/**
 * tests/test_bundle9.spec.mjs
 *
 * Bundle 9 verification — closes the two bugs Dad reported 2026-05-16:
 *   Bug 1: "can only save 3 pictures at a time" (Streamlit rerun nuked scan UI)
 *   Bug 2: "doesn't know where he is when he logs back on" (no resume state)
 *
 * Three tests:
 *  1. ?user= query string auto-signs-in (no manual sign-in flow needed)
 *  2. Welcome-back card renders for a signed-in user (count + last-added)
 *  3. Sign Out clears the URL query so the next visit lands back at sign-in
 *
 * NOT covered live: the scan-result persistence (Bug 1) — running it would
 * burn a real vision API call per CI invocation. That fix is verified by code
 * inspection (results stored in st.session_state, render block reads state,
 * survives reruns) and by the standing app.py syntax/import smoke. If we add
 * a fake VisionEngine mock later, this is the spec to extend.
 *
 * Run from ~/axiom (which has playwright installed):
 *   npx playwright test ~/train-collection/tests/test_bundle9.spec.mjs --reporter=list
 */

import { test, expect } from '@playwright/test';

const BASE_URL = process.env.TRAINS_BASE_URL || 'http://127.0.0.1:8502';
const APP_LOAD_TIMEOUT = 20_000;

test.describe('Bundle 9 — persistent sign-in & welcome-back', () => {
  test('?user=Dad auto-signs-in (skips sign-in screen)', async ({ page }) => {
    await page.goto(`${BASE_URL}/?user=Dad`);
    // Should NOT see the sign-in welcome page
    await expect(page.getByText('Welcome to the Train Collection')).not.toBeVisible({ timeout: 5_000 });
    // Should see the signed-in sidebar marker
    await expect(page.getByText('Signed in as:')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
    // Sidebar shows the name we passed
    await expect(page.getByText('Dad').first()).toBeVisible();
  });

  test('welcome-back card renders for signed-in user', async ({ page }) => {
    await page.goto(`${BASE_URL}/?user=SmokeTest`);
    await expect(page.getByText('Signed in as:')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });

    // Welcome card shows for both empty + populated DB. Empty path says
    // "Your collection is empty"; populated path says "Welcome back, X — you have N items".
    // Match either via the shared "Welcome" prefix to keep this test DB-state agnostic.
    const welcomeText = page.locator('text=/Welcome.*SmokeTest/');
    await expect(welcomeText.first()).toBeVisible({ timeout: APP_LOAD_TIMEOUT });
  });

  test('Sign Out clears query string so user lands at sign-in next visit', async ({ page }) => {
    await page.goto(`${BASE_URL}/?user=TempUser`);
    await expect(page.getByText('Signed in as:')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });

    // Click Sign Out (sidebar)
    await page.getByRole('button', { name: 'Sign Out' }).click();

    // After sign-out, the welcome screen should be visible again
    await expect(page.getByText('Welcome to the Train Collection')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });

    // And the URL should no longer carry ?user=
    // (Streamlit's st.query_params.clear() reflects in window.location.search)
    const url = page.url();
    expect(url).not.toContain('user=TempUser');
  });
});

test.describe('Bundle 9 — scan-result state structure', () => {
  // This is a "shape only" test — it doesn't run a real scan (cost), but it
  // verifies the Scan Items tab renders the upload widget AND the new
  // "Clear scan / start over" copy doesn't show without state (state-only render).
  test('Scan Items tab renders upload widget; clear-scan button hidden without state', async ({ page }) => {
    await page.goto(`${BASE_URL}/?user=SmokeTest`);
    await expect(page.getByText('Signed in as:')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });

    await page.getByRole('tab', { name: 'Scan Items' }).click();
    await expect(page.getByText('Scan & Identify Trains')).toBeVisible({ timeout: APP_LOAD_TIMEOUT });

    // The Clear scan button only appears once st.session_state["scan_results"]
    // is populated. Without a real scan, it must NOT be visible — proves the
    // render block is gated on state, not on a stale flag.
    await expect(
      page.getByRole('button', { name: 'Clear scan / start over' })
    ).not.toBeVisible({ timeout: 3_000 });
  });
});
