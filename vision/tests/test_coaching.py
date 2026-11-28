"""B-04/B-05 acceptance scenarios with synthetic evidence and a scripted model."""
import copy
import tempfile
import unittest
from pathlib import Path

from pthoven_vision.coaching import (CoachRuntime, ToolRegistry, Tool, dump, route_coaching_trigger,
    build_coach_context, decide_practice_strategy, validate_coach_plan, run_b04)
from pthoven_vision.coaching_contracts import (ActionPolicy, PracticeSnapshot, LiveControlState,
    RunPolicy, Evidence, CoachPlan, ApplicationAck)
from pthoven_vision.practice_commands import (CommandJournal, CommandContext,
    issue_practice_command, reconcile_intervention, run_b05)


def fixture():
    clock = [10.0]
    snapshot = PracticeSnapshot.model_validate(dict(snapshot_id='snap1', state_version=1,
        session_id='s1', attempt_id='a1', exercise_ref='song.v1', clock_id='browser1',
        actual_config=dict(bpm=80, start_index=0, end_index=7, hint_level=2, config_version=1),
        calibration_version=1, geometry_version=1, command_epoch=1,
        observation_status='usable_provisional', contact_verified=False,
        metrics={'timing_mae_ms': None}, denominators={'timing_sample_count': 0},
        unknown_intervals=[{'start_ms': 10, 'end_ms': 20, 'reason': 'occluded'}], evidence_refs=['e1']))
    live = LiveControlState.model_validate(dict(session_id='s1', attempt_id='a1', exercise_ref='song.v1',
        clock_id='browser1', phase='running', actual_config=dump(snapshot.actual_config),
        versions=dump(snapshot.versions()), observation_status='usable_provisional', calibration_valid=True,
        user_resume_requested=False, finish_authorized=False))
    run_policy = RunPolicy(policy_version='test-only', cooldown_ms=100, max_runs=4, max_model_calls=6,
        max_tool_calls=4, max_tokens=1000, max_context_bytes=50000, max_tool_result_bytes=10000,
        total_time_ms=1000, max_revisions=1)
    registry = ToolRegistry.with_stage_tools()
    policy = ActionPolicy(policy_version='test-only', allowed_tools=list(registry.tools), min_bpm=30,
        max_bpm=90, max_bpm_delta=30, note_count=8, hint_levels=[0, 1, 2],
        command_ttl_ms=1000, ack_timeout_ms=200, apply_at='next_segment')
    evidence = Evidence(evidence_ref='e1', session_id='s1', attempt_id='a1', exercise_ref='song.v1',
        versions=snapshot.versions(), kind='metrics', values={'timing_mae_ms': {'value': None, 'unit': 'ms'}})
    ctx = CoachRuntime(snapshot=snapshot, live=live, run_policy=run_policy, action_policy=policy,
        registry=registry, evidence={'e1': evidence}, history=[], model=lambda *a, **k: None,
        model_profile={'model_id': 'scripted', 'revision': 'test-v1'}, current_state=lambda: live,
        now=lambda: clock[0])
    trigger = dict(trigger_id='t1', kind='segment_end', session_id='s1', attempt_id='a1', clock_id='browser1', t_ms=100)
    return ctx, live, clock, trigger


def plan(ctx, action='keep_current', refs=None):
    return dict(decision_id='d1', snapshot_ref='snap1', evidence_refs=['e1'], action=action,
        proposal_refs=refs or [], rationale={'observation': 'No valid timing samples', 'hypothesis': '', 'reason': 'Observe'},
        review_criteria=[{'metric': 'timing_sample_count', 'minimum': 1}], user_message='Observe next attempt',
        claims=[dict(evidence_ref='e1', field='timing_mae_ms', value=None, unit='ms')])


def scripted(ctx, operations):
    turns = [0]
    def model(payload, **kwargs):
        turns[0] += 1
        if turns[0] == 1 and operations:
            return {'tokens_used': 10, 'tool_calls': [dict(tool_call_id=str(i), name=n, arguments=a) for i, (n, a) in enumerate(operations)]}
        refs = list(ctx.proposals)
        action = 'intervene' if operations else 'keep_current'
        return {'tokens_used': 10, 'plan': plan(ctx, action, refs)}
    ctx.model = model
    return turns


