/* S-14: deterministic client command boundary. No model/tool code runs here.
 * applyAtomic(nextState) must commit the complete UI/game state synchronously.
 * A throwing commit is unknown, never a fabricated rejection. Recovered unknown
 * commands cannot be replayed. Manual controls/quality holds use manualChange().
 */
"use strict";
(function (root) {
  const clone = (v) => JSON.parse(JSON.stringify(v));
  const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const diag = () => Object.fromEntries([
    "check_client_preconditions", "wait_for_apply_boundary", "apply_atomic_change_set", "emit_application_ack",
  ].map((n) => [n, { status: "skipped", error_code: null, reason: "not reached", retryable: false }]));
  const done = (d, n, reason = "completed") => { d[n] = { status: "success", error_code: null, reason, retryable: false }; };
  const result = (data, outcome, diagnostics, status = "success") => ({ status, data, diagnostics, outcome });
  const controls = { request_recalibration: "calibrating", pause_practice: "paused", resume_practice: "running", finish_practice: "finished" };
  const fields = { set_tempo: ["bpm", "reason"], set_practice_range: ["start_index", "end_index", "reason"], set_hint_level: ["level", "reason"] };

  function check_client_preconditions(ticket, state, expiresAt, now) {
    if (ticket.session_id !== state.session_id || ticket.exercise_ref !== state.exercise_ref || ticket.attempt_ref !== state.attempt_id || ticket.clock_id !== state.clock_id) return "SCOPE_MISMATCH";
    if (!Number.isFinite(expiresAt) || now >= expiresAt) return "EXPIRED_BEFORE_APPLY";
    if (ticket.command_epoch !== state.versions.command_epoch) return "STALE_EPOCH";
    for (const key of ["config_version", "calibration_version", "geometry_version", "command_epoch"]) {
      if (!Number.isInteger(ticket.expected_versions?.[key]) || ticket.expected_versions[key] !== state.versions[key]) return "STALE_VERSION";
    }
    if (state.actual_config.config_version !== state.versions.config_version) return "ACTUAL_VERSION_MISMATCH";
    if (["closed", "finished"].includes(state.phase)) return "SESSION_ENDED";
    if (ticket.expected_phase !== state.phase) return "PHASE_CHANGED";
    if (!["next_segment", "paused"].includes(ticket.apply_at)) return "UNSUPPORTED_BOUNDARY";
    return null;
  }

  function wait_for_apply_boundary(ticket, state, boundary) {
    return ticket.apply_at === "paused" ? state.phase === "paused" : boundary === "next_segment";
  }

  function apply_atomic_change_set(ticket, state, policy) {
    if (!Array.isArray(ticket.change_set) || !ticket.change_set.length) throw new Error("EMPTY_CHANGE_SET");
    const next = clone(state), seen = new Set();
    for (const proposal of ticket.change_set) {
      const name = proposal.operation, args = proposal.arguments;
      if (!policy.allowed_tools.includes(name) || seen.has(name)) throw new Error("UNSUPPORTED_OR_CONFLICTING_TOOL");
      seen.add(name);
      const expected = fields[name] || (controls[name] ? ["reason"] : null);
      if (!expected || !args || !equal(Object.keys(args).sort(), expected.slice().sort()) || typeof args.reason !== "string" || !args.reason.trim()) throw new Error("INVALID_ARGUMENTS");
      if (proposal.snapshot_ref !== ticket.snapshot_ref || !ticket.proposal_refs.includes(proposal.proposal_id)) throw new Error("PROPOSAL_SCOPE_MISMATCH");
      if (name === "set_tempo") {
        if (!Number.isFinite(args.bpm) || args.bpm < policy.min_bpm || args.bpm > policy.max_bpm || Math.abs(args.bpm - state.actual_config.bpm) > policy.max_bpm_delta) throw new Error("BPM_OUT_OF_POLICY");
        next.actual_config.bpm = args.bpm;
      } else if (name === "set_practice_range") {
        if (!Number.isInteger(args.start_index) || !Number.isInteger(args.end_index) || args.start_index < 0 || args.start_index > args.end_index || args.end_index >= policy.note_count) throw new Error("NOTE_RANGE_OUT_OF_POLICY");
        next.actual_config.start_index = args.start_index; next.actual_config.end_index = args.end_index;
      } else if (name === "set_hint_level") {
        if (!Number.isInteger(args.level) || !policy.hint_levels.includes(args.level)) throw new Error("UNSUPPORTED_HINT_LEVEL");
        next.actual_config.hint_level = args.level;
      } else {
        if (ticket.change_set.length !== 1) throw new Error("MIXED_CONTROL_AND_CONFIG");
        if (name === "resume_practice" && (!state.user_resume_requested || !state.calibration_valid || state.observation_status === "hold_required" || state.phase !== "paused")) throw new Error("RESUME_NOT_AUTHORIZED");
        if (name === "finish_practice" && !state.finish_authorized) throw new Error("FINISH_NOT_AUTHORIZED");
        next.phase = controls[name];
        next.versions.command_epoch += 1;
        if (name === "request_recalibration") { next.calibration_valid = false; next.observation_status = "hold_required"; }
      }
    }
    if (state.observation_status === "hold_required" && [...seen].some((n) => fields[n] || n === "resume_practice")) throw new Error("QUALITY_HOLD_ACTIVE");
    next.versions.config_version += 1;
    next.actual_config.config_version = next.versions.config_version;
    return next;
  }

  function emit_application_ack(ticket, state, outcome, reason, at, attempt) {
    return { session_id: state.session_id, command_id: ticket.command_id, result: outcome,
      applied_values: outcome === "application_unknown" ? null : { ...clone(state.actual_config), phase: state.phase },
      actual_versions: clone(state.versions), applied_at_ms: outcome === "applied" ? at : null,
      reason_code: reason, client_clock_id: state.clock_id, effective_attempt_ref: outcome === "applied" ? attempt : null };
  }

  function createClient({ state: initialState, policy, now, applyAtomic, save, restored = {} }) {
    if (typeof applyAtomic !== "function" || typeof save !== "function" || typeof now !== "function") throw new Error("CLIENT_ADAPTERS_REQUIRED");
    let state = clone(initialState);
    const entries = clone(restored);
    const persist = () => save(clone(entries));

    function apply_command_and_ack(request, context) {
      try { return applyCommand(request, context); }
      catch (error) {
        const d = diag();
        d.check_client_preconditions = { status: "failed", error_code: "CLIENT_CONTRACT_OR_STORAGE_FAILURE", reason: "Client validation or persistence failed", retryable: false };
        return result(null, "application_unknown", d, "error");
      }
    }

    function applyCommand(request, context) {
      const d = diag(), ticket = clone(request), old = entries[ticket.command_id];
      if (typeof ticket.command_id !== "string" || !ticket.command_id) throw new Error("COMMAND_ID_REQUIRED");
      if (old) {
        if (!equal(old.ticket, ticket)) return result(null, "rejected", d, "error");
        if (old.ack) { done(d, "emit_application_ack", "cached ACK"); return result(clone(old.ack), "duplicate_ack", d); }
        // A crash between intent persistence and ACK persistence is ambiguous.
        if (old.applying) return result(emit_application_ack(ticket, state, "application_unknown", "INTERRUPTED_COMMIT", null, null), "application_unknown", d);
      }
      const envelope = old?.context || clone(context);
      // Transport must map server expiry to the browser clock with its clock
      // offset/latency uncertainty. Never compare server monotonic timestamps.
      if (envelope.clock_id !== state.clock_id) return result(null, "rejected", d, "error");
      if (Object.values(entries).some((entry) => entry.ticket.command_id !== ticket.command_id && !entry.ack &&
          (entry.applying || entry.ticket.command_epoch === state.versions.command_epoch))) {
        done(d, "check_client_preconditions", "pending command requires reconciliation");
        return result(null, "wait_for_boundary", d);
      }
      let reason = check_client_preconditions(ticket, state, envelope.expires_at_ms, now());
      done(d, "check_client_preconditions");
      if (reason) return finishRejection(reason);
      if (!wait_for_apply_boundary(ticket, state, context.boundary)) {
        entries[ticket.command_id] = { ticket, context: envelope, applying: false };
        persist();
        done(d, "wait_for_apply_boundary", "waiting");
        return result(null, "wait_for_boundary", d);
      }
      done(d, "wait_for_apply_boundary");
      const needsAttempt = ticket.change_set.some((p) => fields[p.operation] || p.operation === "resume_practice");
      if (needsAttempt && (typeof context.effective_attempt_ref !== "string" || !context.effective_attempt_ref)) return finishRejection("NEXT_ATTEMPT_REQUIRED");
      let next;
      try { next = apply_atomic_change_set(ticket, state, policy); }
      catch (error) { return finishRejection(error.message); }
      // Last precondition check directly before the synchronous commit.
      reason = check_client_preconditions(ticket, state, envelope.expires_at_ms, now());
      if (reason) return finishRejection(reason);
      try {
        entries[ticket.command_id] = { ticket, context: envelope, applying: true };
        persist();
        const completion = applyAtomic(clone(next));
        if (completion && typeof completion.then === "function") throw new Error("ASYNC_COMMIT_NOT_SUPPORTED");
        state = next;
        done(d, "apply_atomic_change_set");
        const ack = emit_application_ack(ticket, state, "applied", "APPLIED", now(), context.effective_attempt_ref ?? null);
        entries[ticket.command_id] = { ticket, ack };
        persist();
        done(d, "emit_application_ack");
        return result(clone(ack), "applied", d);
      } catch (error) {
        d.apply_atomic_change_set = { status: "failed", error_code: "COMMIT_INTERRUPTED", reason: "Actual state probe required", retryable: false };
        return result(emit_application_ack(ticket, state, "application_unknown", "COMMIT_INTERRUPTED", null, null), "application_unknown", d, "partial");
      }
      function finishRejection(code) {
        const ack = emit_application_ack(ticket, state, "rejected", code, null, null);
        entries[ticket.command_id] = { ticket, ack };
        persist(); done(d, "emit_application_ack");
        return result(clone(ack), "rejected", d);
      }
    }

    function manualChange(update) {
      // The host also stops evaluation timers/input accounting during quality hold.
      const next = clone(state); update(next);
      next.versions.command_epoch = state.versions.command_epoch + 1;
      next.versions.config_version = state.versions.config_version + 1;
      next.actual_config.config_version = next.versions.config_version;
      applyAtomic(clone(next)); state = next;
    }

    function probe(command_id) {
      return { session_id: state.session_id, command_id, client_clock_id: state.clock_id,
        command_ack: entries[command_id]?.ack ? clone(entries[command_id].ack) : null };
    }
    return { apply_command_and_ack, manualChange, probe, snapshot: () => clone(state) };
  }
  root.PthovenPracticeCommand = { createClient, check_client_preconditions, wait_for_apply_boundary, apply_atomic_change_set, emit_application_ack };
})(globalThis);
