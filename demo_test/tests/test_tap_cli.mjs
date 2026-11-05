import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {calibrate} from '../models/tap-detector.mjs';
import {hand,cycle} from './tap-fixtures.mjs';

test('saved trace replays with no invented ground truth or real-data attribution',()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ptoven-tap-test-'));
  const input=path.join(dir,'capture.json'),output=path.join(dir,'result.json');
  const base={calibration:{index:calibrate(Array.from({length:8},()=>hand()),Array.from({length:8},()=>hand({index:1})),'index')},
    frames:cycle().map(r=>({t_ms:r.t_ms,world_landmarks:r.landmarks})),duration_ms:275,person_id:'synthetic',session_id:'fixture',scenario:'single_tap'};
  fs.writeFileSync(input,JSON.stringify({schema_version:1,evidence_type:'synthetic_fixture',trials:[{...base,truth:null},{...base,truth:[{finger:'index',t_ms:200}]}]}));
  const script=fileURLToPath(new URL('../scripts/check_tap.mjs',import.meta.url));
  execFileSync(process.execPath,[script,'--capture',input,'--output',output]);
  const report=JSON.parse(fs.readFileSync(output));
  assert.equal(report.evidence_type,'synthetic_or_unverified_replay');
  assert.equal(report.trials[0].metrics.status,'ground_truth_missing');
  assert.equal(report.trials[0].metrics.f1,null);
  assert.equal(report.trials[1].metrics.tp,1);
  assert.equal(report.trials[1].predictions[0].finger,'index');
});
