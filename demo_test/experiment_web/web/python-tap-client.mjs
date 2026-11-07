// Camera/UI transport only. Geometry, calibration and candidate decisions live in Python.
export const FINGERS={thumb:[1,2,3,4],index:[5,6,7,8],middle:[9,10,11,12],ring:[13,14,15,16],pinky:[17,18,19,20]};
export async function tapAPI(path,payload){
  const response=await fetch(`/api/tap/${path}`,{method:payload===undefined?'GET':'POST',headers:payload===undefined?{}:{'Content-Type':'application/json'},body:payload===undefined?undefined:JSON.stringify(payload),signal:AbortSignal.timeout(4000)});
  const data=await response.json();if(!response.ok)throw Error(data.error||`Python HTTP ${response.status}`);return data;
}
export class PythonTapClient{
  constructor({send=tapAPI,onFrame=()=>{},onError=()=>{},onStatus=()=>{}}={}){
    Object.assign(this,{send,onFrame,onError,onStatus});this.generation=0;this.session=null;this.queue=[];this.pending=false;this.timer=null;this.sequence=0;this.dropped=0;this.engineRevision=null;
  }
  async reset(calibration={}, {mode='fixed'}={}){
    const token=++this.generation,old=this.session;this.session=null;this.queue=[];this.sequence=0;this.dropped=0;this.engineRevision=null;clearTimeout(this.timer);this.timer=null;
    if(old)void this.send('close',{session_id:old}).catch(()=>{});
    if(!Object.keys(calibration).length)return true;
    this.onStatus('Python 판정 세션 준비 중');
    const result=await this.send('session',{calibration,mode});
    if(token!==this.generation){void this.send('close',{session_id:result.session_id}).catch(()=>{});return false;}
    if(mode!=='fixed'&&result.mode!==mode){void this.send('close',{session_id:result.session_id}).catch(()=>{});throw Error('새 판정 방식을 사용하려면 Python 서버를 재시작하세요.');}
    this.session=result.session_id;this.engineRevision=result.engine_revision??null;this.onStatus('Python 판정 연결됨 · TIP + 관절각');return true;
  }
  offer(frame,context=null){
    if(!this.session)return;
    this.queue.push({frame,context});if(this.queue.length>64){this.queue.shift();this.dropped++;this.onStatus(`Python 입력 대기 초과 · 누적 ${this.dropped}프레임 제외`);}
    if(!this.pending&&this.queue.length>=3)void this.flush();
    else if(!this.timer)this.timer=setTimeout(()=>{this.timer=null;void this.flush();},35);
  }
  async flush(){
    clearTimeout(this.timer);this.timer=null;if(this.pending||!this.session||!this.queue.length)return;
    const rows=this.queue.splice(0,128),token=this.generation,session=this.session,sequence=++this.sequence;
    this.pending=true;
    try{
      const result=await this.send('frames',{session_id:session,sequence,frames:rows.map(r=>r.frame)});
      if(token!==this.generation)return;
      for(let i=0;i<result.frames.length;i++){
        if(result.frames[i].t_ms!==rows[i]?.frame.t_ms)throw Error('Python 응답 프레임 시각이 다릅니다.');
        this.onFrame(result.frames[i],rows[i].context,{received_ms:performance.now(),processing_ms:result.processing_ms,input_frame:rows[i].frame});
      }
    }catch(error){if(token===this.generation){this.queue=[];this.session=null;this.onError(error);}}
    finally{this.pending=false;if(this.queue.length)void this.flush();}
  }
  async drain(){
    const deadline=performance.now()+5000;
    while(this.pending||this.queue.length){
      if(performance.now()>deadline)throw Error('Python 판정 응답 대기 시간 초과');
      await this.flush();await new Promise(r=>setTimeout(r,15));
    }
  }
}

// Preparation and transport buffering only; all geometric validation is Python.
export class RawCalibrationCapture{
  constructor({kind,finger,start}){Object.assign(this,{kind,finger,start});this.frames=[];this.rejected=0;this.first=null;this.last=null;}
  observe(t,world,image){
    if(t<this.start+1500)return;
    if(!world){this.rejected++;return;}
    if(this.last!==null&&t-this.last>500){this.frames=[];this.first=null;}
    this.first??=t;this.last=t;this.frames.push({t_ms:t,world_landmarks:world,landmarks:image});
  }
  status(t){
    const span=this.first===null?0:this.last-this.first,ready=this.frames.length>=12&&span>=1600&&t-this.last<=500;
    return {phase:t<this.start+1500?'preparing':ready?'complete':t-this.start>=8000?'timeout':'collecting',
      prepare_remaining_ms:Math.max(0,this.start+1500-t),frames:this.frames.length,min_frames:12,span_ms:span,rejected:this.rejected,restarts:0};
  }
}
