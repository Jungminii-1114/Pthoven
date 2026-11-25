import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';

test('standalone feature replay rebuilds coverage and preserves missing truth and synthetic source',()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pthoven-depth-cli-'));
  try{
    const file=path.join(dir,'fixture.json');
    const generate=fileURLToPath(new URL('../scripts/make_depth_fixture.mjs',import.meta.url));
    const replay=fileURLToPath(new URL('../scripts/replay_depth.mjs',import.meta.url));
    execFileSync(process.execPath,[generate,file],{cwd:dir});
    const input=JSON.parse(fs.readFileSync(file));
    // Deliberately stale validity: recomputation must not reuse these flags.
    for(const row of [...input.trials[0].pose_frames,...input.trials[0].depth_frames])row.signals={};
    fs.writeFileSync(file,JSON.stringify(input));
    const result=JSON.parse(execFileSync(process.execPath,[replay,file],{cwd:dir}));
    const t=result.trials[0];
    assert.equal(t.original_source,'synthetic_analytic_fixture');
    assert.equal(t.evaluation.labelled,false);
    for(const method of ['pose_full','pose_matched','depth','fusion']){
      assert.equal(t.predictions[method].length,1);
      assert.equal(t.evaluation.methods[method].f1,null);
      assert.ok(t.evaluation.methods[method].coverage_ratio>0.5);
    }
    assert.match(t.latency_source,/reconstructed/);
  }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
