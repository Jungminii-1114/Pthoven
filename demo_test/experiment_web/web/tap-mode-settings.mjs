// This is a UI preference; calibration JSON and recorded mode are not rewritten.
export const TAP_MODE_KEY='pthoven.tap-lab.mode.v1';
export const DEFAULT_TAP_MODE='local_baseline';
export const TAP_MODE_LABELS=Object.freeze({local_baseline:'상대 기준 · 개선 모드',fixed:'고정 기준 · 비교 모드'});
function browserStorage(){try{return globalThis.localStorage??null;}catch{return null;}}
export function loadTapMode(storage=browserStorage()){
  try{const mode=storage?.getItem(TAP_MODE_KEY);return Object.hasOwn(TAP_MODE_LABELS,mode)?mode:DEFAULT_TAP_MODE;}
  catch{return DEFAULT_TAP_MODE;}
}
export function saveTapMode(mode,storage=browserStorage()){
  if(!Object.hasOwn(TAP_MODE_LABELS,mode))return false;
  try{if(!storage)return false;storage.setItem(TAP_MODE_KEY,mode);return true;}
  catch{return false;}
}
