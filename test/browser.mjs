// PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node test/browser.mjs
import assert from 'node:assert/strict';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const launchOptions = {headless: true};
if (process.env.CHROME_PATH) launchOptions.executablePath = process.env.CHROME_PATH;
const browser = await chromium.launch(launchOptions);
const base = process.env.OCR_PUI_URL || 'http://localhost:18081';
const requests = [];
const page = await browser.newPage({viewport: {width: 1280, height: 1000}});
page.on('request', request => requests.push(request.url()));
try {
  await page.goto(base + '/digital-text?q=mascot');
  assert.equal(await page.locator('.ocr-result').count(), 2);
  assert.ok(await page.locator('.ocr-excerpt mark').count() >= 2);
  await page.screenshot({path: '/tmp/ocr-search-desktop.png', fullPage: true});
  const links = await page.getByRole('link', {name: 'View page', exact: true}).evaluateAll(nodes => nodes.map(node => node.href));
  for (const link of links) {
    await page.goto(link);
    await page.waitForSelector('.openseadragon-canvas canvas', {timeout: 30000});
    await page.waitForFunction(() => document.querySelector('.dv-active'), {timeout: 30000});
    assert.equal(await page.locator('.ocr-pages [aria-current="page"]').count(), 1);
    const number = await page.locator('.ocr-pages [aria-current="page"]').innerText();
    assert.equal(number, link.includes('/scw?') ? '8' : '4');
    await page.waitForSelector('.ocr-word-highlight', {timeout: 30000});
    assert.equal(await page.locator('.ocr-word-highlight').count(), number === '8' ? 1 : 5);
    if (number === '4') {
      await page.getByRole('button', {name: 'Next match', exact: true}).click();
      assert.match(await page.locator('.ocr-match-controls [role="status"]').innerText(), /Match 1 of 5/);
    }
    await page.getByRole('button', {name: 'Show whole page', exact: true}).click();
    await page.screenshot({path: `/tmp/ocr-page-${number}.png`, fullPage: true});
  }
  assert.equal(requests.filter(url => new URL(url).hostname === 'compass.fivecolleges.edu').length, 0);
  assert.ok(requests.some(url => url.startsWith('https://digital.smith.edu/')));
  const response = await page.goto(base + '/digital-text/items/scw?canvas=missing');
  assert.equal(response.status(), 404);
  assert.equal(await page.locator('[data-dv-source-group]').count(), 0);
  await page.goto(base + '/digital-text?q=mascot&item=scw');
  assert.equal(await page.locator('.ocr-result').count(), 1);
  await page.goto(base + '/digital-text?q=zzzxqpilotnomatch20260921');
  assert.ok((await page.locator('.ocr-search').innerText()).includes('No pages found'));
  await page.goto(base + '/digital-text?q=' + encodeURIComponent('<script>alert(1)</script>'));
  assert.equal(await page.locator('.ocr-search script').count(), 0);
  await page.goto(base + '/digital-text?q=the');
  if (await page.getByRole('link', {name: 'Next results'}).count()) {
    await page.getByRole('link', {name: 'Next results'}).click();
    assert.ok(await page.getByRole('link', {name: 'Previous results'}).count());
  }
  await page.setViewportSize({width:390, height:844});
  await page.goto(base + '/digital-text?q=mascot');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path:'/tmp/ocr-search-mobile.png', fullPage:true});
  const nojs = await browser.newPage({javaScriptEnabled:false});
  await nojs.goto(base + '/digital-text');
  await nojs.getByLabel('Words or phrase').fill('mascot');
  await nojs.getByRole('button', {name:'Search text'}).click();
  assert.equal(await nojs.locator('.ocr-result').count(), 2);
  await nojs.getByRole('link', {name:'View page', exact:true}).first().click();
  assert.ok((await nojs.locator('noscript').innerText()).includes('Turn on JavaScript'));
  await page.goto(base + '/digital-text');
  await page.getByLabel('Words or phrase').focus();
  await page.keyboard.type('mascot');
  await page.keyboard.press('Enter');
  await page.waitForURL('**/digital-text?**');
  assert.equal(await page.locator('.ocr-result').count(), 2);
  assert.equal((await page.goto(base + '/search?reset=true')).status(), 200);
  console.log('PASS: results, page selection, no Compass requests, stale canvas, filters, empty/escaped input, pagination, mobile, no-JS, keyboard and catalog search');
} finally {
  await browser.close();
}
