"use strict";
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');

test('optional P1 reader injects CSRF into POST PATCH DELETE and never calls task APIs',async()=>{
  const path='tools/am1_console_ui/p1_view.js'; assert.ok(fs.existsSync(path),'optional P1 reader missing');
  const requests=[];
  const context={globalThis:null,setTimeout,clearTimeout,setInterval,clearInterval,URL,location:{origin:'https://fixture.invalid'},
    fetch:async(url,options)=>{requests.push({url,options});return {ok:true,status:204,headers:{get:()=>null},text:async()=>''};}};
  context.globalThis=context;vm.runInNewContext(fs.readFileSync(path,'utf8'),context);
  const reader=new context.AM1P1View({},()=> 'csrf-fixture',()=>{});
  reader.handle='/api/media/opaque';reader.etag='"tag"';
  await reader.request('/api/media','POST','v=0\r\n','application/sdp');
  await reader.request(reader.handle,'PATCH','a=ice-ufrag:x','application/trickle-ice-sdpfrag',{'If-Match':reader.etag});
  await reader.stop();
  assert.deepEqual(requests.map(r=>r.options.method),['POST','PATCH','DELETE']);
  for(const r of requests){assert.equal(r.options.headers['X-AM1-CSRF'],'csrf-fixture');assert.match(r.url,/^\/api\/media/);}
  assert.equal(requests[1].options.headers['If-Match'],'"tag"');
});
