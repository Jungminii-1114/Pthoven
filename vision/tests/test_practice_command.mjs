import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const root = fileURLToPath(new URL('..', import.meta.url));
const pythonEnv = { ...process.env, PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter) };
const py = (code, input) => JSON.parse(execFileSync('python', ['-c', code], { env: pythonEnv, input, encoding: 'utf8' }));
// The ticket is produced by the actual S-09..S-13 Python implementation.
const setup = `
import json
from test_coaching import fixture, scripted
from pthoven_vision.coaching import run_b04, dump
from pthoven_vision.practice_commands import CommandJournal, CommandContext, issue_practice_command
c, live, clock, trigger = fixture()
scripted(c, [('set_tempo', {'bpm': 60, 'reason': 'test'}), ('set_practice_range', {'start_index': 0, 'end_index': 3, 'reason': 'test'})])
plan = run_b04(trigger, c)['data']
j = CommandJournal(':memory:')
commands = CommandContext(j, c.action_policy, lambda: live, lambda t: None, lambda q: None, now=lambda: clock[0])
ticket = issue_practice_command(plan, commands)['data']
`;
const fixture = py(setup + "print(json.dumps({'ticket': ticket, 'state': dump(live), 'policy': dump(c.action_policy)}))");
const clone = (v) => JSON.parse(JSON.stringify(v));

function client({ commit, save, restored, state } = {}) {
  const context = {};
  vm.runInNewContext(fs.readFileSync(new URL('../web/practice-command.js', import.meta.url), 'utf8'), context);
  let time = 100;
  let applied = 0;
  let stored;
  const api = context.PthovenPracticeCommand.createClient({ state: state || fixture.state,
    policy: fixture.policy, now: () => time, restored,
    applyAtomic: commit || (() => { applied += 1; }),
    save: save || ((value) => { stored = value; }) });
  return { api, tick: (t) => { time = t; }, applied: () => applied, stored: () => stored };
}
const envelope = (extra = {}) => ({ clock_id: 'browser1', expires_at_ms: 1000,
  boundary: 'next_segment', effective_attempt_ref: 'a2', ...extra });

test('S-09..S-15 round trip uses the real Python ticket and reconciles the browser ACK', () => {
  const c = client();
  const out = c.api.apply_command_and_ack(fixture.ticket, envelope());
  assert.equal(out.outcome, 'applied');
  assert.equal(out.data.applied_values.bpm, 60);
  assert.equal(out.data.applied_values.end_index, 3);
  assert.equal(out.data.actual_versions.config_version, 2);
  const reconciled = py(setup + `
import sys
from pthoven_vision.practice_commands import reconcile_intervention
ack = json.load(sys.stdin)
ack['command_id'] = ticket['command_id']
print(json.dumps(reconcile_intervention({'command_id': ticket['command_id'], 'observation': ack}, commands)))
`, JSON.stringify(out.data));
  assert.equal(reconciled.outcome, 'intervention_confirmed');
  assert.equal(reconciled.data.actual_values.bpm, 60);
  assert.equal(reconciled.data.effective_attempt_ref, 'a2');
});

test('waits for the promised boundary and preserves the original expiry on redelivery', () => {
  const c = client();
  const waiting = c.api.apply_command_and_ack(fixture.ticket, envelope({ boundary: null }));
  assert.equal(waiting.outcome, 'wait_for_boundary');
  assert.equal(c.applied(), 0);
  c.tick(1500);
  const expired = c.api.apply_command_and_ack(fixture.ticket, envelope({ expires_at_ms: 9999 }));
  assert.equal(expired.outcome, 'rejected');
  assert.equal(expired.data.reason_code, 'EXPIRED_BEFORE_APPLY');
  assert.equal(c.api.snapshot().actual_config.bpm, 80);
});

test('duplicates, including after restart, return the original ACK with no second application', () => {
  const c = client();
  const first = c.api.apply_command_and_ack(fixture.ticket, envelope());
  c.tick(5000);
  const again = c.api.apply_command_and_ack(fixture.ticket, envelope());
  assert.equal(again.outcome, 'duplicate_ack');
  assert.deepEqual(clone(again.data), clone(first.data));
  assert.equal(c.applied(), 1);
  const recovered = client({ restored: c.stored(), state: c.api.snapshot() });
  assert.equal(recovered.api.apply_command_and_ack(fixture.ticket, envelope()).outcome, 'duplicate_ack');
  assert.equal(recovered.applied(), 0);
});

