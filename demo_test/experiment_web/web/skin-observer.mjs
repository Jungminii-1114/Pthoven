// One pair at a time; old camera/hand/trial results cannot enter a new epoch.
export class SkinObserver {
  constructor({send, onResult, onStatus, interval=120, maxGap=250}) {
    Object.assign(this,{send,onResult,onStatus,interval,maxGap});
    this.enabled=false;this.epoch=0;this.pending=false;this.previous=null;
  }
  reset(){this.epoch++;this.previous=null;}
  enable(value){this.enabled=value;this.reset();}
  offer(makePatch,t,context=null){
    if(!this.enabled||this.pending)return;
    if(this.previous&&t-this.previous.t<this.interval)return;
    const patch=makePatch();
    if(!patch){this.reset();this.onStatus('손등 영역을 충분히 보여주세요.');return;}
    const previous=this.previous;this.previous={patch,t};
    if(!previous||t-previous.t>this.maxGap){this.onStatus('다음 손등 프레임을 기다립니다.');return;}
    const epoch=this.epoch;this.pending=true;
    const payload={previous_patch:previous.patch,patch,previous_t_ms:previous.t,t_ms:t};
    Promise.resolve().then(()=>this.send(payload)).then(result=>{
      if(this.enabled&&epoch===this.epoch)this.onResult(result,context,payload);
    }).catch(error=>{
      if(this.enabled&&epoch===this.epoch){this.previous=null;this.onStatus(`손등 분석 보류: ${error.message}`);}
    }).finally(()=>{this.pending=false;});
  }
}
