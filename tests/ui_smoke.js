// Optional dev test: drives the REAL page in a real browser (Chromium via Playwright), against the demo AIs.
// Needs Node 18+ and Playwright:  npm install playwright   (then  npx playwright install chromium  once)
// Terminal 1:  python aibridge/app.py --demo --no-browser --port 8791      (use a fresh workspace: set BRIDGE_WORKSPACE to an empty folder)
// Terminal 2:  node tests/ui_smoke.js http://127.0.0.1:8791
const { chromium } = require("playwright");
const BASE = process.argv[2] || "http://127.0.0.1:8791";
let passed = 0;
const errors = [];
function check(name, cond, extra) {
  if (!cond) { console.log("FAIL ", name, extra === undefined ? "" : JSON.stringify(extra)); process.exitCode = 1; throw new Error("failed: " + name); }
  passed++; console.log("PASS ", name);
}

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1400, height: 900 } });
  p.on("pageerror", e => errors.push(e.message));
  const text = sel => p.evaluate(s => (document.querySelector(s) || {}).textContent || "", sel);
  const until = (fn, ms = 20000, arg) => p.waitForFunction(fn, arg, { timeout: ms });
  try {
    // 1. first open: the New session sheet, with seats, styles, safety and playbooks
    await p.goto(BASE + "/");
    await until(() => document.querySelector("#newsession"), 8000);
    check("an empty app opens the New session sheet", true);
    check("header shows the DEMO badge and Idle", (await text("#hdr")).includes("DEMO") && (await text("#status")).includes("Idle"));
    check("five styles and Ask me first as the default safety mode", await p.evaluate(() =>
      document.querySelectorAll("#newsession input[name=style]").length === 5 && document.querySelector("#newsession input[name=mode][value=approval]").checked));
    await until(() => document.querySelectorAll("#f-playbook option").length >= 4, 6000);
    check("the playbook picker lists the starters", (await p.$$eval("#f-playbook option", o => o.map(x => x.textContent))).some(t => t.startsWith("Bug hunt")));
    check("each seat has AI, model and name pickers", await p.evaluate(() => ["claude", "chatgpt"].every(s =>
      document.querySelector("#f-ai-" + s) && document.querySelector("#f-model-" + s).options.length > 1 && document.querySelector("#f-name-" + s))));

    // 2. a playbook fills the form; its blank must be filled before it starts
    await p.selectOption("#f-playbook", { index: 1 });  // Build a feature
    await until(() => document.querySelector("#f-blank-0"), 5000);
    check("picking a playbook fills the form (style, turns) and asks for its blank", await p.evaluate(() =>
      document.querySelector("#newsession input[name=style][value=collaboration]").checked && document.querySelector("#f-turns").value === "20"
      && document.querySelector("#f-blank-0").dataset.blank === "feature"));
    await p.click("#btn-start");
    await until(() => [...document.querySelectorAll(".toast")].some(t => t.textContent.includes("Fill in")), 4000);
    check("Start refuses while a blank is empty", !!(await p.$("#newsession")));
    await p.fill("#f-blank-0", "a README");
    await p.fill("#f-turns", "4");
    await p.selectOption("#f-ai-claude", "claude|api"); await p.selectOption("#f-ai-chatgpt", "openai|api");
    await p.waitForTimeout(300);
    await p.click("#btn-start");
    await until(() => !document.querySelector("#newsession"), 8000);
    await until(() => document.querySelectorAll(".msg").length >= 2, 15000);
    check("the session starts with the blank filled into the task", (await text("#tasktext")).startsWith("Build a README."), await text("#tasktext"));
    check("messages show who said it and with which model", await p.evaluate(() => document.querySelectorAll(".msg.claude").length >= 1 && [...document.querySelectorAll(".msg .tag")].some(t => /claude-/.test(t.textContent))));

    // 3. approval card
    await until(() => document.querySelector(".appr"), 20000);
    check("an approval card says who wants what and shows the content",
      (await text(".appr-h")).includes("ChatGPT") && (await text(".appr-h")).includes("write file demo/README.md") && (await text(".appr pre")).includes("Demo project"));
    check("the tab title flags it", (await p.title()).startsWith("(1)"));
    await p.click(".appr .btn.good");
    await until(() => !document.querySelector(".appr"), 8000);
    check("approving removes the card", true);

    // 4. finish -> report card
    await until(() => /Finished/.test(document.querySelector("#status").textContent), 30000);
    check("the session finishes", true);
    await until(() => document.querySelector("#rc-live"), 6000);
    check("a report card appears with what got done", (await text("#rc-live")).includes("Report card") && (await text("#rc-live")).includes("files changed"));
    check("...counting the README ChatGPT wrote", await p.evaluate(() => S.report.files.includes("demo/README.md") && S.report.agents.chatgpt.files === 1));
    await p.click("#rc-close");
    check("the report card can be dismissed", !(await p.$("#rc-live")));
    check("'+6 turns' is offered after finishing", !!(await p.$("#btn-more")));

    // 5. side panel: activity log, last report, files + diff
    await p.evaluate(() => { openTab("activity"); setActSeg("log"); });
    await until(() => document.querySelector("#p-log").textContent.includes("Ship the README"), 6000);
    check("Decisions & log shows the decision and the approval", (await text("#p-log")).includes("approved by human"));
    await p.click("#btn-last-report");
    await until(() => document.querySelector("#modal.on .rc"), 4000);
    check("the last report card opens from the log", true);
    await p.keyboard.press("Escape");
    await p.evaluate(() => openTab("files"));
    await until(() => [...document.querySelectorAll("button.chg-path")].some(x => x.textContent.includes("demo/README.md")), 8000);
    await p.evaluate(() => [...document.querySelectorAll("button.chg-path")].find(x => x.textContent.includes("demo/README.md")).click());
    await until(() => document.querySelector("#modal.on pre"), 6000);
    check("Files > Recent changes opens the change", (await text("#modal pre")).includes("Demo project"));
    await p.keyboard.press("Escape");
    check("Escape closes it", !(await p.evaluate(() => document.querySelector("#modal").classList.contains("on"))));

    // 6. composer: literal text, slash commands
    await p.fill("#say", '<img src=x onerror="window.__pwned=1"> **hi** `code`');
    await p.click("#sendbtn");
    await until(() => [...document.querySelectorAll(".msg.human")].some(m => m.textContent.includes("onerror")), 6000);
    check("your message is shown as text, nothing injected", await p.evaluate(() => { const m = [...document.querySelectorAll(".msg.human")].pop();
      return !m.querySelector("img") && !window.__pwned && m.querySelector("code").textContent === "code"; }));
    await until(() => /Finished|Running|Paused/.test(document.querySelector("#status").textContent), 6000);
    await until(() => /Finished/.test(document.querySelector("#status").textContent), 30000);
    await p.fill("#say", "/help"); await p.keyboard.press("Escape"); await p.click("#sendbtn");
    await until(() => document.querySelector("#modal.on") && document.querySelector("#modal").textContent.includes("/decide"), 6000);
    check("/help lists commands, including /decide /play /report", (await text("#modal")).includes("/play") && (await text("#modal")).includes("/report"));
    await p.keyboard.press("Escape");

    // 7. header controls talk to the API
    await p.selectOption("#sel-mode", "readonly");
    await until(() => S.session.mode === "readonly", 6000);
    check("the safety dropdown applies at once", true);
    await p.selectOption("#sel-mode", "approval");
    await until(() => S.session.mode === "approval", 6000);

    // 8. settings: every section renders, search finds things
    await p.evaluate(() => openSettings("appearance"));
    for (const id of await p.evaluate(() => SP_SECTIONS.map(x => x[0]))) {
      await p.evaluate(i => showSection(i), id); await p.waitForTimeout(150);
      check(`Settings > ${id} renders`, await p.evaluate(i => !document.querySelector("#sec-" + i).hidden && document.querySelector("#sec-" + i).textContent.length > 40, id));
    }
    await p.fill("#sp-search", "discord");
    await p.waitForTimeout(250);
    check("settings search finds Discord", await p.evaluate(() => !document.querySelector("#sec-notify").hidden));
    await p.evaluate(() => closeSettings());

    // 9. the setup wizard (opens by itself on a real first run; demo mode opens it on request)
    check("Settings has 'Run setup again'", !!(await p.$("#spn-setup")) || await p.evaluate(() => { openSettings("appearance"); const ok = !!document.querySelector("#spn-setup"); closeSettings(); return ok; }));
    await p.evaluate(() => openWizard(0));
    await until(() => document.querySelector("#wz-welcome"), 6000);
    await p.click("#wz-next"); await until(() => document.querySelector("#wz-look"), 4000);
    await p.click("#wz-next"); await until(() => document.querySelector("#wz-ais .chip"), 8000);
    check("the wizard walks Welcome > Look > Connect your AIs, and sees both AIs ready in demo mode",
      await p.evaluate(() => document.querySelectorAll("#wz-ais .wz-agents .chip.st-good").length === 2));
    await p.evaluate(() => closeWizard());
    check("the wizard closes", !(await p.$("#wizard")));

    // 10. themes don't break the page
    for (const t of [{ theme: "light", glass: false }, { theme: "dark", glass: true, glass_amount: 60, glass_grad: ["", "", ""] }, { theme: "dark", glass: false }]) {
      await p.evaluate(async x => saveAppearance(x), t); await p.waitForTimeout(300);
    }
    check("switching themes doesn't throw", errors.length === 0, errors);
    check("no page errors at all", errors.length === 0, errors);
    console.log(`\nALL ${passed} UI CHECKS PASSED`);
  } catch (e) {
    console.log("ERROR", e.message);
    if (errors.length) console.log("page errors:", errors);
    process.exitCode = 1;
  } finally {
    await b.close();
  }
})();
