// Runs a Guild KERNEL script the way KERNEL's playwright/execute does: the code is
// the body of an async function with `page`, `context` and `browser` in scope,
// and its return value is the result. Used by tests/test_kernel.py with local
// Chromium to prove the script works on the real vendor page.
// Usage: node kernel_script_runner.cjs < code.js   (prints the result as JSON)
const { execSync } = require('child_process');
let playwright;
try { playwright = require('playwright'); } catch {
  playwright = require(execSync('npm root -g').toString().trim() + '/playwright');
}

(async () => {
  const code = require('fs').readFileSync(0, 'utf8');
  const browser = await playwright.chromium.launch();
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    const result = await new AsyncFunction('page', 'context', 'browser', code)(page, context, browser);
    process.stdout.write(JSON.stringify({ success: true, result }));
  } catch (err) {
    process.stdout.write(JSON.stringify({ success: false, error: String(err && err.message || err) }));
  } finally {
    await browser.close();
  }
})();
