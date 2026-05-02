import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  reporter: 'list',
  use: {
    baseURL: process.env.TRAINS_BASE_URL || 'http://127.0.0.1:8502',
    trace: 'on-first-retry',
  },
});
