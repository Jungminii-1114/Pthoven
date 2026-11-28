"""B-05 server harness, S-13 issuance and S-15 reconciliation.

SQLite transactions persist queued tickets before delivery. The journal belongs
to a single coordinator process; its lock serializes threads and session writes.
The browser implements S-14 in web/practice-command.js.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .contracts import result
from .coaching import ContractViolation, done, failed, dump, uid
from .coaching_contracts import (ActionPolicy, ApplicationAck, ClientStateProbe,
    AppliedPracticeConfig, CommandTicket, InterventionRecord, LiveControlState, ValidatedPlan)


def node_diagnostics(*steps):
    return {name: {'status': 'skipped', 'error_code': None, 'reason': 'not reached', 'retryable': False} for name in steps}


class CommandJournal:
    """Recover with the same filesystem path after restart; :memory: is test-only."""
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS commands (
            command_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            decision_id TEXT NOT NULL, ticket TEXT NOT NULL,
            lifecycle TEXT NOT NULL, delivery TEXT NOT NULL,
            record TEXT, baseline TEXT NOT NULL, UNIQUE(session_id, decision_id))''')
        self.db.commit()

    def close(self):
        self.db.close()

    def find(self, session_id, decision_id):
        with self.lock:
            row = self.db.execute('SELECT ticket FROM commands WHERE session_id=? AND decision_id=?', (session_id, decision_id)).fetchone()
            return CommandTicket.model_validate_json(row[0]) if row else None

    def get(self, command_id):
        with self.lock:
            row = self.db.execute('SELECT ticket FROM commands WHERE command_id=?', (command_id,)).fetchone()
            if row is None:
                raise ContractViolation('UNKNOWN_COMMAND')
            return CommandTicket.model_validate_json(row[0])

    def pending(self, session_id):
        with self.lock:
            return self.db.execute("SELECT 1 FROM commands WHERE session_id=? AND lifecycle IN ('queued','reconciliation_required')", (session_id,)).fetchone() is not None

    def record(self, command_id):
        with self.lock:
            row = self.db.execute('SELECT record FROM commands WHERE command_id=?', (command_id,)).fetchone()
            return InterventionRecord.model_validate_json(row[0]) if row and row[0] else None

    def effective_config(self, session_id, attempt_id):
        """Only confirmed actual conditions may be attached to a next attempt."""
        with self.lock:
            rows = self.db.execute("SELECT record FROM commands WHERE session_id=? AND lifecycle='confirmed_applied' ORDER BY rowid DESC", (session_id,))
            for row in rows:
                record = InterventionRecord.model_validate_json(row[0])
                if record.effective_attempt_ref == attempt_id:
                    return copy.deepcopy(record.actual_values)
            return None

    def pending_tickets(self):
        with self.lock:
            return [CommandTicket.model_validate_json(row[0]) for row in self.db.execute(
                "SELECT ticket FROM commands WHERE lifecycle IN ('queued','reconciliation_required')")]

    def confirmed_head(self, session_id):
        with self.lock:
            row = self.db.execute("SELECT record FROM commands WHERE session_id=? AND lifecycle='confirmed_applied' ORDER BY rowid DESC LIMIT 1", (session_id,)).fetchone()
            return InterventionRecord.model_validate_json(row[0]) if row else None


@dataclass
class CommandContext:
    journal: CommandJournal
    policy: ActionPolicy
    current_state: Callable[[], LiveControlState]
    publish: Callable[[dict], None]
    probe: Callable[[dict], dict | None]
    # Must use the same coordinator clock domain as S-12 expires_at.
    now: Callable[[], float] = time.monotonic
    # State-store adapters may persist the confirmed projection here. The journal
    # remains authoritative even if projection writing fails and is retried.
    persist_actual_state: Callable[[dict], None] | None = None


def reserve_idempotency_key(plan, ctx):
    old = ctx.journal.find(plan.session_id, plan.plan_ref)
    if old:
        # Same ID cannot be reused with different selected changes or scope.
        if (old.change_set != plan.change_set or old.snapshot_ref != plan.snapshot_ref or
                old.expected_versions != plan.expected_versions or old.attempt_ref != plan.attempt_ref or
                old.exercise_ref != plan.exercise_ref or old.clock_id != plan.clock_id or
                old.expected_phase != plan.expected_phase or old.apply_at != plan.apply_at):
            raise ContractViolation('DECISION_ID_CONFLICT')
    return old


def persist_pending_ticket(plan, ctx):
    live = ctx.current_state()
    confirmed = ctx.journal.confirmed_head(plan.session_id)
    if confirmed:
        actual = confirmed.actual_versions
        if (live.versions.command_epoch < actual.command_epoch or
                (live.versions.command_epoch == actual.command_epoch and live.versions.config_version < actual.config_version)):
            raise ContractViolation('STATE_PROJECTION_PENDING')
    if (live.session_id != plan.session_id or live.attempt_id != plan.attempt_ref or
            live.exercise_ref != plan.exercise_ref or live.clock_id != plan.clock_id or
            live.phase != plan.expected_phase or live.phase in {'closed', 'finished'} or
            live.versions != plan.expected_versions or ctx.now()*1000 >= plan.expires_at):
        raise ContractViolation('STALE_PLAN')
    if not plan.change_set:
        raise ContractViolation('EMPTY_CHANGE_SET')
    now_ms = ctx.now()*1000
    ticket = CommandTicket(command_id=uid('command'), decision_id=plan.plan_ref,
        session_id=plan.session_id, attempt_ref=plan.attempt_ref, exercise_ref=plan.exercise_ref,
        clock_id=plan.clock_id,
        snapshot_ref=plan.snapshot_ref, proposal_refs=plan.proposal_refs, change_set=plan.change_set,
        expected_versions=plan.expected_versions, expected_phase=plan.expected_phase,
        command_epoch=plan.expected_versions.command_epoch, apply_at=plan.apply_at,
        issued_at=now_ms, expires_at=plan.expires_at,
        deadline=track_ack_deadline(now_ms, ctx))
    with ctx.journal.db:
        baseline = {**dump(live.actual_config), 'phase': live.phase}
        ctx.journal.db.execute('INSERT INTO commands VALUES (?,?,?,?,?,?,NULL,?)',
            (ticket.command_id, ticket.session_id, ticket.decision_id, ticket.model_dump_json(), 'queued', 'not_sent', json.dumps(baseline)))
    return ticket


def publish_command_ticket(ticket, ctx):
    # Convert coordinator time to a relative duration. Browser timestamps are a
    # different monotonic clock and must never be compared to issued_at/deadline.
    ttl = max(0, ticket.expires_at - ctx.now()*1000)
    payload = dump(ticket)
    payload['remaining_valid_ms'] = ttl
    ctx.publish(payload)
    with ctx.journal.db:
        ctx.journal.db.execute("UPDATE commands SET delivery='sent' WHERE command_id=?", (ticket.command_id,))


def track_ack_deadline(issued_at, ctx):
    return issued_at + ctx.policy.ack_timeout_ms


def issue_practice_command(request, context):
    ctx = context
    d = node_diagnostics('reserve_idempotency_key', 'persist_pending_ticket', 'publish_command_ticket', 'track_ack_deadline')
    step, ticket = 'reserve_idempotency_key', None
    try:
        plan = ValidatedPlan.model_validate(request)
        with ctx.journal.lock:
            old = reserve_idempotency_key(plan, ctx)
            done(d, step)
            if old:
                return result(dump(old), 'duplicate_ticket', diagnostics=d)
            if ctx.journal.pending(plan.session_id):
                return result(None, 'pending_conflict', diagnostics=d)
            step = 'persist_pending_ticket'
            ticket = persist_pending_ticket(plan, ctx)
            done(d, step)
            step = 'track_ack_deadline'
            done(d, step)
            step = 'publish_command_ticket'
            publish_command_ticket(ticket, ctx)
            done(d, step)
            return result(dump(ticket), 'queued', diagnostics=d)
    except Exception as exc:
        failed(d, step, str(exc) if isinstance(exc, ContractViolation) else type(exc).__name__,
               retryable=step == 'publish_command_ticket')
        # The queued ticket is still usable for recovery after delivery failure.
        return result(dump(ticket) if ticket else None, 'dispatch_failed',
                      status='partial' if ticket else 'error', diagnostics=d)


def verify_ack_identity(ack, ticket, ctx):
    if ack.command_id != ticket.command_id or ack.session_id != ticket.session_id:
        raise ContractViolation('ACK_IDENTITY_MISMATCH')
    live = ctx.current_state()
    if live.session_id != ticket.session_id or ack.client_clock_id != ticket.clock_id:
        raise ContractViolation('ACK_CLOCK_MISMATCH')


def reconcile_actual_values(ack, ticket, ctx):
    if ack.result == 'application_unknown':
        return 'reconciliation_required'
    if ack.result == 'rejected':
        if ack.applied_at_ms is not None or ack.effective_attempt_ref is not None:
            raise ContractViolation('REJECTED_ACK_HAS_APPLICATION')
        return 'confirmed_not_applied'
    needs_attempt = any(p.operation in {'set_tempo', 'set_practice_range', 'set_hint_level', 'resume_practice'} for p in ticket.change_set)
    if ack.applied_values is None or ack.applied_at_ms is None or (needs_attempt and ack.effective_attempt_ref is None):
        raise ContractViolation('INCOMPLETE_APPLICATION_ACK')
    expected = ticket.expected_versions.model_copy(deep=True)
    expected.config_version += 1
    controls = {'request_recalibration', 'pause_practice', 'resume_practice', 'finish_practice'}
    if any(p.operation in controls for p in ticket.change_set):
        expected.command_epoch += 1
    if ack.actual_versions != expected:
        raise ContractViolation('ACK_VERSION_CONFLICT')
    baseline = json.loads(ctx.journal.db.execute('SELECT baseline FROM commands WHERE command_id=?', (ticket.command_id,)).fetchone()[0])
    baseline['config_version'] = expected.config_version
    for p in ticket.change_set:
        a, values = p.arguments, ack.applied_values
        requested = ({'bpm': a['bpm']} if p.operation == 'set_tempo' else
                     {'start_index': a['start_index'], 'end_index': a['end_index']} if p.operation == 'set_practice_range' else
                     {'hint_level': a['level']} if p.operation == 'set_hint_level' else
                     {'phase': {'pause_practice': 'paused', 'resume_practice': 'running',
                                'request_recalibration': 'calibrating', 'finish_practice': 'finished'}[p.operation]})
        if any(values.get(k) != v for k, v in requested.items()):
            raise ContractViolation('ACK_VALUE_CONFLICT')
        baseline.update(requested)
    if ack.applied_values != baseline:
        raise ContractViolation('ACK_UNREQUESTED_VALUE_CONFLICT')
    return 'confirmed_applied'


def finalize_intervention_record(ticket, ack, lifecycle, reason, ctx):
    old = ctx.journal.record(ticket.command_id)
    out = InterventionRecord(intervention_id=old.intervention_id if old else uid('intervention'),
        command_ref=ticket.command_id,
        requested_values=[{'operation': p.operation, 'arguments': copy.deepcopy(p.arguments)} for p in ticket.change_set],
        actual_values=copy.deepcopy(ack.applied_values) if ack else None,
        lifecycle=lifecycle, effective_attempt_ref=ack.effective_attempt_ref if ack and lifecycle == 'confirmed_applied' else None,
        reconciliation_reason=reason, actual_versions=ack.actual_versions if ack else None,
        applied_at_ms=ack.applied_at_ms if ack and lifecycle == 'confirmed_applied' else None)
    # Duplicate acknowledgements must not roll back a previously confirmed record.
    if old and old.lifecycle != 'reconciliation_required':
        if (out.lifecycle, out.actual_values, out.actual_versions, out.applied_at_ms, out.effective_attempt_ref) != (
                old.lifecycle, old.actual_values, old.actual_versions, old.applied_at_ms, old.effective_attempt_ref):
            raise ContractViolation('FINAL_ACK_CONFLICT')
        return old
    with ctx.journal.db:
        ctx.journal.db.execute('UPDATE commands SET lifecycle=?,record=? WHERE command_id=?',
            (lifecycle, out.model_dump_json(), ticket.command_id))
    return out


def project_confirmed_state(record, ticket, ctx):
    if record.lifecycle != 'confirmed_applied':
        return
    live = ctx.current_state()
    # Late historical ACKs remain in the journal, without undoing manual controls
    # or reconnect/calibration events that changed the current epoch or clock.
    if (live.session_id != ticket.session_id or live.clock_id != ticket.clock_id or
            live.versions not in (ticket.expected_versions, record.actual_versions)):
        return
    projected = live.model_copy(deep=True)
    projected.actual_config = AppliedPracticeConfig.model_validate({k: record.actual_values[k]
        for k in ('bpm', 'start_index', 'end_index', 'hint_level', 'config_version')})
    projected.versions = record.actual_versions.model_copy(deep=True)
    projected.phase = record.actual_values['phase']
    if record.effective_attempt_ref is not None:
        projected.attempt_id = record.effective_attempt_ref
    if projected.phase == 'calibrating':
        projected.calibration_valid = False
        projected.observation_status = 'hold_required'
    if ctx.persist_actual_state:
        ctx.persist_actual_state(dump(projected))
    live.actual_config = projected.actual_config
    live.versions = projected.versions
    live.phase = projected.phase
    live.attempt_id = projected.attempt_id
    live.calibration_valid = projected.calibration_valid
    live.observation_status = projected.observation_status


def schedule_state_probe(ticket, ctx):
    # A reader returns command-specific cached ACK, never an inferred success
    # just because current BPM happens to equal the requested BPM.
    return ctx.probe({'session_id': ticket.session_id, 'command_id': ticket.command_id})


def reconcile_intervention(request, context):
    """request = {command_id, observation: ApplicationAck | ClientStateProbe | null}."""
    ctx = context
    d = node_diagnostics('verify_ack_identity', 'reconcile_actual_values', 'finalize_intervention_record', 'schedule_state_probe')
    step, ticket, record = 'verify_ack_identity', None, None
    try:
        if not isinstance(request, dict) or set(request) != {'command_id', 'observation'}:
            raise ContractViolation('INVALID_RECONCILIATION_REQUEST')
        with ctx.journal.lock:
            ticket = ctx.journal.get(request['command_id'])
            observation = request['observation']
            if observation is None:
                old = ctx.journal.record(ticket.command_id)
                if old and old.lifecycle != 'reconciliation_required':
                    record = old
                    project_confirmed_state(old, ticket, ctx)
                    return result(dump(old), 'intervention_confirmed' if old.lifecycle == 'confirmed_applied' else 'intervention_rejected', diagnostics=d)
                step = 'finalize_intervention_record'
                # Persist uncertainty BEFORE the network call, including if the probe crashes.
                finalize_intervention_record(ticket, None, 'reconciliation_required', 'ACK_MISSING', ctx)
                done(d, step)
                step = 'schedule_state_probe'
                observation = schedule_state_probe(ticket, ctx)
                done(d, step)
            step = 'verify_ack_identity'
            ack = None
            if observation is not None:
                if 'command_ack' in observation:
                    probe = ClientStateProbe.model_validate(observation)
                    live = ctx.current_state()
                    if (probe.command_id, probe.session_id, probe.client_clock_id) != (ticket.command_id, ticket.session_id, ticket.clock_id):
                        raise ContractViolation('PROBE_IDENTITY_MISMATCH')
                    ack = probe.command_ack
                else:
                    ack = ApplicationAck.model_validate(observation)
                if ack:
                    verify_ack_identity(ack, ticket, ctx)
                    done(d, step)
            step = 'reconcile_actual_values'
            lifecycle = reconcile_actual_values(ack, ticket, ctx) if ack else 'reconciliation_required'
            done(d, step)
            step = 'finalize_intervention_record'
            record = finalize_intervention_record(ticket, ack, lifecycle, ack.reason_code if ack else 'ACK_MISSING', ctx)
            project_confirmed_state(record, ticket, ctx)
            done(d, step)
            outcome = {'confirmed_applied': 'intervention_confirmed', 'confirmed_not_applied': 'intervention_rejected',
                       'reconciliation_required': 'reconciliation_required'}[record.lifecycle]
            return result(dump(record), outcome, diagnostics=d)
    except Exception as exc:
        failed(d, step, str(exc) if isinstance(exc, ContractViolation) else type(exc).__name__)
        if record is not None and not isinstance(exc, ContractViolation):
            return result(dump(record), 'intervention_confirmed' if record.lifecycle == 'confirmed_applied' else 'reconciliation_required',
                          status='partial', diagnostics=d)
        # Identity/value conflicts retain the pending lock; they never confirm execution.
        return result(None, 'reconciliation_required' if step == 'schedule_state_probe' else 'ack_conflict',
                      status='error', diagnostics=d)


def run_b05(validated_plan, context, *, observation=None):
    """Issue now, reconcile on ACK or deadline. Never interpret waiting as failure."""
    plan = ValidatedPlan.model_validate(validated_plan)
    if not plan.change_set:
        return result(None, 'no_change')
    issued = issue_practice_command(plan, context)
    if issued['data'] is None:
        return issued
    ticket = CommandTicket.model_validate(issued['data'])
    if observation is not None or context.now()*1000 >= ticket.deadline or issued['outcome'] == 'dispatch_failed':
        return reconcile_intervention({'command_id': ticket.command_id, 'observation': observation}, context)
    return issued
