const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const http = require('node:http');
const path = require('node:path');
const readline = require('node:readline');
const { once } = require('node:events');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const python = process.env.PYTHON || path.join(root, '.local/venv/bin/python');
const serve = [
  'from http.server import ThreadingHTTPServer',
  'from financial_twin.server import Handler',
  'server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)',
  'print(server.server_port, flush=True)',
  'server.serve_forever()',
].join('\n');

async function verify(browser, status) {
  let calls = 0;
  const provider = http.createServer((request, response) => {
    calls++;
    request.resume();
    response.writeHead(status || 500);
    response.end();
  });
  provider.listen(0, '127.0.0.1');
  await once(provider, 'listening');
  const env = {
    PATH: process.env.PATH,
    AI_BASE_URL: 'http://127.0.0.1:' + provider.address().port,
    AI_API_KEY_MODEL: 'fictional-test-model',
  };
  if (status) env.AI_API_KEY_1 = 'fictional-test-key';
  const app = spawn(python, ['-c', serve], { cwd: root, env, stdio: ['ignore', 'pipe', 'inherit'] });
  const lines = readline.createInterface({ input: app.stdout });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  try {
    const [port] = await once(lines, 'line');
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:' + port, { waitUntil: 'domcontentloaded' });
    const story = 'Fictional household with CHF 5000 in cash deciding whether to study.';
    const proposal = 'Fictional decision to spend CHF 1600 on a course.';
    const pattern = status === 401 ? /rejected.*key/i : status === 429 ? /rate limit/i : /AI_API_KEY_1/;
    await page.locator('#statement').fill(story);
    await page.locator('#extract-button').press('Enter');
    await page.locator('#intake-error').filter({ hasText: pattern }).waitFor();
    assert.equal(await page.locator('#statement').inputValue(), story);
    assert.equal(await page.locator('#offline-demo').evaluate(button => button === document.activeElement), true);
    await page.locator('#offline-demo').press('Enter');
    await page.locator('#results').waitFor();
    const inspect = page.getByText('Details', { exact: true }).first();
    await inspect.click();
    await page.locator('#inspector').getByRole('tab', { name: 'What if' }).click();
    await page.locator('#branch-event').fill(proposal);
    await page.getByRole('button', { name: 'Get the numbers' }).press('Enter');
    await page.locator('#inspector [role="alert"]').filter({ hasText: pattern }).waitFor();
    assert.equal(await page.locator('#branch-event').inputValue(), proposal);
    const loaded = page.waitForResponse(response => response.url().endsWith('/api/demo/alexia'));
    await page.getByRole('button', { name: 'Explore the fictional demo without AI' }).press('Enter');
    assert.equal((await loaded).status(), 200);
    await page.locator('#drawer-backdrop').waitFor({ state: 'hidden' });
    await inspect.click();
    assert.equal(await page.locator('#branch-event').inputValue(), proposal);
    assert.equal(await page.locator('#statement').inputValue(), story);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    if (status) assert.ok(calls >= 2);
    else assert.equal(calls, 0);
    console.log(status || 'missing', 'intake + branch + recovery passed, provider calls', calls);
  } finally {
    await context.close();
    lines.close();
    app.kill();
    provider.close();
  }
}

async function main() {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined, headless: true });
  try {
    for (const status of [0, 401, 429]) await verify(browser, status);
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
