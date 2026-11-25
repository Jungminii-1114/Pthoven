import test from 'node:test';
import assert from 'node:assert/strict';

test('lab keeps native video and connection alive when optional model import fails',async()=>{
  // Browser API doubles: this exercises wiring, not hardware or actual pixels.
  const ids=new Map();let stopped=0,canvasVideoDraws=0;
  const context={clearRect(){},fillRect(){},fillText(){},beginPath(){},stroke(){},drawImage(){canvasVideoDraws++;}};
  const el=id=>{
    if(!ids.has(id))ids.set(id,{value:'',checked:false,disabled:false,textContent:'',dataset:{},style:{},
      replaceChildren(...children){this.children=children;},setAttribute(name,value){this[name]=value;},getContext:()=>context});
    return ids.get(id);
  };
  const stream={getTracks:()=>[track],getVideoTracks:()=>[track]};
  const track={label:'iPhone Camera',readyState:'live',getSettings:()=>({deviceId:'phone'}),stop(){stopped++;},addEventListener(){}};
  Object.assign(el('video'),{play:async()=>{},readyState:2,videoWidth:1920,videoHeight:1080,currentTime:1,getVideoPlaybackQuality:()=>({totalVideoFrames:1})});
  el('handSide').value='Right';el('mirror').checked=true;
  const replacements={document:{getElementById:el,hidden:false},
    localStorage:{values:new Map(),getItem(k){return this.values.get(k)??null;},setItem(k,v){this.values.set(k,v);}},
    navigator:{mediaDevices:{getUserMedia:async()=>stream,enumerateDevices:async()=>[{kind:'videoinput',label:'iPhone Camera',deviceId:'phone'}],addEventListener(){}}},
    window:{addEventListener(){}},location:{search:''},Option:class{constructor(text,value){this.text=text;this.value=value;}},
    requestAnimationFrame:()=>1,cancelAnimationFrame:()=>{},
    fetch:async url=>{if(!['/api/tap/status','/api/tap/calibrate'].includes(url))throw Error('Unexpected endpoint '+url);return {ok:true,json:async()=>url.endsWith('/status')?{algorithm:'python_tip_angles_v1'}:{ok:true,diagnostics:{algorithm:'python_tip_angles_v1'},diagnostic_text:'Python 보정 진단 · 웹 전송 계층 테스트'}};}};
  const old={};for(const [k,v] of Object.entries(replacements)){old[k]=Object.getOwnPropertyDescriptor(globalThis,k);Object.defineProperty(globalThis,k,{value:v,configurable:true,writable:true});}
  try{
    await import('../web/tap-lab.mjs');
    assert.equal(el('tapMode').value,'local_baseline');
    assert.match(el('modeStatus').textContent,/상대 기준/);
    el('tapMode').value='fixed';await el('tapMode').onchange();
    assert.equal(replacements.localStorage.getItem('pthoven.tap-lab.mode.v1'),'fixed');
    assert.match(el('modeStatus').textContent,/고정 기준/);
    await el('connect').onclick();
    assert.equal(el('tapMode').value,'fixed','camera connection preserves explicit mode selection');
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(el('video').srcObject,stream);
    assert.match(el('activeCamera').textContent,/iPhone/);
    assert.match(el('cameraState').textContent,/영상 수신 중/);
    assert.match(el('trackerState').textContent,/사용 불가/);
    assert.equal(el('cameraPlaceholder').hidden,true);
    assert.equal(stopped,0);
    assert.equal(canvasVideoDraws,0,'preview must use native video, not model-dependent canvas copies');
    // Re-analysis is offline evidence and must not silently install a live model.
    const {hand}=await import('./tap-fixtures.mjs');
    const diagnostic={kind:'pthoven_calibration_diagnostics',algorithm:'score_axis_quality_v3',attempts:[{
      kind:'lift',finger:'index',frames:Array.from({length:12},()=>hand({index:1})),rest_frames:Array.from({length:12},()=>hand())}]};
    const file={size:20000,text:async()=>JSON.stringify(diagnostic)};
    await el('importCalibration').onchange({target:{files:[file]}});
    assert.match(el('calibrationDiagnostics').textContent,/Python 품질 조건 통과/);
    assert.match(el('calibrationDiagnostics').textContent,/현재 카메라의 보정은 변경하지/);
    assert.equal(el('calibrated').textContent,'보정된 손가락: 없음');
    await el('importCalibration').onchange({target:{files:[{size:2,text:async()=>'{}'}]}});
    assert.match(el('status').textContent,/보정 진단 JSON이 아닙니다/);
    assert.equal(stopped,0,'reanalysis must not interrupt the connected camera');
    el('disconnect').onclick();assert.equal(stopped,1);assert.equal(el('video').srcObject,null);
  }finally{
    for(const k of Object.keys(replacements)){if(old[k])Object.defineProperty(globalThis,k,old[k]);else delete globalThis[k];}
  }
});
