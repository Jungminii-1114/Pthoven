import test from 'node:test';
import assert from 'node:assert/strict';
import {RECORDING_PROTOCOL,recordingCue} from '../web/tap-recording-protocol.mjs';

test('recording gives the player preparation and a full return tail after ten seconds',()=>{
  assert.equal(RECORDING_PROTOCOL.prepare_ms,1000);
  assert.equal(RECORDING_PROTOCOL.play_ms,10000);
  assert.equal(RECORDING_PROTOCOL.return_ms,1000);
  assert.equal(RECORDING_PROTOCOL.total_ms,12000);
  assert.equal(recordingCue(999).phase,'prepare');
  assert.equal(recordingCue(1000).phase,'play');
  assert.equal(recordingCue(10999).phase,'play');
  assert.equal(recordingCue(11000).phase,'return');
  assert.equal(recordingCue(11999).phase,'return');
  assert.equal(recordingCue(12000).phase,'complete');
});
