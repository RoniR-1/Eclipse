const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
(async () => {
 const browser = await chromium.launch({executablePath:process.env.CHROME_PATH || '/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try {
  const page = await browser.newPage({viewport:{width:1280,height:900},acceptDownloads:true});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(process.env.APP_URL || 'http://127.0.0.1:8000/');
  await page.locator('#statement').fill('I am a fictional person with CHF 8000 in cash, CHF 4800 monthly net income and CHF 2400 monthly expenses. I want to save CHF 10000 for a trip in three years.');
  const extraction = page.waitForResponse(r => r.url().endsWith('/api/extract'), {timeout:120000});
  await page.locator('#extract-button').click();
  assert.equal((await extraction).status(), 200);
  await page.locator('#tuning').waitFor({state:'visible',timeout:120000});
  assert.match(await page.locator('#baseline').innerText(), /8.000/);
  assert.match(await page.locator('#goal-editor').innerText(), /Trip/i);
  const target = page.locator('#goal-editor input').first();
  await target.fill('12000');
  await page.locator('#simulate-button').click();
  await page.locator('#results').waitFor({state:'visible',timeout:120000});
  await page.getByText('Details',{exact:true}).first().click();
  const inspector = page.locator('#inspector');
  await inspector.getByRole('tab',{name:'What if'}).click();
  await inspector.getByText('Edit this point').click();
  await inspector.locator('[name=cash_chf]').fill('8100');
  const edited = page.waitForResponse(r => r.url().endsWith('/api/edit-nodes'));
  await inspector.getByRole('button',{name:'Save changes'}).click();
  assert.equal((await edited).status(),200);
  await inspector.locator('#branch-event').fill('Take a new job with CHF 500 higher net monthly income beginning immediately.');
  const proposed = page.waitForResponse(r => r.url().endsWith('/api/branch/propose'), {timeout:120000});
  await inspector.getByRole('button',{name:'Get the numbers'}).click();
  assert.equal((await proposed).status(),200);
  await inspector.getByText('Check the numbers').waitFor({timeout:120000});
  const applied = page.waitForResponse(r => r.url().endsWith('/api/branch/apply'));
  await inspector.getByRole('button',{name:'Create branch'}).click();
  assert.equal((await applied).status(),200);
  await page.locator('#path-comparison').waitFor({state:'visible',timeout:30000});
  console.log('COMPARISON', (await page.locator('#path-comparison').innerText()).slice(0,900));
  await page.getByRole('button',{name:'Sensitivity & checks'}).click();
  await page.locator('#path-report-panel table').waitFor({timeout:60000});
  assert.match(await page.locator('#path-report-panel').innerText(), /model checks/i);
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#download-run').click();
  await (await downloadPromise).saveAs(process.env.RUN_OUTPUT || '/tmp/lauz-browser-run.json');
  for (const width of [1280,375]) {
   await page.setViewportSize({width,height:850});
   assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  assert.deepEqual(errors,[]);
  console.log('PASS live extraction, goal edit, simulation, cash edit, live branch proposal and apply, comparison, sensitivity, download, responsive width and page errors');
 } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode=1; });
