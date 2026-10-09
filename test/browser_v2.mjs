// PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node test/browser_v2.mjs
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const port = process.env.OCR_TEST_PORT || '49159';
const fake = spawn(process.env.RUBY || 'ruby', ['test/fake_ui_server.rb'], {
  cwd: repo,
  env: {...process.env, OCR_TEST_PORT: port},
  stdio: ['ignore', 'pipe', 'inherit']
});

const base = await new Promise((resolve, reject) => {
  let output = '';
  const timeout = setTimeout(() => reject(new Error('Fake UI server did not start')), 10000);
  fake.stdout.on('data', chunk => {
    output += chunk.toString();
    const match = output.match(/FAKE_UI_URL=(http:\/\/127\.0\.0\.1:\d+)/);
    if (match) {
      clearTimeout(timeout);
      resolve(match[1]);
    }
  });
  fake.on('exit', code => {
    clearTimeout(timeout);
    reject(new Error(`Fake UI server exited before startup (${code})`));
  });
});

let browser;
try {
  const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const launchOptions = {headless: true};
  if (process.env.CHROME_PATH) launchOptions.executablePath = process.env.CHROME_PATH;
  browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({viewport: {width: 1280, height: 1000}});

  await page.goto(`${base}/digital-text?q=mascot`);
  assert.equal(await page.locator('.ocr-result').count(), 2);
  assert.equal(await page.getByRole('navigation', {name: 'Filter by publication'}).count(), 1);
  assert.equal(await page.getByRole('navigation', {name: 'Filter by decade'}).count(), 1);
  assert.equal(await page.getByRole('navigation', {name: 'Sort results'}).count(), 1);
  assert.equal(await page.getByText('2 matching items', {exact: false}).count(), 1);
  assert.equal(await page.locator('.ocr-result-count[aria-live]').count(), 0);
  assert.equal(await page.locator('[aria-current="true"]').count(), 1);

  const query = page.getByLabel('Words or phrase');
  await query.focus();
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => document.activeElement.textContent.trim()), 'Search text');
  await page.keyboard.press('Tab');
  assert.match(await page.evaluate(() => document.activeElement.getAttribute('href')), /series=Example\+Weekly/);
  const focusStyle = await page.evaluate(() => getComputedStyle(document.activeElement).outlineWidth);
  assert.equal(focusStyle, '3px');

  await page.keyboard.press('Enter');
  await page.waitForURL(/series=Example\+Weekly/);
  assert.equal(await page.locator('.ocr-result').count(), 1);
  assert.equal(await page.locator('[aria-current="true"]').count(), 2);
  await page.getByRole('link', {name: 'Clear all'}).click();
  await page.waitForURL(/sort=relevance/);
  assert.equal(await page.locator('.ocr-result').count(), 2);

  await page.getByLabel('From').fill('1930');
  await page.getByLabel('To').fill('1940');
  await page.getByRole('button', {name: 'Apply years'}).click();
  await page.waitForURL(/year_from=1930/);
  assert.equal(await page.locator('.ocr-result').count(), 1);
  assert.equal(await page.getByRole('link', {name: /Remove start year filter/}).count(), 1);

  await page.goto(`${base}/digital-text?q=mascot`);
  await page.getByRole('link', {name: 'See all 2 matching pages'}).first().click();
  await page.waitForURL(/item=demo-a/);
  assert.equal(await page.locator('.ocr-result').count(), 1);
  assert.match(await page.locator('.ocr-result-count').innerText(), /2 matching pages/);

  await page.getByRole('link', {name: 'Example Weekly, 1927-12-07'}).click();
  await page.waitForURL(/\/items\/demo-a/);
  assert.equal(await page.getByRole('heading', {name: 'Example Weekly, 1927-12-07'}).count(), 1);
  assert.equal(await page.locator('[data-dv-source-group]').count(), 0);
  assert.equal(await page.getByRole('link', {name: 'View in the finding aid'}).count(), 1);
  assert.equal(await page.locator('.ocr-pages a').count(), 2);

  const noJavaScript = await browser.newPage({javaScriptEnabled: false});
  await noJavaScript.goto(`${base}/digital-text`);
  await noJavaScript.getByLabel('Words or phrase').fill('mascot');
  await noJavaScript.getByRole('button', {name: 'Search text'}).click();
  assert.equal(await noJavaScript.locator('.ocr-result').count(), 2);
  await noJavaScript.getByRole('link', {name: 'Clear all'}).click();
  assert.ok((await noJavaScript.url()).includes('q=mascot'));

  console.log('PASS: keyboard focus, visible focus, named filter/sort landmarks, current states, GET filters/chips, grouped/item pages and no-JavaScript navigation against the fake API');
} finally {
  if (browser) await browser.close();
  fake.kill('SIGTERM');
}
