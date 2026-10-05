"use strict";
// Synthetic frames only. Output stays outside Git; no household evidence.
const {chromium}=require("playwright"), fs=require("node:fs"), path=require("node:path");
const {createFixture}=require("./am1_console_layout_fixture.cjs");
const args=process.argv.slice(2), startupApproval=args.includes("--startup-approval");
const [output, label="after", source]=args.filter(arg=>arg!=="--startup-approval");
(async()=>{
  const {server,fixture}=createFixture(source);
  if (startupApproval) fixture.snapshot={session_id:"synthetic-approval", phase:"host_ready", telemetry:{},
    pending_gate:["live_start",null], input_pause:{reason:"window-blur"},
    progress:{startup:{step:7,stage:"final_readiness",waiting:true}}};
  await new Promise(r=>server.listen(0,"127.0.0.1",r));
  const browser=await chromium.launch({headless:true,channel:"msedge"});
  try {
    fs.mkdirSync(output,{recursive:true});
    for(const [name,width,height] of [["half",767,786],["full",1536,794]]) {
      const page=await browser.newPage({viewport:{width,height},deviceScaleFactor:1.25});
      await page.goto(`http://127.0.0.1:${server.address().port}/`);
      fixture.jpeg=Buffer.from(await page.evaluate(()=>{
        const c=document.createElement("canvas");c.width=640;c.height=480;
        const x=c.getContext("2d");x.fillStyle="#203b45";x.fillRect(0,0,640,480);
        x.strokeStyle="#508b92";for(let n=0;n<640;n+=40){x.beginPath();x.moveTo(n,0);x.lineTo(n,480);x.stroke();}
        x.fillStyle="#e4ede9";x.font="30px system-ui";x.fillText("Synthetic camera fixture",110,245);
        return c.toDataURL("image/jpeg").split(",")[1];
      }),"base64");
      await page.waitForFunction(()=>document.querySelector("#primary").classList.contains("fresh"));
      await page.waitForTimeout(650);
      await page.screenshot({path:path.join(output,`${label}-${name}.png`),fullPage:false});
      console.log(JSON.stringify({label,name,...await page.evaluate(()=>({width:innerWidth,height:innerHeight,
        scrollHeight:document.documentElement.scrollHeight,scrollWidth:document.documentElement.scrollWidth,
        devicePixelRatio,primary:document.querySelector("#primary").getBoundingClientRect().toJSON(),
        bodyControls:document.querySelector(".body-pad").getBoundingClientRect().toJSON()}))}));
      await page.close();
    }
  } finally {await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
})().catch(err=>{console.error(err);process.exitCode=1;});
