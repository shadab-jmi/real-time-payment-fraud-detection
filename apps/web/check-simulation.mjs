import { chromium } from "playwright-core";
import assert from "node:assert/strict";

const base = process.env.WEB_URL || "http://127.0.0.1:3000";
const browser = await chromium.launch({channel:"msedge"});
try {
  const owner = await browser.newContext({baseURL:base});
  const stranger = await browser.newContext({baseURL:base});
  const page = await owner.newPage();
  await page.goto("/");
  await page.getByText("Try the payment simulator").click();
  await page.getByLabel("Payments",{exact:true}).fill("10");
  await page.getByRole("button",{name:"Start simulation"}).click();
  await page.waitForURL(/run_id=/);
  const run = new URL(page.url()).searchParams.get("run_id");
  const resource = "/v1/simulations/"+run;
  const headers = {Origin:base};
  assert.equal((await stranger.request.get(resource)).status(),404);
  assert.equal((await stranger.request.post(resource+"/stop",{headers})).status(),404);
  assert.equal((await stranger.request.post("/v1/simulations",{headers,data:{scenario:"mixed",count:1,rate:1}})).status(),409);
  await page.getByRole("heading",{name:"Completed"}).waitFor();
  const report = await (await owner.request.get(resource)).json();
  assert.equal(report.generated,10);
  assert.equal(report.processed,report.approved+report.reviewed+report.blocked);
  assert.equal(report.processed,10);
  const cells = page.locator(".sim-metrics section");
  const positions = () => cells.evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().top));
  assert.equal(new Set(await positions()).size,1);
  const paymentLink = page.getByRole("link",{name:report.items[0].id});
  assert.ok((await paymentLink.innerText()).length < report.items[0].id.length);
  await paymentLink.click();
  await page.getByRole("heading",{name:"Model explanation"}).waitFor();
  const evidence = await (await owner.request.get(resource+"/transactions/"+report.items[0].id)).json();
  const percent = v=>v.toLocaleString("en-US",{style:"percent",maximumFractionDigits:2});
  assert.equal(await page.locator(".score-heading strong").innerText(),percent(evidence.score));
  assert.equal((await page.locator(".score-heading .pill").textContent()),evidence.action);
  assert.equal(await page.locator(".risk-track b").evaluate(n=>parseFloat(n.style.left).toFixed(3)),(100*evidence.score).toFixed(3));
  for (const threshold of [evidence.review_threshold,evidence.block_threshold])
    assert.ok((await page.locator(".thresholds").innerText()).includes(percent(threshold)));
  const maximum = Math.max(...evidence.contributions.map(v=>Math.abs(v.signed_value)));
  for(const v of evidence.contributions){
    const row = page.locator('[aria-describedby="tip-'+v.feature_name+'"]');
    await row.focus();
    const tooltip = await row.getByRole("tooltip").innerText();
    assert.ok(tooltip.includes(String(v.feature_value)) && tooltip.includes(String(v.signed_value)));
    assert.ok(!(await row.locator("span").first().innerText()).includes(v.feature_name));
    const style = await row.locator(".scale i").evaluate(n=>({width:parseFloat(n.style.width),left:n.style.left,right:n.style.right,color:getComputedStyle(n).backgroundColor}));
    assert.ok(Math.abs(style.width-50*Math.abs(v.signed_value)/maximum)<0.001);
    assert.equal(v.signed_value<0?style.right:style.left,"50%");
    assert.equal(style.color,v.signed_value<0?"rgb(52, 105, 160)":"rgb(17, 107, 96)");
  }
  assert.equal(await page.locator(".technical").getAttribute("open"),null);
  await page.getByText("Technical details",{exact:true}).click();
  assert.equal(await page.locator(".technical tbody tr").count(),12);
  await page.setViewportSize({width:375,height:800});
  assert.ok(new Set(await positions()).size>1);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.setViewportSize({width:1280,height:800});
  assert.equal(await page.locator(".factor").count(),12);
  assert.equal((await stranger.request.get(resource+"/transactions/"+report.items[0].id)).status(),404);
  await page.getByRole("combobox").first().selectOption("mixed");
  await page.getByRole("button",{name:"Start simulation"}).click();
  await page.waitForURL(url=>url.searchParams.get("run_id")!==run);
  const running = new URL(page.url()).searchParams.get("run_id");
  await page.getByLabel("Simulation run").selectOption(run);
  await page.getByRole("heading",{name:"Completed"}).waitFor();
  assert.ok(await page.getByRole("button",{name:"Stop simulation"}).isDisabled());
  await page.getByLabel("Simulation run").selectOption(running);
  await page.getByRole("button",{name:"Stop simulation"}).click();
  await page.getByRole("heading",{name:"Stopped"}).waitFor();
  await page.getByLabel("Simulation run").selectOption(run);
  await page.getByRole("heading",{name:"Completed"}).waitFor();
  console.log("Passed browser start, completion, explanations, isolation, stop and history",run);
} finally {
  await browser.close();
}
