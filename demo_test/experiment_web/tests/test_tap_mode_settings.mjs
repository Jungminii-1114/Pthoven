import test from 'node:test';
import assert from 'node:assert/strict';
import {DEFAULT_TAP_MODE,TAP_MODE_KEY,loadTapMode,saveTapMode} from '../web/tap-mode-settings.mjs';

const storage=()=>{const values=new Map();return {getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)};};
test('fresh lab selects improved mode and explicit comparison choice survives reload',()=>{
  const s=storage();assert.equal(DEFAULT_TAP_MODE,'local_baseline');assert.equal(loadTapMode(s),'local_baseline');
  assert.equal(saveTapMode('fixed',s),true);assert.equal(loadTapMode(s),'fixed');
  assert.equal(saveTapMode('local_baseline',s),true);assert.equal(loadTapMode(s),'local_baseline');
});
test('invalid or unavailable browser storage cannot select an unknown mode',()=>{
  const s=storage();s.setItem(TAP_MODE_KEY,'unknown');assert.equal(loadTapMode(s),'local_baseline');
  assert.equal(saveTapMode('unknown',s),false);
  const denied={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}};
  assert.equal(loadTapMode(denied),'local_baseline');assert.equal(saveTapMode('fixed',denied),false);
  assert.equal(loadTapMode(null),'local_baseline');assert.equal(saveTapMode('fixed',null),false);
});
