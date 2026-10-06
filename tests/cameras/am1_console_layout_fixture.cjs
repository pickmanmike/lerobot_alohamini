"use strict";
// Motor-free loopback fixture. No process launch, SSH, credentials or serial path.
const http = require("node:http"), fs = require("node:fs"), path = require("node:path");
const ROOT = path.resolve(__dirname, "../..");
function createFixture(root = ROOT) {
  const fixture = {snapshot:{phase:"idle", session_id:null, telemetry:{}}, requests:[], jpeg:null,
    camerasAvailable:true, sequence:1, primaryAgeMs:0};
  const roles = ["forward", "backward", "chest", "wrist_left", "wrist_right"];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    res.setHeader("Cache-Control", "no-store");
    if (req.method === "POST") {
      let data = "";
      req.on("data", chunk => { data += chunk; });
      req.on("end", () => {
        const value = JSON.parse(data); fixture.requests.push({path:url.pathname, ...value});
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify({accepted:true, session_id:fixture.snapshot.session_id}));
      });
      return;
    }
    if (url.pathname === "/measure" || process.env.AM1_LAYOUT_MEASURE === "1") {
      res.setHeader("Content-Type", "text/html");
      res.end('<!doctype html><title>AM1 non-actuating viewport measurement</title><h1>Viewport measurement only</h1><p>No robot or camera operation.</p><pre id="metrics"></pre><script>function measure(){document.querySelector("#metrics").textContent=JSON.stringify({innerWidth,innerHeight,outerWidth,outerHeight,devicePixelRatio,screenWidth:screen.width,screenHeight:screen.height,availableWidth:screen.availWidth,availableHeight:screen.availHeight,visualScale:visualViewport.scale},null,2)}measure();addEventListener("resize",measure)</script>');
      return;
    }
    if (url.pathname === "/api/state") {
      res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(fixture.snapshot)); return;
    }
    if (["/camera/api/status", "/camera/status.json"].includes(url.pathname)) {
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({cameras:Object.fromEntries(roles.map((role, index) => [role, {
        state:fixture.camerasAvailable ? "fresh":"unavailable", configured:true,
        sequence:fixture.sequence++, age_ms:0, fps:10, rotation_degrees:[180,180,180,90,270][index],
      }]))})); return;
    }
    if (url.pathname === "/camera/api/frame.jpeg" && fixture.jpeg && fixture.camerasAvailable) {
      res.setHeader("Content-Type", "image/jpeg"); res.setHeader("X-Frame-Sequence", fixture.sequence++);
      res.setHeader("X-Frame-Age-Ms", "0"); res.end(fixture.jpeg); return;
    }
    if (url.pathname === "/camera/api/stream.mjpeg" && fixture.jpeg && fixture.camerasAvailable) {
      res.setHeader("Content-Type", "multipart/x-mixed-replace; boundary=frame");
      const timer = setInterval(() => res.write(Buffer.concat([
        Buffer.from(`--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${fixture.jpeg.length}\r\nX-Frame-Sequence: ${fixture.sequence++}\r\nX-Frame-Age-Ms: ${fixture.primaryAgeMs}\r\n\r\n`), fixture.jpeg, Buffer.from("\r\n")
      ])), 100);
      res.on("close", () => clearInterval(timer)); return;
    }
    const assets = {"/":"tools/am1_console_ui/index.html",
      "/assets/app.js":"tools/am1_console_ui/app.js", "/assets/style.css":"tools/am1_console_ui/style.css",
      ...Object.fromEntries(["style.css", "app.js", "freshness.js", "mjpeg.js"].map(name =>
        ["/camera/assets/"+name, "tools/am1_camera/"+name]))};
    if (assets[url.pathname]) {
      res.setHeader("Content-Type", url.pathname.endsWith(".js") ? "text/javascript" :
        url.pathname.endsWith(".css") ? "text/css" : "text/html");
      res.end(fs.readFileSync(path.join(root, assets[url.pathname]))); return;
    }
    res.statusCode = 503; res.end("Fixture unavailable");
  });
  return {...fixture, server, fixture};
}
module.exports = {createFixture};
if (require.main === module) {
  const {server} = createFixture(process.env.AM1_LAYOUT_SOURCE || ROOT);
  server.listen(Number(process.env.AM1_LAYOUT_PORT || 0), "127.0.0.1", () => console.log(`AM1_LAYOUT_URL=http://127.0.0.1:${server.address().port}`));
}