test('one invalid setting rejects the whole change_set without partial UI mutation', () => {
  const c = client(), ticket = clone(fixture.ticket);
  ticket.change_set[1].arguments.end_index = 100;
  const out = c.api.apply_command_and_ack(ticket, envelope());
  assert.equal(out.outcome, 'rejected');
  assert.equal(c.applied(), 0);
  assert.equal(c.api.snapshot().actual_config.bpm, 80);
  assert.equal(c.api.snapshot().actual_config.end_index, 7);
});

test('manual stop invalidates the old epoch and wins over agent commands', () => {
  const c = client();
  c.api.apply_command_and_ack(fixture.ticket, envelope({ boundary: null }));
  c.api.manualChange((next) => { next.phase = 'paused'; });
  const out = c.api.apply_command_and_ack(fixture.ticket, envelope());
  assert.equal(out.outcome, 'rejected');
  assert.equal(out.data.reason_code, 'STALE_EPOCH');
  assert.equal(c.api.snapshot().phase, 'paused');
  assert.equal(c.api.snapshot().actual_config.bpm, 80);
});

test('reconnect, foreign session, changed calibration and clock are rejected', () => {
  for (const mutate of [
    (s) => { s.session_id = 'other'; },
    (s) => { s.versions.command_epoch += 1; },
    (s) => { s.versions.calibration_version += 1; },
    (s) => { s.clock_id = 'new-browser-clock'; },
  ]) {
    const state = clone(fixture.state); mutate(state);
    const c = client({ state });
    assert.equal(c.api.apply_command_and_ack(fixture.ticket, envelope()).outcome, 'rejected');
    assert.equal(c.applied(), 0);
  }
});

test('a crash during the UI commit is unknown and cannot be replayed after restart', () => {
  const c = client({ commit: () => { throw new Error('UI interrupted'); } });
  const out = c.api.apply_command_and_ack(fixture.ticket, envelope());
  assert.equal(out.outcome, 'application_unknown');
  const recovered = client({ restored: c.stored() });
  assert.equal(recovered.api.apply_command_and_ack(fixture.ticket, envelope()).outcome, 'application_unknown');
  assert.equal(recovered.applied(), 0);
  assert.equal(recovered.api.probe(fixture.ticket.command_id).command_ack, null);
  const other = clone(fixture.ticket); other.command_id = 'another-command';
  assert.equal(recovered.api.apply_command_and_ack(other, envelope()).outcome, 'wait_for_boundary');
});

test('malformed commands and storage failures are NodeResult errors', () => {
  const c = client();
  const out = c.api.apply_command_and_ack({}, envelope());
  assert.equal(out.status, 'error');
  assert.equal(out.data, null);
  const offline = client({ save: () => { throw new Error('quota exceeded'); } });
  assert.equal(offline.api.apply_command_and_ack(fixture.ticket, envelope()).outcome, 'application_unknown');
  assert.equal(offline.applied(), 0);
});

test('ACK loss is resolved by a read-only command-specific probe', () => {
  const c = client();
  const ack = c.api.apply_command_and_ack(fixture.ticket, envelope()).data;
  assert.deepEqual(clone(c.api.probe(fixture.ticket.command_id).command_ack), clone(ack));
  assert.equal(c.api.probe('unknown').command_ack, null);
  assert.equal(c.applied(), 1);
});

test('same command ID with modified payload is never re-applied', () => {
  const c = client();
  c.api.apply_command_and_ack(fixture.ticket, envelope());
  const altered = clone(fixture.ticket); altered.change_set[0].arguments.bpm = 65;
  assert.equal(c.api.apply_command_and_ack(altered, envelope()).status, 'error');
  assert.equal(c.applied(), 1);
});

test('resume requires real user intent and valid observation; reason text is insufficient', () => {
  const state = clone(fixture.state); state.phase = 'paused';
  const ticket = clone(fixture.ticket); ticket.expected_phase = 'paused'; ticket.apply_at = 'paused';
  ticket.change_set = [ticket.change_set[0]];
  ticket.change_set[0].operation = 'resume_practice';
  ticket.change_set[0].arguments = { reason: 'User wants to resume' };
  const c = client({ state });
  assert.equal(c.api.apply_command_and_ack(ticket, envelope()).data.reason_code, 'RESUME_NOT_AUTHORIZED');
  state.user_resume_requested = true;
  const allowed = client({ state });
  const out = allowed.api.apply_command_and_ack(ticket, envelope());
  assert.equal(out.outcome, 'applied');
  assert.equal(out.data.actual_versions.command_epoch, 2);
});
