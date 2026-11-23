import {chromium} from "playwright-core";
import {execFileSync} from "node:child_process";
import {mkdirSync,statSync} from "node:fs";
import {resolve} from "node:path";

const origin=process.env.GRAFANA_URL||"http://127.0.0.1:3001";
const password=execFileSync("docker",["compose","exec","-T","grafana","cat","/var/lib/grafana/admin-password"],{encoding:"utf8"}).trim();
const browser=await chromium.launch({channel:process.env.BROWSER_CHANNEL||"msedge",headless:true});
try {
    const ctx=await browser.newContext({viewport:{width:1440,height:1280}});
    const login=await ctx.request.post(origin+"/login",{data:{user:"admin",password}});
    if(!login.ok())throw new Error("Grafana login failed");
    const page=await ctx.newPage();
    const end=Math.round(statSync(".local/release/load.json").mtimeMs);
    await page.goto(origin+"/d/payments-local/payment-operations?kiosk&from="+(end-300000)+"&to="+end);
    await page.getByText("Kafka consumer lag",{exact:true}).first().waitFor();
    await page.locator("canvas").first().waitFor();
    for (const legend of ["Quarantined","Fallbacks / min","Age","p95","Accepted"])
        await page.getByText(legend,{exact:true}).first().waitFor();
    await page.evaluate(()=>document.fonts.ready);
    mkdirSync("docs/release",{recursive:true});
    await page.screenshot({path:resolve("docs/release/dashboard.png"),fullPage:true});
    console.log("Saved authenticated Grafana dashboard");
}finally{await browser.close()}
