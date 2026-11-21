const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome',
    headless: true,
    args: ['--no-sandbox'],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const cdp = await page.context().newCDPSession(page);
    const url = process.env.APP_URL || 'http://127.0.0.1:8000/';
    async function inspect(name) {
      const { nodes } = await cdp.send('Accessibility.getFullAXTree');
      const names = nodes.filter(node => !node.ignored)
        .map(node => `${node.role?.value}:${node.name?.value || ''}`)
        .filter(value => /story|goal|target|priority|chart|branch|income|error/i.test(value));
      console.log(name, names.slice(0, 30).join(' | '));
      const missing = await page.evaluate(() => [...document.querySelectorAll('input,select,textarea,button')]
        .filter(element => element.getClientRects().length && !element.disabled && !element.labels?.length
          && !element.getAttribute('aria-label') && !element.getAttribute('aria-labelledby')
          && !element.textContent.trim() && !element.title)
        .map(element => element.outerHTML));
      assert.deepEqual(missing, []);
    }
    await page.goto(url);
    await inspect('INTAKE');
    await page.locator('#statement').fill('I am a fictional person with CHF 8000 in cash, CHF 4800 monthly net income and CHF 2400 monthly expenses. I want to save CHF 10000 for a trip in three years.');
    await page.locator('#extract-button').click();
    await page.locator('#tuning').waitFor({ state: 'visible', timeout: 120000 });
    await inspect('REVIEW');
    await page.locator('#simulate-button').click();
    await page.locator('#results').waitFor({ state: 'visible' });
    await inspect('RESULTS');
    const chart = page.locator('#trajectory-chart svg');
    assert.equal(await chart.getAttribute('role'), 'img');
    assert.match(await chart.getAttribute('aria-label'), /Net worth/);
    assert.ok(await chart.evaluate(element => element.getBoundingClientRect().width >= 520));
    const button = page.getByText('Explore this moment', { exact: true }).first();
    await button.focus();
    await page.keyboard.press('Enter');
    await page.locator('#close-drawer').waitFor({ state: 'visible' });
    assert.equal(await page.evaluate(() => document.activeElement.id), 'close-drawer');
    await page.keyboard.press('Escape');
    assert.equal(await button.evaluate(element => element === document.activeElement), true);
    await button.click();
    const panel = page.locator('#inspector');
    await panel.locator('#branch-event').fill('Take a new job with CHF 500 higher net monthly income beginning immediately.');
    await panel.getByRole('button', { name: 'Get the numbers' }).click();
    await panel.getByText('Check the numbers').waitFor({ timeout: 120000 });
    await inspect('BRANCH REVIEW');
    const field = panel.locator('.block').first().getByLabel(/Change per month/);
    await field.fill('900');
    assert.match((await panel.locator('.proposal-summary').innerText()).replace(/\s/g, ''), /900/);
    const request = page.waitForRequest(response => response.url().endsWith('/api/branch/apply'));
    const response = page.waitForResponse(response => response.url().endsWith('/api/branch/apply'));
    await panel.getByRole('button', { name: 'Create branch' }).click();
    const body = (await request).postDataJSON();
    assert.equal(body.proposal.changes[0].amount_chf, 900);
    const applied = await response;
    assert.equal(applied.status(), 200);
    const result = await applied.json();
    assert.equal(result.branch.proposal.changes[0].amount_chf, 900);
    assert.equal(result.nodes[0].scenario.phases[0].monthly_income_chf, 5700);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.goto(url);
    await page.route('**/api/extract', route => route.fulfill({
      status: 401,
      json: { error: 'API key rejected. Configure a valid API key.' },
    }));
    await page.locator('#statement').fill('A fictional financial story for testing');
    await page.locator('#extract-button').click();
    await page.locator('#intake-error').getByText('API key rejected.', { exact: false }).waitFor();
    await inspect('ERROR');
    const ax = await cdp.send('Accessibility.getFullAXTree');
    assert.ok(ax.nodes.some(node => !node.ignored && node.role?.value === 'alert'));
    console.log('PASS accessible names, semantic charts, mobile chart dimensions, focus restoration, error alert and edited proposal');
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
