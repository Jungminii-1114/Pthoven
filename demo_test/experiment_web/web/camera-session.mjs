// Camera ownership is independent of the optional hand-tracking model.
export const isIPhone = device => /iphone|continuity|아이폰|연속성/i.test(device?.label || '');
export function cameraError(error) {
  return ({NotAllowedError:'카메라 권한이 거부되었습니다. 주소창의 카메라 권한을 허용한 뒤 다시 연결하세요.',
    NotFoundError:'사용 가능한 카메라가 없습니다. iPhone 연속성 카메라 연결을 확인한 뒤 장치를 다시 찾아주세요.',
    NotReadableError:'카메라를 열 수 없습니다. 다른 앱에서 카메라를 사용 중인지 확인하고 다시 연결하세요.',
    OverconstrainedError:'선택한 카메라가 더 이상 사용 가능하지 않습니다. 장치를 다시 찾아 선택하세요.',
    TimeoutError:'카메라 응답을 기다리다 중단했습니다. 권한과 iPhone 연결을 확인한 뒤 다시 연결하세요.'})[error?.name] || error?.message || '카메라 연결에 실패했습니다.';
}
export class CameraSession {
  constructor(mediaDevices, onChange=()=>{}, timeoutMs=20000) {
    this.mediaDevices=mediaDevices;this.onChange=onChange;this.timeoutMs=timeoutMs;
    this.stream=null;this.generation=0;this.phase='idle';this.devices=[];this.lastError=null;
  }
  notify(){this.onChange({phase:this.phase,stream:this.stream,error:this.lastError});}
  async refresh(){
    if(!this.mediaDevices?.enumerateDevices)throw Error('이 브라우저는 카메라 장치 목록을 지원하지 않습니다. localhost에서 Chrome 또는 Safari로 열어주세요.');
    this.devices=(await this.mediaDevices.enumerateDevices()).filter(d=>d.kind==='videoinput');return this.devices;
  }
  stop(){
    this.generation++;const old=this.stream;this.stream=null;
    old?.getTracks().forEach(t=>t.stop());this.phase='idle';this.lastError=null;this.notify();
  }
  async connect(deviceId=''){
    this.stop();const generation=this.generation;this.phase='opening';this.notify();
    let acquired=null,timer;
    try{
      if(!this.mediaDevices?.getUserMedia)throw Error('카메라 API가 없습니다. localhost에서 카메라 지원 브라우저로 열어주세요.');
      const pending=this.mediaDevices.getUserMedia({video:{...(deviceId?{deviceId:{exact:deviceId}}:{}),width:{ideal:1920},height:{ideal:1080}},audio:false});
      // A cancelled/timed-out permission request may resolve late. Release its tracks.
      pending.then(s=>{if(generation!==this.generation)s.getTracks().forEach(t=>t.stop());},()=>{});
      const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>reject(Object.assign(Error('Camera timeout'),{name:'TimeoutError'})),this.timeoutMs);});
      acquired=await Promise.race([pending,timeout]);
      if(generation!==this.generation)return null;
      this.stream=acquired;this.phase='streaming';this.lastError=null;
      const track=acquired.getVideoTracks()[0];
      if(!track||track.readyState==='ended')throw Error('카메라가 영상 트랙을 제공하지 않았습니다.');
      track.addEventListener('ended',()=>{
        if(this.stream!==acquired)return;
        this.stop();this.phase='ended';this.lastError=Error('카메라 연결이 종료되었습니다. iPhone 연결을 확인한 뒤 다시 연결하세요.');this.notify();
      });
      this.notify();return acquired;
    }catch(error){
      if(generation!==this.generation)return null;
      this.generation++;acquired?.getTracks().forEach(t=>t.stop());this.stream=null;
      this.phase='error';this.lastError=error;this.notify();throw error;
    }finally{clearTimeout(timer);}
  }
}
