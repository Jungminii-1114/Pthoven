const $=id=>document.getElementById(id);
async function status(){
  const result=await Promise.allSettled([
    fetch('/api/health',{signal:AbortSignal.timeout(4000)}).then(r=>{if(!r.ok)throw Error();return r.json();}),
    fetch('/api/depth/status',{signal:AbortSignal.timeout(4000)}).then(r=>{if(!r.ok)throw Error();return r.json();})
  ]);
  const ready=result[0].status==='fulfilled'&&result[0].value.ok;
  $('health').textContent=ready?'로컬 서버 연결됨':'서버 응답 없음 · 터미널을 확인하세요';$('dot').dataset.ready=String(ready);
  const d=result[1].status==='fulfilled'?result[1].value:null;
  const state=d?.state||d?.status;
  $('depthState').textContent=({ready:'Depth 모델 준비됨',loading:'Depth 모델 로딩 중',not_loaded:'Depth 실험 진입 후 모델 로드',error:'Depth 모델 오류 · 실험 화면에서 확인'})[state]||'Depth 상태 확인 불가';
}
void status();