class CoachingTests(unittest.TestCase):
    def test_empty_change_set_never_issues(self):
        ctx, _, _, trigger = fixture()
        scripted(ctx, [])
        out = run_b04(trigger, ctx)
        self.assertEqual(out['outcome'], 'validated')
        self.assertEqual(out['data']['change_set'], [])
        self.assertIsNone(ctx.active_request)
        self.assertEqual([x['node'] for x in ctx.trace], ['S-09', 'S-10', 'S-11', 'S-12'])

    def test_trigger_deduplication_and_no_new_evidence(self):
        ctx, _, _, trigger = fixture()
        self.assertEqual(route_coaching_trigger(trigger, ctx)['outcome'], 'coaching_required')
        ctx.active_request = None
        self.assertEqual(route_coaching_trigger(trigger, ctx)['outcome'], 'suppressed_duplicate')
        trigger['trigger_id'] = 'new-frame-id'
        self.assertEqual(route_coaching_trigger(trigger, ctx)['outcome'], 'suppressed_duplicate')

    def test_concurrent_run_and_budget(self):
        ctx, _, _, trigger = fixture()
        route_coaching_trigger(trigger, ctx)
        trigger.update(trigger_id='user', kind='user_request')
        self.assertEqual(route_coaching_trigger(trigger, ctx)['outcome'], 'wait_for_boundary')
        ctx.active_request = None
        ctx.run_count = ctx.run_policy.max_runs
        self.assertEqual(route_coaching_trigger(trigger, ctx)['outcome'], 'budget_exhausted')

    def test_quality_hold_before_budget_or_model(self):
        ctx, live, _, trigger = fixture()
        calls = []
        def hold():
            calls.append('system_hold')
            live.versions.command_epoch += 1
            live.phase = 'paused'
        ctx.quality_hold = hold
        ctx.run_count = ctx.run_policy.max_runs
        trigger['kind'] = 'quality_degraded'
        self.assertEqual(route_coaching_trigger(trigger, ctx)['outcome'], 'wait_for_boundary')
        self.assertEqual(calls, ['system_hold'])

    def test_foreign_session_and_ended_session(self):
        for foreign in [True, False]:
            ctx, live, _, trigger = fixture()
            if foreign:
                trigger['session_id'] = 'other'
            else:
                live.phase = 'closed'
            self.assertEqual(route_coaching_trigger(trigger, ctx)['status'], 'error')

    def test_context_keeps_unknowns_and_excludes_staged_history(self):
        ctx, _, _, trigger = fixture()
        ctx.history = [{'history_ref': 'h1', 'session_id': 's1', 'exercise_ref': 'song.v1',
                        'lifecycle': 'staged', 'actual_values': {'bpm': 30}}]
        req = route_coaching_trigger(trigger, ctx)['data']
        out = build_coach_context(req, ctx)
        self.assertEqual(out['data']['history_refs'], [])
        self.assertIsNone(out['data']['snapshot']['metrics']['timing_mae_ms'])
        self.assertEqual(out['data']['snapshot']['actual_config']['config_version'], 1)
        self.assertTrue(out['data']['snapshot']['unknown_intervals'])
        ctx.run_policy.max_context_bytes = 1
        self.assertEqual(build_coach_context(req, ctx)['outcome'], 'insufficient_context')

    def test_optional_images_disabled(self):
        ctx, _, _, trigger = fixture()
        ctx.registry.tools['inspect_visual_context'] = Tool('inspect_visual_context', 'optional_read', {})
        ctx.action_policy.allowed_tools.append('inspect_visual_context')
        req = route_coaching_trigger(trigger, ctx)['data']
        self.assertNotIn('inspect_visual_context', build_coach_context(req, ctx)['data']['allowed_tool_ids'])

    def test_model_failure_keeps_confirmed_settings(self):
        ctx, live, _, trigger = fixture()
        before = dump(live)
        def broken(*a, **kw):
            raise TimeoutError('runtime timeout')
        ctx.model = broken
        out = run_b04(trigger, ctx)
        self.assertEqual((out['status'], out['outcome']), ('error', 'model_unavailable'))
        self.assertEqual(dump(live), before)

    def test_shared_call_and_time_limits(self):
        for mode in ['calls', 'time', 'tokens']:
            ctx, _, clock, trigger = fixture()
            def model(*a, **kw):
                if mode == 'time':
                    clock[0] += 2
                return {'tokens_used': 2000 if mode == 'tokens' else 1, 'tool_calls': [
                    dict(tool_call_id='same', name='set_tempo', arguments={'bpm': 60, 'reason': 'test'})]}
            ctx.model = model
            out = run_b04(trigger, ctx)
            self.assertEqual(out['outcome'], 'model_unavailable')
            self.assertLessEqual(ctx.budget.model_calls, ctx.run_policy.max_model_calls)
            self.assertLessEqual(ctx.budget.tool_calls, ctx.run_policy.max_tool_calls)

    def test_read_result_changes_decision_and_is_traced(self):
        ctx, _, _, trigger = fixture()
        e = ctx.evidence['e1'].model_copy(deep=True)
        e.evidence_ref = 'read-evidence'
        e.values = {'evaluable_count': {'value': 0, 'unit': 'notes'}}
        ctx.registry.tools['get_segment_evidence'] = Tool('get_segment_evidence', 'read', {'type': 'object'},
            lambda args, **kw: {'status': 'success', 'data': dump(e), 'diagnostics': {}, 'outcome': 'evidence_available'})
        ctx.action_policy.allowed_tools.append('get_segment_evidence')
        calls = []
        def model(payload, **kw):
            calls.append(payload)
            if not payload['tool_results']:
                return {'tokens_used': 1, 'tool_calls': [dict(tool_call_id='read', name='get_segment_evidence', arguments={})]}
            p = plan(ctx, 'observe_more')
            p['evidence_refs'] = ['read-evidence']
            p['claims'] = [dict(evidence_ref='read-evidence', field='evaluable_count', value=0, unit='notes')]
            return {'tokens_used': 1, 'plan': p}
        ctx.model = model
        self.assertEqual(run_b04(trigger, ctx)['outcome'], 'validated')
        self.assertEqual(len(calls), 2)
        self.assertTrue(any('tool_call' in t for t in ctx.trace))

    def test_plan_rejects_bad_evidence_values_and_protected_fields(self):
        for kind in ['unknown_ref', 'null_as_zero', 'protected', 'range', 'conflict', 'mixed']:
            ctx, _, _, trigger = fixture()
            operations = [('set_tempo', {'bpm': 60, 'reason': 'test'})]
            if kind == 'protected':
                operations[0][1]['contact_verified'] = True
            if kind == 'range':
                operations = [('set_practice_range', {'start_index': 0, 'end_index': 100, 'reason': 'test'})]
            if kind == 'conflict':
                operations.append(('set_tempo', {'bpm': 65, 'reason': 'other'}))
            if kind == 'mixed':
                operations.append(('pause_practice', {'reason': 'test'}))
            scripted(ctx, operations)
            req = route_coaching_trigger(trigger, ctx)['data']
            built = build_coach_context(req, ctx)
            decided = decide_practice_strategy(built['data'], ctx)
            if kind == 'protected':
                self.assertEqual(decided['status'], 'error')
                continue
            p = decided['data']
            if kind == 'unknown_ref': p['evidence_refs'] = ['invented']
            if kind == 'null_as_zero': p['claims'][0]['value'] = 0
            self.assertEqual(validate_coach_plan(p, ctx)['status'], 'error', kind)

    def test_manual_control_during_model_call_invalidates_plan(self):
        for change in ['epoch', 'phase', 'config']:
            ctx, live, _, trigger = fixture()
            def model(*a, **kw):
                if change == 'epoch': live.versions.command_epoch += 1
                if change == 'phase': live.phase = 'paused'
                if change == 'config': live.actual_config.bpm = 70
                return {'tokens_used': 1, 'plan': plan(ctx)}
            ctx.model = model
            self.assertEqual(run_b04(trigger, ctx)['status'], 'error')

    def test_revision_uses_same_budget_and_receives_findings(self):
        ctx, _, _, trigger = fixture()
        turns = []
        def model(payload, **kw):
            turns.append(payload)
            p = plan(ctx)
            if len(turns) == 1:
                p['proposal_refs'] = ['same', 'same']
                # Duplicate schema is checked by S-12; stage valid proposals first
                # in this fixture to keep the S-11 reference contract intact.
                return {'tokens_used': 1, 'tool_calls': [dict(tool_call_id='one', name='set_tempo', arguments={'bpm': 60, 'reason': 'test'})]}
            p = plan(ctx, 'intervene', list(ctx.proposals))
            if len(turns) == 2:
                p['review_criteria'] = []
            return {'tokens_used': 1, 'plan': p}
        ctx.model = model
        out = run_b04(trigger, ctx)
        self.assertEqual(out['outcome'], 'validated')
        self.assertEqual(ctx.budget.model_calls, 3)
        self.assertIn('REVIEW_CRITERIA_REQUIRED', turns[-1]['context']['instructions'])

    def test_context_rejects_foreign_history_and_evidence(self):
        for source in ['history', 'evidence']:
            ctx, _, _, trigger = fixture()
            if source == 'history':
                ctx.history = [{'session_id': 'other', 'exercise_ref': 'song.v1'}]
            else:
                ctx.evidence['e1'].session_id = 'other'
            req = route_coaching_trigger(trigger, ctx)['data']
            self.assertEqual(build_coach_context(req, ctx)['status'], 'error')


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.ctx, self.live, self.clock, self.trigger = fixture()
        scripted(self.ctx, [('set_tempo', {'bpm': 60, 'reason': 'test'}),
                            ('set_practice_range', {'start_index': 0, 'end_index': 3, 'reason': 'test'})])
        self.plan = run_b04(self.trigger, self.ctx)['data']
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'commands.sqlite'
        self.journal = CommandJournal(self.path)
        self.sent = []
        self.commands = CommandContext(self.journal, self.ctx.action_policy, lambda: self.live,
            self.sent.append, lambda query: None, now=lambda: self.clock[0])

    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    def issue(self):
        return issue_practice_command(self.plan, self.commands)

    def ack(self, ticket):
        v = ticket['expected_versions'].copy()
        v['config_version'] += 1
        return dict(session_id='s1', command_id=ticket['command_id'], result='applied',
            applied_values=dict(bpm=60, start_index=0, end_index=3, hint_level=2, config_version=2, phase='running'),
            actual_versions=v, applied_at_ms=500, reason_code='APPLIED', client_clock_id='browser1', effective_attempt_ref='a2')

    def test_queued_duplicate_and_conflict(self):
        first = self.issue()
        self.assertEqual(first['outcome'], 'queued')
        self.assertEqual(self.live.actual_config.config_version, 1)
        second = self.issue()
        self.assertEqual(second['outcome'], 'duplicate_ticket')
        self.assertEqual(first['data'], second['data'])
        self.assertEqual(len(self.sent), 1)
        other = copy.deepcopy(self.plan); other['plan_ref'] = 'd2'
        self.assertEqual(issue_practice_command(other, self.commands)['outcome'], 'pending_conflict')

    def test_delivery_failure_persists_for_restart(self):
        def offline(*a): raise ConnectionError('offline')
        self.commands.publish = offline
        out = self.issue()
        self.assertEqual((out['status'], out['outcome']), ('partial', 'dispatch_failed'))
        self.journal.close()
        self.journal = CommandJournal(self.path)
        self.commands.journal = self.journal
        self.assertTrue(self.journal.pending('s1'))
        self.assertEqual(self.journal.pending_tickets()[0].command_id, out['data']['command_id'])

    def test_ack_loss_probe_and_late_ack_after_deadline(self):
        ticket = self.issue()['data']
        self.clock[0] += 3  # Server expiry does not prove non-application.
        missing = reconcile_intervention({'command_id': ticket['command_id'], 'observation': None}, self.commands)
        self.assertEqual(missing['data']['lifecycle'], 'reconciliation_required')
        self.assertTrue(self.journal.pending('s1'))
        ack = self.ack(ticket)
        self.commands.probe = lambda q: dict(session_id='s1', command_id=ticket['command_id'], client_clock_id='browser1', command_ack=ack)
        confirmed = reconcile_intervention({'command_id': ticket['command_id'], 'observation': None}, self.commands)
        self.assertEqual(confirmed['outcome'], 'intervention_confirmed')
        self.assertEqual(confirmed['data']['effective_attempt_ref'], 'a2')
        self.assertEqual(confirmed['data']['actual_values']['bpm'], 60)
        self.assertEqual(confirmed['data']['requested_values'][0]['arguments']['bpm'], 60)
        self.assertFalse(self.journal.pending('s1'))
        self.assertEqual(self.live.actual_config.bpm, 60)
        self.assertEqual(self.live.versions.config_version, 2)
        self.assertEqual(self.journal.effective_config('s1', 'a2')['bpm'], 60)
        self.assertIsNone(self.journal.effective_config('s1', 'unconfirmed-attempt'))

    def test_identity_values_version_and_clock_conflicts(self):
        ticket = self.issue()['data']
        for mutation in [lambda a: a.update(command_id='other'), lambda a: a['applied_values'].update(bpm=70),
                         lambda a: a['actual_versions'].update(command_epoch=88), lambda a: a.update(client_clock_id='other')]:
            ack = self.ack(ticket); mutation(ack)
            out = reconcile_intervention({'command_id': ticket['command_id'], 'observation': ack}, self.commands)
            self.assertEqual(out['outcome'], 'ack_conflict')
            self.assertTrue(self.journal.pending('s1'))

    def test_duplicate_ack_and_conflict_cannot_erase_confirmation(self):
        ticket = self.issue()['data']
        req = {'command_id': ticket['command_id'], 'observation': self.ack(ticket)}
        first = reconcile_intervention(req, self.commands)
        self.assertEqual(reconcile_intervention(req, self.commands)['data'], first['data'])
        req['observation']['result'] = 'application_unknown'
        self.assertEqual(reconcile_intervention(req, self.commands)['outcome'], 'ack_conflict')
        self.assertEqual(self.journal.record(ticket['command_id']).lifecycle, 'confirmed_applied')

    def test_expired_plan_and_empty_changes(self):
        self.clock[0] += 2
        self.assertEqual(self.issue()['status'], 'error')
        self.plan['change_set'] = []
        self.assertEqual(run_b05(self.plan, self.commands)['outcome'], 'no_change')

    def test_probe_failure_remains_uncertain(self):
        ticket = self.issue()['data']
        def offline(*a): raise ConnectionError('offline')
        self.commands.probe = offline
        out = reconcile_intervention({'command_id': ticket['command_id'], 'observation': None}, self.commands)
        self.assertEqual(out['status'], 'error')
        self.assertEqual(self.journal.record(ticket['command_id']).lifecycle, 'reconciliation_required')

    def test_late_ack_preserves_new_manual_state_and_original_clock(self):
        ticket = self.issue()['data']
        self.live.versions.command_epoch += 1
        self.live.actual_config.bpm = 70
        self.live.clock_id = 'reconnected-browser'
        out = reconcile_intervention({'command_id': ticket['command_id'], 'observation': self.ack(ticket)}, self.commands)
        self.assertEqual(out['outcome'], 'intervention_confirmed')
        self.assertEqual(self.live.actual_config.bpm, 70)
        self.assertEqual(self.live.versions.command_epoch, 2)

    def test_projection_failure_keeps_confirmed_record_and_blocks_stale_issuance(self):
        ticket = self.issue()['data']
        def unavailable(*a): raise OSError('state store unavailable')
        self.commands.persist_actual_state = unavailable
        out = reconcile_intervention({'command_id': ticket['command_id'], 'observation': self.ack(ticket)}, self.commands)
        self.assertEqual((out['status'], out['outcome']), ('partial', 'intervention_confirmed'))
        self.assertEqual(self.journal.record(ticket['command_id']).lifecycle, 'confirmed_applied')
        other = copy.deepcopy(self.plan); other['plan_ref'] = 'd2'
        self.assertEqual(issue_practice_command(other, self.commands)['status'], 'error')
        self.commands.persist_actual_state = None
        reconcile_intervention({'command_id': ticket['command_id'], 'observation': None}, self.commands)
        self.assertEqual(self.live.actual_config.bpm, 60)


if __name__ == '__main__':
    unittest.main()
