/* B01 browser adapter: capture, transport and display only. All pose decisions are Python's. */
"use strict";

(() => {
  const API = "/api/vision/v1";
  const $ = (id) => document.getElementById(id);
  const video = $("camera-video");
  const overlay = $("landmark-overlay");
  const overlayContext = overlay.getContext("2d");
  const encoder = document.createElement("canvas");
  const encoderContext = encoder.getContext("2d", { alpha: false });
  const clockId = `browser-${crypto.randomUUID()}`;
  const FINGERS = { thumb: "엄지", index: "검지", middle: "중지", ring: "약지", pinky: "소지" };
  const LIVE_STAGES = new Set(["preparing", "collecting", "validating"]);
  const BONES = [[0,1],[1,2],[2,3],[3,4],[0,5],[5,6],[6,7],[7,8],[5,9],[9,10],[10,11],[11,12],[9,13],[13,14],[14,15],[15,16],[13,17],[0,17],[17,18],[18,19],[19,20]];
  const state = {
    generation: 0, session: null, stream: null, retiredSession: null,
    camera: "disconnected", permission: "unknown", deviceId: "", deviceLabel: "",
    tracker: "not_loaded", model: null, health: null, healthBusy: false, modelRequestSequence: 0, modelResponseSequence: 0,
    connectionBusy: false, controlBusy: false, refreshBusy: false,
    geometryPending: false, geometrySignature: "", deviceSignature: "", syncTimer: null, syncBlocked: false,
    flipX: false, flipY: false, capture: null, captureToken: 0, finishing: false,
    frameHandle: null, frameMode: null, lastMediaTime: -1, lastPresented: -1,
    lastFrameAt: 0, frameSequence: 0, latest: null, inFlight: null,
    observed: null, observationAt: 0, quality: null, diagnostics: null,
    received: 0, processed: 0, skipped: 0, latency: null, sessionPollBusy: false,
    readyConfirmed: false, readyCalibration: null, controllers: new Set(), controlRevision: 0,
    selectedHand: "right", activeFingers: ["index"], lastDetailedRender: 0,
    // B-02: proposals become final events only after the browser stamps emitted_ms once (CONTRACTS 6.2).
    inputSummary: null, lastInput: null, seenEvents: new Set(), audio: null, ackFailures: 0,
  };
  const KEY_ORDER = ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"];
  const FLAG_TEXT = { OUTSIDE_KEYBOARD: "건반 영역 밖", KEY_BOUNDARY: "건반 경계", COORDINATE_UNAVAILABLE: "후보 시점 좌표 없음", STALE_GEOMETRY: "기하 버전 변경" };
  let geometryTimer;

  class ApiError extends Error {
    constructor(message, status, result) { super(message); this.name = "ApiError"; this.status = status; this.result = result; }
  }

  function reasonText(value) {
    if (!value) return "";
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return value.map(reasonText).filter(Boolean).join(" · ");
    if (typeof value !== "object") return String(value);
    if (value.reason) return reasonText(value.reason);
    if (value.message) return reasonText(value.message);
    if (value.error) return reasonText(value.error);
    return Object.entries(value).map(([key, item]) => {
      const reason = reasonText(item);
      return reason ? `${key}: ${reason}` : "";
    }).filter(Boolean).join(" · ");
  }

  async function request(path, { method = "GET", body, detached = false, keepalive = false } = {}) {
    const controller = new AbortController();
    if (!detached) state.controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(`${API}${path}`, {
        method, headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal, cache: "no-store", keepalive,
      });
      const result = await response.json().catch(() => { throw new ApiError("서버가 JSON 응답을 반환하지 않았습니다.", response.status, null); });
      if (!response.ok || (path !== "/health" && (!['success', 'partial'].includes(result.status) || result.data == null))) {
        throw new ApiError(reasonText(result.diagnostics) || reasonText(result.error) || `요청 실패 (HTTP ${response.status})`, response.status, result);
      }
      return result;
    } catch (error) {
      if (error.name === "AbortError") throw new ApiError("요청이 중단되었거나 응답 시간이 초과되었습니다.", 0, null);
      throw error;
    } finally {
      clearTimeout(timeout);
      state.controllers.delete(controller);
    }
  }

  const sessionPath = (session = state.session) => `/sessions/${encodeURIComponent(session.session_id)}`;
  const context = () => ({ generation: state.generation, sessionId: state.session?.session_id, epoch: state.session?.command_epoch, geometry: state.session?.geometry_version, calibration: state.session?.calibration_version, controlRevision: state.controlRevision });
  const current = (ctx) => ctx.generation === state.generation && ctx.sessionId === state.session?.session_id && ctx.epoch === state.session?.command_epoch;
  const sameVersions = (ctx) => current(ctx) && ctx.geometry === state.session?.geometry_version && ctx.calibration === state.session?.calibration_version;
  const capturing = () => Boolean(state.capture && LIVE_STAGES.has(state.capture.stage));
  const unavailable = () => state.connectionBusy || state.controlBusy || capturing();

  function notice(message, tone = "") { $("notice").textContent = message; $("notice").dataset.tone = tone; }
  function status(id, text, tone = "") { $(id).textContent = text; $(id).dataset.tone = tone; }
  function failure(error, prefix = "") {
    state.diagnostics = error.result?.diagnostics || { reason: error.message };
    notice(`${prefix}${error.message}`, "error");
    render(true);
  }
  function clearOverlay() {
    state.observed = null;
    overlayContext.clearRect(0, 0, overlay.width, overlay.height);
  }
  function flushPending() { state.latest = null; }

  // The caller must first validate its request context. An accepted mutation may advance epoch.
  function applySession(snapshot) {
    if (!snapshot || snapshot.session_id !== state.session?.session_id) return false;
    if (!Number.isInteger(snapshot.command_epoch) || snapshot.command_epoch < state.session.command_epoch) return false;
    if (!Number.isInteger(snapshot.geometry_version) || !Number.isInteger(snapshot.calibration_version) || snapshot.geometry_version < state.session.geometry_version || snapshot.calibration_version < state.session.calibration_version) return false;
    const changed = snapshot.command_epoch !== state.session.command_epoch || snapshot.geometry_version !== state.session.geometry_version || snapshot.calibration_version !== state.session.calibration_version;
    if (changed) {
      flushPending(); clearOverlay(); state.readyConfirmed = false;
      if (capturing() && snapshot.device_state?.calibration !== "collecting" && snapshot.lifecycle_phase !== "calibrating") {
        state.capture = { ...state.capture, stage: "cancelled", diagnostics: { reason: "세션 세대가 변경되어 수집이 종료되었습니다." } };
        state.captureToken += 1;
      }
    }
    state.session = snapshot;
    if (!["ready", "running"].includes(snapshot.lifecycle_phase)) state.readyConfirmed = false;
    state.inputSummary = snapshot.lifecycle_phase === "running" ? snapshot.input_run : null;
    return true;
  }

  async function runControl(work) {
    if (state.controlBusy || state.connectionBusy || !state.session) return;
    const generation = state.generation;
    state.controlBusy = true;
    state.controlRevision += 1;
    flushPending();
    render();
    try {
      // Serialize mutations after the single frame POST; never relabel a queued frame.
      if (state.inFlight) await state.inFlight.promise;
      if (generation !== state.generation || !state.session) return;
      await work(context());
    } catch (error) {
      if (generation === state.generation) {
        failure(error);
        if (error.status === 409) await pollSession(true);
      }
    } finally {
      if (generation === state.generation) {
        state.controlBusy = false;
        render(true);
        if (!capturing() && (state.geometryPending || state.deviceSignature !== deviceSignature())) scheduleSync();
      }
    }
  }

  async function enumerateCameras() {
    if (!navigator.mediaDevices?.enumerateDevices) throw new Error("카메라 API를 사용할 수 없습니다. localhost 또는 HTTPS에서 접속하세요.");
    const selected = $("camera-select").value;
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
    const select = $("camera-select");
    select.replaceChildren();
    if (!devices.length) select.add(new Option("표시할 카메라 없음", ""));
    else select.add(new Option("사용할 카메라를 선택하세요", ""));
    devices.forEach((device, index) => {
      const option = new Option(device.label || `카메라 ${index + 1} · 권한 허용 후 이름 표시`, device.deviceId);
      option.disabled = !device.deviceId;
      select.add(option);
    });
    if (devices.some((d) => d.deviceId === selected && selected)) select.value = selected;
    else if (state.deviceId && devices.some((d) => d.deviceId === state.deviceId)) select.value = state.deviceId;
    else select.value = ""; // No implicit replacement of a disappeared device.
    $("camera-help").textContent = devices.some((d) => !d.label) ? "권한 허용 전에는 장치명이 비어 있을 수 있습니다. 이름 확인 후 원하는 카메라를 선택하세요." : `${devices.length}개 장치 · 연결할 카메라를 직접 선택하세요. 연결 중인 장치는 프리뷰 위에 표시됩니다.`;
    render();
  }

  async function requestPermission() {
    if (unavailable() || state.stream) return;
    state.connectionBusy = true;
    state.permission = "requesting";
    render();
    let permissionStream;
    try {
      // This temporary stream grants permission only; it is never used as a fallback preview.
      permissionStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      state.permission = "granted";
      permissionStream.getTracks().forEach((track) => track.stop());
      permissionStream = null;
      await enumerateCameras();
      notice("권한이 허용되었습니다. 이름을 확인하고 사용할 장치를 선택한 뒤 연결하세요.");
    } catch (error) {
      state.permission = error.name === "NotAllowedError" ? "denied" : "unknown";
      failure(error, "카메라 권한 확인 실패: ");
    } finally {
      permissionStream?.getTracks().forEach((track) => track.stop());
      state.connectionBusy = false;
      render();
    }
  }

  function stopFrameLoop() {
    if (state.frameHandle != null) {
      if (state.frameMode === "video") video.cancelVideoFrameCallback?.(state.frameHandle);
      else cancelAnimationFrame(state.frameHandle);
    }
    state.frameHandle = null;
  }

  async function closeRemote(session, keepalive = false) {
    let actual = session;
    if (!keepalive) {
      try { const result = await request(sessionPath(session), { detached: true }); actual = result.data.session || result.data; } catch { /* Attempt close with the last acknowledged epoch as well. */ }
    }
    if (actual.lifecycle_phase === "closed") return;
    await request(`${sessionPath(session)}/close`, { method: "POST", body: { command_epoch: actual.command_epoch }, detached: true, keepalive });
  }

  async function teardown() {
    const oldSession = state.session || state.retiredSession;
    state.generation += 1;
    stopFrameLoop();
    clearTimeout(state.syncTimer);
    clearTimeout(geometryTimer);
    state.syncTimer = null;
    state.controllers.forEach((controller) => controller.abort());
    state.stream?.getTracks().forEach((track) => track.stop());
    video.pause();
    video.srcObject = null;
    state.stream = null;
    state.session = null;
    state.retiredSession = oldSession;
    state.camera = "disconnected";
    state.capture = null;
    state.captureToken += 1;
    state.finishing = false;
    state.controlBusy = false;
    state.geometryPending = false;
    state.geometrySignature = "";
    state.deviceSignature = "";
    state.syncBlocked = false;
    state.lastFrameAt = 0;
    state.lastMediaTime = -1;
    state.lastPresented = -1;
    state.frameSequence = 0;
    state.readyConfirmed = false;
    state.readyCalibration = null;
    state.quality = null;
    flushPending();
    clearOverlay();
    render(true);
    if (state.inFlight) await state.inFlight.promise;
    if (oldSession) {
      await closeRemote(oldSession);
      if (state.retiredSession?.session_id === oldSession.session_id) state.retiredSession = null;
    }
  }

  async function createSession(generation) {
    const result = await request("/sessions", { method: "POST", detached: true, body: {
      clock_id: clockId, selected_device_id: state.deviceId, selected_hand: state.selectedHand,
      active_fingers: [...state.activeFingers], input_source: "camera_pose", exercise_ref: "builtin-ceg.v1", policy_version: "b01-policy.v1",
    } });
    const snapshot = result.data;
    if (!snapshot.session_id || !Number.isInteger(snapshot.command_epoch) || !Number.isInteger(snapshot.geometry_version) || !Number.isInteger(snapshot.calibration_version)) throw new Error("세션 응답에 필수 ID·버전이 없습니다.");
    if (generation !== state.generation) { await closeRemote(snapshot); return false; }
    state.session = snapshot;
    state.diagnostics = result.diagnostics;
    state.geometryPending = true;
    state.geometrySignature = "";
    state.deviceSignature = "";
    state.syncBlocked = false;
    state.received = 0; state.processed = 0; state.skipped = 0; state.latency = null;
    state.capture = null; state.captureToken += 1;
    state.readyConfirmed = false; state.readyCalibration = null; state.quality = null;
    clearOverlay();
    return true;
  }

  async function connectCamera() {
    if (unavailable()) return;
    const chosenId = $("camera-select").value;
    if (!chosenId) { notice("권한 허용 후 목록에서 사용할 카메라를 직접 선택하세요.", "error"); return; }
    if (!state.activeFingers.length) { notice("활성 손가락을 하나 이상 선택하세요.", "error"); return; }
    state.connectionBusy = true;
    render();
    let generation;
    try {
      await teardown(); // The old stream AND session are closed before reconnecting.
      generation = state.generation;
      state.camera = "connecting"; state.permission = "requesting";
      render();
      const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { deviceId: { exact: chosenId } } });
      if (generation !== state.generation) { stream.getTracks().forEach((track) => track.stop()); return; }
      const track = stream.getVideoTracks()[0];
      if (!track) { stream.getTracks().forEach((item) => item.stop()); throw new Error("선택한 장치에 영상 트랙이 없습니다."); }
      const settings = track.getSettings();
      if (settings.deviceId && settings.deviceId !== chosenId) { stream.getTracks().forEach((item) => item.stop()); throw new Error("선택한 카메라와 실제 연결 장치가 다릅니다. 자동 대체하지 않았습니다."); }
      state.stream = stream;
      state.deviceId = chosenId;
      state.deviceLabel = track.label || "선택한 카메라 (운영체제 이름 미제공)";
      state.permission = "granted";
      track.addEventListener("ended", () => { if (state.stream === stream) void handleStreamEnd(); });
      video.srcObject = stream;
      await video.play();
      if (generation !== state.generation) return;
      if (!await createSession(generation)) return;
      startFrameLoop(generation);
      await enumerateCameras();
      notice("선택한 카메라를 연결했습니다. 새 영상을 확인한 뒤 손 추적 모델을 준비하세요.");
    } catch (error) {
      if (generation === undefined || generation === state.generation) {
        state.stream?.getTracks().forEach((track) => track.stop());
        video.srcObject = null; state.stream = null; state.camera = "error";
        if (error.name === "NotAllowedError") state.permission = "denied";
        if (state.session) {
          const unused = state.session;
          state.session = null; state.retiredSession = unused;
          try { await closeRemote(unused); state.retiredSession = null; } catch { /* Expose retry through 연결 종료. */ }
        }
        failure(error, "선택 장치 연결 실패: ");
      }
    } finally {
      if (generation === undefined || generation === state.generation) {
        state.connectionBusy = false;
        render(true);
        if (state.session) scheduleSync();
      }
    }
  }

  async function stopCamera() {
    if (state.connectionBusy || capturing()) return;
    state.connectionBusy = true; render();
    try { await teardown(); notice("카메라와 이전 세션을 종료했습니다."); }
    catch (error) { failure(error, "영상은 종료했지만 서버 세션 종료를 확인하지 못했습니다. 연결 종료를 다시 눌러주세요. "); }
    finally { state.connectionBusy = false; render(true); }
  }

  async function handleStreamEnd() {
    state.connectionBusy = true;
    try { await teardown(); notice("카메라 연결이 종료되었습니다. 장치를 확인한 뒤 직접 다시 연결하세요.", "error"); }
    catch (error) { failure(error, "카메라가 종료되었으며 서버 세션 종료 확인이 필요합니다. "); }
    finally { state.camera = "ended"; state.connectionBusy = false; render(true); }
  }

  function modelStatus(model, sequence) {
    if (!model || !["loading", "ready", "error", "not_loaded"].includes(model.state)) return;
    if (sequence < state.modelResponseSequence) return;
    state.modelResponseSequence = sequence;
    const changed = state.tracker !== model.state;
    state.model = model; state.tracker = model.state;
    if (changed) {
      if (model.state === "error") { flushPending(); clearOverlay(); notice(`추적 모델 오류: ${reasonText(model.error) || "모델 준비를 다시 시도하세요."} 카메라 프리뷰는 유지됩니다.`, "error"); }
      if (model.state === "ready") { state.syncBlocked = false; notice("Python 추적 모델이 준비되었습니다. 선택한 손을 영상 안에 두세요.", "good"); scheduleSync(); }
    }
  }

  async function pollHealth() {
    if (state.healthBusy) return;
    state.healthBusy = true;
    const sequence = ++state.modelRequestSequence;
    try {
      const health = await request("/health", { detached: true });
      state.health = health;
      modelStatus(health.model, sequence);
      status("server-status", `Python 서버 · ${typeof health.runtime === "string" ? health.runtime : "연결됨"}`, "good");
    } catch (error) { status("server-status", "서버 연결 확인 필요", "error"); state.health = { error: error.message }; }
    finally { state.healthBusy = false; render(); }
  }

  async function prepareModel() {
    if (state.tracker === "loading" || unavailable()) return;
    const sequence = ++state.modelRequestSequence;
    state.modelResponseSequence = sequence;
    state.tracker = "loading"; render();
    try {
      const result = await request("/model/prepare", { method: "POST", body: {}, detached: true });
      modelStatus(result.data, sequence);
      if (state.tracker === "loading") { notice("추적 모델을 준비하고 있습니다. 카메라 영상은 계속 표시됩니다."); await pollHealth(); }
    } catch (error) { if (sequence >= state.modelResponseSequence) { state.tracker = "error"; failure(error, "추적 모델 준비 실패: "); } }
    render(true);
  }

  function geometry() {
    const width = $("preview-stage").clientWidth;
    const height = $("preview-stage").clientHeight;
    const scale = video.videoWidth && video.videoHeight ? Math.min(width / video.videoWidth, height / video.videoHeight) : 1;
    const contentWidth = video.videoWidth ? video.videoWidth * scale : width;
    const contentHeight = video.videoHeight ? video.videoHeight * scale : height;
    const offsetX = (width - contentWidth) / 2, offsetY = (height - contentHeight) / 2;
    const left = offsetX + contentWidth * Number($("key-left").value) / 100;
    const top = offsetY + contentHeight * Number($("key-top").value) / 100;
    return {
      source_size: { width: video.videoWidth, height: video.videoHeight }, display_size: { width, height },
      object_fit: "contain", flip_x: state.flipX, flip_y: state.flipY,
      keyboard_rect: { left, top, width: Math.min(contentWidth * Number($("key-width").value) / 100, offsetX + contentWidth - left), height: Math.min(contentHeight * Number($("key-height").value) / 100, offsetY + contentHeight - top) },
    };
  }

  function updateGeometryUI() {
    const left = $("key-left"), top = $("key-top"), width = $("key-width"), height = $("key-height");
    if (+left.value + +width.value > 100) width.value = String(100 - +left.value);
    if (+top.value + +height.value > 100) height.value = String(100 - +top.value);
    for (const id of ["key-left", "key-top", "key-width", "key-height"]) $(`${id}-value`).textContent = `${$(id).value}%`;
    const rect = geometry().keyboard_rect;
    Object.assign($("keyboard-guide").style, Object.fromEntries(Object.entries(rect).map(([key, value]) => [key, `${value}px`])));
    $("source-layer").style.setProperty("--flip-x", state.flipX ? "-1" : "1");
    $("source-layer").style.setProperty("--flip-y", state.flipY ? "-1" : "1");
    for (const [id, value] of [["flip-x", state.flipX], ["flip-y", state.flipY]]) { $(id).setAttribute("aria-pressed", String(value)); $(id).querySelector("span").textContent = value ? "켜짐" : "꺼짐"; }
  }

  function geometryChanged() {
    updateGeometryUI();
    clearOverlay(); flushPending();
    if (!state.session) return;
    state.geometryPending = true;
    state.syncBlocked = false;
    state.readyConfirmed = false;
    clearTimeout(geometryTimer);
    geometryTimer = setTimeout(scheduleSync, 180);
    render();
  }
  function deviceSignature() { return `${state.deviceId}/${state.deviceLabel}/${video.videoWidth}/${video.videoHeight}/${state.tracker}`; }
  function scheduleSync() {
    if (state.syncTimer || state.syncBlocked) return;
    state.syncTimer = setTimeout(() => { state.syncTimer = null; void syncDeviceAndGeometry(); }, 120);
  }

  async function syncDeviceAndGeometry() {
    if (!state.session || !state.stream || !video.videoWidth || !video.videoHeight || state.camera !== "receiving" || state.connectionBusy || state.controlBusy || state.syncBlocked) return;
    await runControl(async (ctx) => {
      try {
      if (capturing() && state.geometryPending) {
        await cancelCaptureRequest(ctx);
        if (ctx.generation !== state.generation) return;
        ctx = context();
        notice("화면 크기가 바뀌어 수집을 취소했습니다. 새 배치 적용 후 다시 수집하세요.", "error");
      }
      if (capturing()) return;
      const signature = deviceSignature();
      if (signature !== state.deviceSignature) {
        const result = await request(`${sessionPath()}/device`, { method: "POST", body: {
          command_epoch: ctx.epoch, permission: "granted", stream: "receiving", device_label: state.deviceLabel,
          width: video.videoWidth, height: video.videoHeight,
          // Do not supply tracker:'ready'. Only the Python runtime can assert tracker readiness.
        } });
        if (!current(ctx)) return;
        if (!applySession(result.data.session || result.data)) throw new Error("장치 상태 응답에 유효한 세션 버전이 없습니다.");
        state.deviceSignature = signature;
        ctx = context();
      }
      const next = geometry();
      const nextSignature = JSON.stringify(next);
      if (!next.display_size.width || !next.display_size.height) return;
      if (nextSignature !== state.geometrySignature) {
        const result = await request(`${sessionPath()}/geometry`, { method: "PUT", body: { command_epoch: ctx.epoch, expected_geometry_version: ctx.geometry, geometry: next } });
        if (!current(ctx)) return;
        if (!applySession(result.data.session || result.data)) throw new Error("화면 기하 응답에 유효한 세션 버전이 없습니다.");
        state.geometrySignature = nextSignature;
        state.diagnostics = result.diagnostics;
      }
      state.geometryPending = JSON.stringify(geometry()) !== state.geometrySignature;
      updateGeometryUI();
      } catch (error) { state.syncBlocked = true; throw error; }
    });
  }

  function startFrameLoop(generation) {
    stopFrameLoop();
    const supportsVideoCallback = typeof video.requestVideoFrameCallback === "function";
    state.frameMode = supportsVideoCallback ? "video" : "animation";
    const tick = (_now, metadata) => {
      if (generation !== state.generation || !state.stream) return;
      const mediaTime = metadata?.mediaTime ?? video.currentTime;
      const presented = metadata?.presentedFrames;
      const fresh = Number.isFinite(mediaTime) && mediaTime !== state.lastMediaTime && (presented === undefined || presented !== state.lastPresented);
      if (video.readyState >= 2 && fresh) {
        state.lastMediaTime = mediaTime;
        if (presented !== undefined) state.lastPresented = presented;
        state.lastFrameAt = performance.now();
        const wasReceiving = state.camera === "receiving";
        state.camera = "receiving";
        state.received += 1;
        if (!wasReceiving) { updateGeometryUI(); render(); scheduleSync(); }
        if (state.geometrySignature && JSON.stringify(geometry()) !== state.geometrySignature) { state.geometryPending = true; flushPending(); scheduleSync(); }
        enqueueFrame(state.lastFrameAt);
      }
      state.frameHandle = supportsVideoCallback ? video.requestVideoFrameCallback(tick) : requestAnimationFrame(tick);
    };
    state.frameHandle = supportsVideoCallback ? video.requestVideoFrameCallback(tick) : requestAnimationFrame(tick);
  }

  function enqueueFrame(tMs) {
    if (!state.session || state.controlBusy || state.connectionBusy || state.geometryPending || state.syncBlocked || state.tracker !== "ready" || state.deviceSignature !== deviceSignature()) return;
    const ctx = context();
    const captureId = capturing() ? state.capture.capture_id : null;
    // Bind every transport field BEFORE enqueue. A later epoch/capture may never relabel these pixels.
    const packet = {
      command_epoch: ctx.epoch, geometry_version: ctx.geometry, calibration_version: ctx.calibration, clock_id: clockId,
      frame_id: ++state.frameSequence, t_ms: tMs,
    };
    if (captureId) packet.capture_id = captureId;
    encoder.width = Math.min(640, video.videoWidth);
    encoder.height = Math.max(1, Math.round(video.videoHeight * encoder.width / video.videoWidth));
    try {
      encoderContext.drawImage(video, 0, 0, encoder.width, encoder.height); // Source pixels, never CSS-mirrored pixels.
      packet.image = encoder.toDataURL("image/jpeg", 0.8);
    } catch (error) { failure(error, "프레임 획득 실패: "); return; }
    if (state.latest) state.skipped += 1;
    state.latest = Object.freeze({ ctx: Object.freeze(ctx), captureId, captureToken: state.captureToken, packet: Object.freeze(packet) });
    pumpFrames();
  }

  function frameCurrent(item) {
    return sameVersions(item.ctx) && item.captureToken === state.captureToken && item.captureId === (capturing() ? state.capture.capture_id : null) && !state.geometryPending;
  }

  function pumpFrames() {
    if (state.inFlight || !state.latest || state.controlBusy || state.connectionBusy) return;
    const item = state.latest;
    state.latest = null;
    if (!frameCurrent(item)) return;
    const flight = { promise: null };
    state.inFlight = flight;
    flight.promise = (async () => {
      const started = performance.now();
      try {
        const result = await request(`/sessions/${encodeURIComponent(item.ctx.sessionId)}/frames`, { method: "POST", body: item.packet });
        if (!frameCurrent(item)) return;
        const data = result.data;
        if (!applySession(data.session)) throw new Error("프레임 응답에 유효한 세션 스냅샷이 없습니다.");
        state.processed += 1;
        state.latency = performance.now() - started;
        state.diagnostics = result.diagnostics;
        if (sameVersions(item.ctx)) {
          state.observed = data.observation;
          state.observationAt = performance.now();
          state.quality = data.quality;
          drawLandmarks();
          if (data.capture && item.captureId === data.capture.capture_id) updateCapture(data.capture, item.captureToken);
          if (data.input) handleInput(item.ctx, data.input);
        }
        render();
      } catch (error) {
        if (frameCurrent(item)) {
          clearOverlay(); state.quality = { reason_codes: [error.message] };
          failure(error, "프레임 처리 보류: ");
          if (error.status === 409) void pollSession(true);
          if (error.status === 503) void pollHealth();
        }
      } finally {
        if (state.inFlight === flight) state.inFlight = null;
        pumpFrames();
      }
    })();
  }

  function drawLandmarks() {
    const width = $("preview-stage").clientWidth, height = $("preview-stage").clientHeight;
    const ratio = window.devicePixelRatio || 1;
    if (overlay.width !== Math.round(width * ratio) || overlay.height !== Math.round(height * ratio)) { overlay.width = Math.round(width * ratio); overlay.height = Math.round(height * ratio); }
    overlayContext.setTransform(ratio, 0, 0, ratio, 0, 0);
    overlayContext.clearRect(0, 0, width, height);
    if (!video.videoWidth || !video.videoHeight || !state.observed || state.geometryPending || state.camera !== "receiving") return;
    const scale = Math.min(width / video.videoWidth, height / video.videoHeight);
    const imageWidth = video.videoWidth * scale, imageHeight = video.videoHeight * scale;
    const offsetX = (width - imageWidth) / 2, offsetY = (height - imageHeight) / 2;
    // The shared source-layer CSS flips both contain-aligned video and this unflipped overlay.
    for (const hand of state.observed.hands || []) {
      const points = hand.image_xy;
      if (!Array.isArray(points) || points.length !== 21) continue;
      const valid = (index) => Array.isArray(points[index]) && Number.isFinite(points[index][0]) && Number.isFinite(points[index][1]);
      const xy = (index) => [offsetX + imageWidth * points[index][0], offsetY + imageHeight * points[index][1]];
      overlayContext.strokeStyle = hand.handedness?.toLowerCase() === state.selectedHand ? "#91ead0" : "#edd78b";
      overlayContext.fillStyle = overlayContext.strokeStyle;
      overlayContext.lineWidth = 1.7;
      for (const [a, b] of BONES) if (valid(a) && valid(b)) { overlayContext.beginPath(); overlayContext.moveTo(...xy(a)); overlayContext.lineTo(...xy(b)); overlayContext.stroke(); }
      for (let i = 0; i < points.length; i += 1) if (valid(i)) { overlayContext.beginPath(); overlayContext.arc(...xy(i), [4,8,12,16,20].includes(i) ? 3.6 : 2.2, 0, Math.PI * 2); overlayContext.fill(); }
    }
  }

  async function changeSelection() {
    if (unavailable()) return;
    const hand = document.querySelector('input[name="selected-hand"]:checked').value;
    const fingers = [...document.querySelectorAll('input[name="active-finger"]:checked')].map((input) => input.value);
    state.selectedHand = hand; state.activeFingers = fingers;
    state.capture = null; state.captureToken += 1; state.readyConfirmed = false; state.readyCalibration = null; state.diagnostics = null; state.quality = null;
    flushPending(); clearOverlay();
    if (!state.session && !state.stream && !state.retiredSession) { notice(fingers.length ? "선택한 손과 손가락으로 새 세션을 준비합니다." : "활성 손가락을 하나 이상 선택하세요."); render(true); return; }
    state.connectionBusy = true; render();
    const old = state.session || state.retiredSession;
    state.generation += 1;
    const generation = state.generation;
    stopFrameLoop();
    state.session = null;
    state.retiredSession = old;
    state.geometryPending = true;
    try {
      if (state.inFlight) await state.inFlight.promise;
      if (old) await closeRemote(old);
      state.retiredSession = null;
      if (generation !== state.generation) return;
      if (fingers.length && state.stream) { await createSession(generation); startFrameLoop(generation); notice("손·손가락 선택이 바뀌어 새 세션을 만들었습니다. 기준 자세부터 다시 보정하세요."); }
      else if (state.stream) { startFrameLoop(generation); notice("이전 세션을 종료했습니다. 활성 손가락을 하나 이상 선택하면 새 세션을 준비합니다.", "error"); }
      else notice("선택을 저장했습니다. 사용할 카메라를 연결하세요.");
    } catch (error) { if (generation === state.generation) failure(error, "새 세션 준비 실패: "); }
    finally { if (generation === state.generation) { state.connectionBusy = false; render(true); if (state.session) scheduleSync(); } }
  }

  function updateCapture(progress, token) {
    if (token !== state.captureToken || !state.capture || progress.capture_id !== state.capture.capture_id) return;
    state.capture = { ...state.capture, ...progress };
    render();
    // Prefer the current server contract. An elapsed browser timer never finishes a capture.
    const ready = typeof progress.ready_to_finish === "boolean" ? progress.ready_to_finish : progress.can_finish === true || (progress.stage === "collecting" && Number.isFinite(progress.remaining_ms) && progress.remaining_ms <= 0);
    if (ready && progress.stage === "collecting" && !state.finishing) void finishCapture();
  }

  async function startCapture(phase, finger) {
    if (unavailable() || !state.session || state.geometryPending || state.tracker !== "ready" || state.camera !== "receiving") return;
    await runControl(async (ctx) => {
      const token = ++state.captureToken;
      state.capture = null;
      const result = await request(`${sessionPath()}/calibration/captures`, { method: "POST", body: { command_epoch: ctx.epoch, phase, finger } });
      if (!current(ctx) || token !== state.captureToken) return;
      if (result.data.session) applySession(result.data.session);
      if (!result.data.capture_id) throw new Error("수집 응답에 capture_id가 없습니다.");
      state.capture = { ...result.data, phase, finger, stage: result.data.stage || "preparing" };
      state.diagnostics = result.diagnostics;
      notice(phase === "rest" ? "손을 자연스럽게 둔 기준 자세를 유지하세요. 다음 새 프레임부터 서버가 준비 시간을 계산합니다." : `${FINGERS[finger]}를 편안하게 들고 유지하세요. 손 전체의 자세는 바꾸지 마세요.`);
    });
  }

  async function finishCapture() {
    if (!capturing() || state.finishing || state.controlBusy) return;
    const token = state.captureToken;
    const generation = state.generation;
    const captureId = state.capture.capture_id;
    state.finishing = true;
    await runControl(async (ctx) => {
      if (token !== state.captureToken) return;
      state.capture = { ...state.capture, stage: "validating" }; render();
      const result = await request(`${sessionPath()}/calibration/captures/${encodeURIComponent(captureId)}/finish`, { method: "POST", body: { command_epoch: ctx.epoch } });
      if (!current(ctx) || token !== state.captureToken) return;
      if (!applySession(result.data.session)) throw new Error("보정 완료 응답에 새 세션 버전이 없습니다.");
      if (result.outcome === "needs_more_samples") { state.capture = { ...state.capture, ...result.data.capture }; return; }
      const passed = result.data.capture?.stage === "passed" || ["rest_saved", "calibrated"].includes(result.outcome);
      state.capture = { ...state.capture, ...result.data.capture, stage: passed ? "passed" : "failed", diagnostics: result.diagnostics };
      state.readyCalibration = result.data.calibration;
      state.diagnostics = result.diagnostics;
      state.captureToken += 1;
      flushPending();
      notice(passed ? (state.capture.phase === "rest" ? "기준 자세를 저장했습니다. 선택한 손가락의 들림 자세를 각각 수집하세요." : `${FINGERS[state.capture.finger]} 보정을 완료했습니다. 기준 자세로 돌아와 입력 준비를 확인하세요.`) : `보정 검사에 통과하지 못했습니다. ${reasonText(result.diagnostics) || reasonText(result.data.capture?.diagnostics) || "상세 진단을 확인하고 다시 수집하세요."}`, passed ? "good" : "error");
    });
    if (generation !== state.generation) return;
    if (token === state.captureToken && state.capture?.stage === "validating") state.capture = { ...state.capture, stage: "failed", diagnostics: state.diagnostics };
    state.finishing = false; render(true);
  }

  async function cancelCaptureRequest(ctx) {
    const captureId = state.capture?.capture_id;
    if (!captureId) return;
    const result = await request(`${sessionPath()}/calibration/captures/${encodeURIComponent(captureId)}?command_epoch=${ctx.epoch}`, { method: "DELETE" });
    if (!current(ctx)) return;
    if (result.data.session) applySession(result.data.session);
    state.capture = { ...state.capture, ...(result.data.capture || result.data), stage: "cancelled" };
    state.captureToken += 1;
    state.finishing = false;
    flushPending();
    state.diagnostics = result.diagnostics;
  }

  async function cancelCapture() {
    if (!capturing() || state.controlBusy) return;
    await runControl(async (ctx) => { await cancelCaptureRequest(ctx); notice("수집을 취소했습니다. 기존에 발급된 다른 유효 보정은 서버 상태에 따라 유지됩니다."); });
  }

  async function checkReady() {
    if (unavailable() || state.geometryPending) return;
    await runControl(async (ctx) => {
      const result = await request(`${sessionPath()}/ready`, { method: "POST", body: { command_epoch: ctx.epoch } });
      if (!current(ctx)) return;
      if (!applySession(result.data.session)) throw new Error("입력 준비 응답에 유효한 세션이 없습니다.");
      state.readyCalibration = result.data.calibration;
      state.readyConfirmed = state.session.lifecycle_phase === "ready" && state.session.device_state?.input === "ready";
      state.diagnostics = result.diagnostics;
      notice(state.readyConfirmed ? "입력 준비가 확인되었습니다. 보정 기록을 JSON으로 저장할 수 있습니다." : `아직 입력 준비 조건을 만족하지 않았습니다. ${reasonText(result.diagnostics)}`, state.readyConfirmed ? "good" : "error");
    });
  }

  function playNote(midi) {
    const audio = state.audio;
    if (!audio || !Number.isInteger(midi)) return;
    try {
      const osc = audio.createOscillator(), gain = audio.createGain(), t = audio.currentTime;
      osc.frequency.value = 440 * 2 ** ((midi - 69) / 12);
      gain.gain.setValueAtTime(0.0001, t); gain.gain.exponentialRampToValueAtTime(0.25, t + 0.01); gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.35);
      osc.connect(gain).connect(audio.destination); osc.start(t); osc.stop(t + 0.4);
    } catch (_) { /* 음 재생 실패가 입력 사건을 지우지 않는다(PRD 5.1). */ }
  }

  function flashKey(key, withheld) {
    const spans = $("keyboard-guide").querySelectorAll ? [...$("keyboard-guide").querySelectorAll("span")] : [];
    const index = KEY_ORDER.indexOf(key);
    if (index >= 0 && spans[index]) { spans[index].classList?.add("hit"); setTimeout(() => spans[index].classList?.remove("hit"), 180); }
    if (withheld) { $("keyboard-guide").classList?.add("withheld"); setTimeout(() => $("keyboard-guide").classList?.remove("withheld"), 250); }
  }

  function handleInput(ctx, input) {
    state.inputSummary = input.summary;
    const byDecision = new Map();
    for (const proposal of input.proposals || []) {
      if (state.seenEvents.has(proposal.event_id) || !sameVersions(ctx)) continue; // 중복·오래된 결과로 소리/표시하지 않는다.
      state.seenEvents.add(proposal.event_id);
      const emitted = performance.now();
      const accepted = proposal.input_status === "accepted_provisional";
      if (accepted) { flashKey(proposal.virtual_key, false); playNote(proposal.midi_note); } else flashKey(null, true);
      state.lastInput = { ...proposal, emitted_ms: emitted };
      if (!byDecision.has(proposal.decision_ref)) byDecision.set(proposal.decision_ref, []);
      byDecision.get(proposal.decision_ref).push({ event_id: proposal.event_id, emitted_ms: emitted });
    }
    for (const [decisionRef, events] of byDecision) void publishEvents(ctx, decisionRef, events, 0);
  }

  async function publishEvents(ctx, decisionRef, events, attempt) {
    try {
      const result = await request(`${sessionPath()}/events/ack`, { method: "POST", body: { command_epoch: ctx.epoch, decision_ref: decisionRef, clock_id: clockId, events } });
      if (current(ctx) && result.data?.summary) { state.inputSummary = result.data.summary; render(); }
    } catch (error) {
      // 같은 emitted_ms로 유한 재전송. 이미 난 소리는 다시 내지 않는다.
      if (attempt < 2 && current(ctx) && error.status !== 409 && error.status !== 422) setTimeout(() => void publishEvents(ctx, decisionRef, events, attempt + 1), 200 * (attempt + 1));
      else state.ackFailures += 1;
    }
  }

  async function startInput() {
    if (unavailable() || !state.readyConfirmed) return;
    try { state.audio = state.audio || new (window.AudioContext || window.webkitAudioContext)(); await state.audio.resume?.(); } catch (_) { state.audio = null; }
    await runControl(async (ctx) => {
      const result = await request(`${sessionPath()}/start`, { method: "POST", body: { command_epoch: ctx.epoch } });
      if (!current(ctx)) return;
      if (!applySession(result.data.session)) throw new Error("입력 시작 응답에 유효한 세션이 없습니다.");
      state.seenEvents.clear(); state.lastInput = null;
      const running = result.outcome === "input_running";
      notice(running ? "입력 점검을 시작했습니다. 손가락을 들었다 내려 보세요." : `입력을 시작할 수 없습니다. ${reasonText(result.diagnostics)}`, running ? "good" : "error");
    });
  }

  async function stopInput() {
    if (unavailable() || state.session?.lifecycle_phase !== "running") return;
    await runControl(async (ctx) => {
      const result = await request(`${sessionPath()}/pause`, { method: "POST", body: { command_epoch: ctx.epoch } });
      if (!current(ctx)) return;
      applySession(result.data.session);
      const counts = result.data.last_run?.counts;
      notice(counts ? `입력 점검 종료 · 후보 ${counts.candidates}개 (건반 ${counts.accepted_provisional}, 보류 ${counts.withheld}). 다시 시작하려면 입력 준비 확인부터 하세요.` : "입력 점검을 종료했습니다.", "good");
    });
  }

  async function exportCalibration() {
    if (unavailable()) return;
    await runControl(async (ctx) => {
      const result = await request(`${sessionPath()}/exports`, { method: "POST", body: { command_epoch: ctx.epoch } });
      if (!current(ctx)) return;
      const blob = new Blob([JSON.stringify(result.data, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `b01-calibration-${ctx.sessionId}.json`;
      document.body.append(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      notice("서버의 전체 보정 기록을 JSON으로 저장했습니다. 원본 영상 다운로드는 포함하지 않습니다.", "good");
    });
  }

  async function pollSession(force = false) {
    if (!state.session || state.sessionPollBusy || (!force && (state.controlBusy || state.connectionBusy))) return;
    const ctx = context();
    const token = state.captureToken;
    state.sessionPollBusy = true;
    try {
      const result = await request(`/sessions/${encodeURIComponent(ctx.sessionId)}`);
      if (!current(ctx) || ctx.controlRevision !== state.controlRevision) return;
      if (!applySession(result.data.session || result.data)) return;
      if (result.data.quality) state.quality = result.data.quality;
      if (result.data.capture) updateCapture(result.data.capture, token);
      render();
    } catch (error) { if (current(ctx)) { state.diagnostics = error.result?.diagnostics || { reason: error.message }; render(); } }
    finally { state.sessionPollBusy = false; }
  }

  function renderFingerRows() {
    const root = $("finger-calibrations");
    const key = state.activeFingers.join(",");
    if (root.dataset.fingers !== key) {
      root.replaceChildren(); root.dataset.fingers = key;
      for (const finger of state.activeFingers) {
        const row = document.createElement("div"); row.className = "finger-row";
        const name = document.createElement("strong"); name.textContent = FINGERS[finger];
        const label = document.createElement("span"); label.className = "finger-state";
        const button = document.createElement("button"); button.className = "button"; button.type = "button"; button.textContent = "들림 수집";
        button.addEventListener("click", () => void startCapture("lift", finger));
        row.dataset.finger = finger; row.append(name, label, button); root.append(row);
      }
    }
    for (const row of root.children) {
      const calibrated = state.session?.calibrated_fingers?.includes(row.dataset.finger);
      row.querySelector(".finger-state").textContent = calibrated ? "보정 완료" : "보정 전";
      row.querySelector(".finger-state").dataset.tone = calibrated ? "good" : "";
      row.querySelector("button").disabled = unavailable() || !state.session?.baseline_ref || state.geometryPending || state.tracker !== "ready" || state.camera !== "receiving";
    }
  }

  function renderCapture() {
    const c = state.capture;
    const labels = { preparing: "준비 자세 유지", collecting: "유효 프레임 수집 중", validating: "서버 품질 검사 중", passed: "수집 검사 통과", failed: "수집 검사 실패", cancelled: "수집 취소됨" };
    $("capture-stage").textContent = c ? labels[c.stage] || c.stage : "수집 대기";
    $("cancel-capture").disabled = !capturing() || state.controlBusy || state.finishing;
    $("accepted-count").textContent = c?.accepted_count ?? 0;
    $("input-count").textContent = c?.input_count ?? 0;
    const preparation = c?.preparation_ms ?? 1000;
    const minimum = c?.min_collect_ms ?? 3000;
    const maximum = c?.max_collect_ms ?? 5000;
    const elapsed = Number.isFinite(c?.elapsed_ms) ? c.elapsed_ms : 0;
    const collected = Math.max(0, elapsed - preparation);
    $("capture-progress").value = c?.stage === "passed" ? 100 : Math.min(100, 100 * elapsed / (preparation + maximum));
    $("capture-instruction").textContent = c ? `${c.phase === "rest" ? "기준 자세" : `${FINGERS[c.finger] || c.finger} 들림`} · ${c.stage === "preparing" ? "다음 프레임부터 준비, 자세를 유지하세요." : c.stage === "collecting" ? "자세를 유지하세요. 새 프레임만 수집합니다." : "서버 결과를 확인하세요."}` : "준비 1초 · 수집 3초 · 표본 부족 시 최대 5초";
    $("capture-remaining").textContent = !c ? "서버 판정 대기" : c.stage === "preparing" ? `준비 ${Math.max(0, preparation - elapsed) / 1000}s` : c.stage === "collecting" ? (collected < minimum ? `최소 수집까지 ${((minimum - collected) / 1000).toFixed(1)}s` : `표본 추가 대기 · 최대 ${Math.max(0, maximum - collected) / 1000}s`) : "서버 판정";
    const resultText = c?.stage === "failed"
      ? reasonText(c.diagnostics || c.reason_counts || state.diagnostics) || "보정 조건을 만족하지 못했습니다. 자세와 상세 진단을 확인하세요."
      : c?.stage === "cancelled" ? reasonText(c.diagnostics) || "수집이 취소되었습니다. 다시 시작할 수 있습니다."
      : c?.stage === "passed" ? (c.phase === "rest" ? "기준 저장 완료 · 들림 보정을 진행하세요." : "보정 완료 · 입력 준비는 별도로 확인하세요.")
      : "3초에 표본이 부족하면 최대 5초까지 기다리며, 서버가 종료를 결정합니다.";
    $("capture-result").textContent = resultText;
  }

  function renderInput() {
    const running = state.session?.lifecycle_phase === "running";
    const summary = running ? state.inputSummary : null;
    status("input-status", running ? "실행 중" : state.readyConfirmed ? "시작 가능" : "대기", running ? "good" : "");
    $("input-start").disabled = unavailable() || running || !state.readyConfirmed || state.camera !== "receiving";
    $("input-stop").disabled = unavailable() || !running;
    const counts = summary?.counts || {};
    $("input-candidates").textContent = counts.candidates ?? 0;
    $("input-accepted").textContent = counts.accepted_provisional ?? 0;
    $("input-withheld").textContent = counts.withheld ?? 0;
    $("input-pending").textContent = summary?.pending_publication ?? 0;
    const last = state.lastInput;
    $("input-last").textContent = !last ? "최근 입력 없음" : last.input_status === "accepted_provisional" ? `${FINGERS[last.finger] || last.finger} → ${last.virtual_key} (가상 입력 · 접촉 미확인)` : `${FINGERS[last.finger] || last.finger} 후보 보류 · ${(last.quality_flags || []).map((f) => FLAG_TEXT[f] || f).join(", ")}`;
    const fingers = summary?.finger_states || {};
    $("input-fingers").textContent = Object.keys(fingers).length ? Object.entries(fingers).map(([f, v]) => `${FINGERS[f] || f}: ${v.code || v.phase || "—"}`).join(" · ") : "손가락 상태 대기";
  }

  function render(force = false) {
    const cameraLabels = { disconnected: "연결 전", connecting: "새 영상 대기", receiving: "영상 수신 중", stalled: "영상 정지 · 보류", ended: "연결 종료", error: "연결 오류" };
    const trackerLabels = { not_loaded: "모델 미준비", loading: "모델 준비 중", ready: "모델 준비 완료", error: "모델 오류", failed: "모델 오류" };
    const valid = state.session?.calibrated_fingers || [];
    const calibrated = state.activeFingers.length > 0 && state.activeFingers.every((f) => valid.includes(f));
    status("video-status", cameraLabels[state.camera], state.camera === "receiving" ? "good" : ["error", "stalled", "ended"].includes(state.camera) ? "error" : "");
    status("preview-indicator", cameraLabels[state.camera], state.camera === "receiving" ? "good" : ["error", "stalled", "ended"].includes(state.camera) ? "error" : "");
    status("tracker-status", trackerLabels[state.tracker] || "상태 확인 중", state.tracker === "ready" ? "good" : state.tracker === "error" ? "error" : "");
    status("calibration-status", capturing() ? "자세 수집 중" : calibrated ? "보정 완료" : state.capture?.stage === "failed" ? "검사 실패" : valid.length ? `${valid.length}개 보정 완료` : "보정 전", calibrated ? "good" : "");
    const ready = (state.readyConfirmed || state.session?.lifecycle_phase === "running") && state.camera === "receiving" && state.tracker === "ready" && !state.geometryPending;
    status("ready-status", ready ? "입력 준비 완료" : "확인 필요", ready ? "good" : "");
    $("active-camera").textContent = state.stream ? state.deviceLabel : "연결된 카메라 없음";
    $("resolution-label").textContent = state.stream && video.videoWidth ? `${video.videoWidth} × ${video.videoHeight}` : "— × —";
    $("preview-placeholder").hidden = Boolean(state.stream && state.lastFrameAt);
    $("permission-status").textContent = { unknown: "권한 미확인", requesting: "권한 요청 중", granted: "권한 허용", denied: "권한 거부" }[state.permission];
    $("camera-select").disabled = unavailable();
    $("permission-button").disabled = unavailable() || Boolean(state.stream);
    $("connect-button").disabled = unavailable() || !$("camera-select").value || !state.activeFingers.length;
    $("connect-button").textContent = state.connectionBusy ? "처리 중…" : state.stream ? "선택 장치 재연결" : "선택 장치 연결";
    $("stop-button").disabled = state.connectionBusy || capturing() || (!state.stream && !state.session && !state.retiredSession);
    $("refresh-cameras").disabled = unavailable() || state.refreshBusy;
    $("prepare-model").disabled = unavailable() || state.tracker === "loading";
    $("prepare-model").textContent = state.tracker === "loading" ? "모델 준비 중…" : state.tracker === "ready" ? "추적 모델 상태 확인" : state.tracker === "error" ? "추적 모델 다시 준비" : "추적 모델 준비";
    $("model-help").textContent = state.model?.error ? `모델 오류: ${reasonText(state.model.error)} · 영상 프리뷰는 유지됩니다.` : state.tracker === "loading" ? "Python 서버가 모델을 준비 중입니다. 서버 상태를 주기적으로 확인합니다." : "영상 수신과 추적 준비는 별도 상태입니다. 모델 오류가 나도 카메라 프리뷰는 유지됩니다.";
    $("hand-fieldset").disabled = unavailable(); $("finger-fieldset").disabled = unavailable();
    for (const id of ["flip-x", "flip-y", "key-left", "key-top", "key-width", "key-height"]) $(id).disabled = unavailable();
    $("geometry-status").textContent = !state.session ? "연결 후 적용" : state.geometryPending ? "화면 기하 적용 중" : `기하 v${state.session.geometry_version}`;
    if (state.syncBlocked) $("geometry-status").textContent = "적용 실패 · 다시 적용 필요";
    $("retry-geometry").disabled = unavailable() || !state.session || state.camera !== "receiving";
    $("baseline-status").textContent = state.session?.baseline_ref ? "기준 저장됨" : "기준 없음";
    $("capture-rest").disabled = unavailable() || !state.session || state.geometryPending || state.tracker !== "ready" || state.camera !== "receiving";
    $("capture-rest").textContent = state.session?.baseline_ref ? "기준 자세 다시 수집" : "기준 자세 수집";
    $("ready-button").disabled = unavailable() || !calibrated || state.geometryPending || state.camera !== "receiving" || state.tracker !== "ready";
    $("export-button").disabled = unavailable() || !state.session?.baseline_ref || state.geometryPending;
    renderFingerRows(); renderCapture(); renderInput();
    const now = performance.now();
    if (!force && now - state.lastDetailedRender < 200) return;
    state.lastDetailedRender = now;
    $("metric-frames").textContent = state.received;
    $("metric-processed").textContent = state.processed;
    $("metric-skipped").textContent = state.skipped;
    $("metric-latency").textContent = state.latency == null ? "—" : `${Math.round(state.latency)}ms`;
    const handCount = state.observed?.hands?.length;
    $("quality-status").textContent = state.camera === "stalled" ? "새 영상 프레임 없음 · 관측 보류" : state.quality ? `${state.quality.quality_state || state.quality.state || "품질 응답"} · ${reasonText(state.quality.reason_codes || state.quality.reasons) || "사유 없음"}` : handCount === 0 ? "손 없음 · 추론 실패와 구분되는 정상 빈 관측" : "선택 손 관측 대기";
    if ($("diagnostic-details").open || force) $("diagnostic-json").textContent = JSON.stringify({ clock_id: clockId, session: state.session, device: { selected_device_id: state.deviceId, label: state.deviceLabel, permission: state.permission, stream: state.camera, tracker: state.tracker }, model: state.model, transport: { in_flight: Boolean(state.inFlight), queued: Boolean(state.latest), skipped: state.skipped, jpeg_max_width: 640, source_flipped: false }, capture: state.capture, quality: state.quality, diagnostics: state.diagnostics }, null, 2);
  }

  $("permission-button").addEventListener("click", () => void requestPermission());
  $("refresh-cameras").addEventListener("click", async () => { state.refreshBusy = true; render(); try { await enumerateCameras(); } catch (error) { failure(error); } finally { state.refreshBusy = false; render(); } });
  $("camera-select").addEventListener("change", () => { render(); if (state.stream && $("camera-select").value !== state.deviceId) notice("선택 장치 연결을 눌러야 카메라가 바뀝니다. 현재 영상은 프리뷰 위 장치의 영상입니다."); });
  $("connect-button").addEventListener("click", () => void connectCamera());
  $("stop-button").addEventListener("click", () => void stopCamera());
  $("prepare-model").addEventListener("click", () => void (state.tracker === "ready" ? pollHealth() : prepareModel()));
  for (const input of document.querySelectorAll('input[name="selected-hand"],input[name="active-finger"]')) input.addEventListener("change", () => void changeSelection());
  $("flip-x").addEventListener("click", () => { if (!unavailable()) { state.flipX = !state.flipX; geometryChanged(); } });
  $("flip-y").addEventListener("click", () => { if (!unavailable()) { state.flipY = !state.flipY; geometryChanged(); } });
  for (const id of ["key-left", "key-top", "key-width", "key-height"]) $(id).addEventListener("input", geometryChanged);
  $("retry-geometry").addEventListener("click", () => { state.syncBlocked = false; state.geometryPending = true; scheduleSync(); render(); });
  $("capture-rest").addEventListener("click", () => void startCapture("rest", state.activeFingers.includes("index") ? "index" : state.activeFingers[0]));
  $("cancel-capture").addEventListener("click", () => void cancelCapture());
  $("ready-button").addEventListener("click", () => void checkReady());
  $("export-button").addEventListener("click", () => void exportCalibration());
  $("input-start").addEventListener("click", () => void startInput());
  $("input-stop").addEventListener("click", () => void stopInput());
  $("diagnostic-details").addEventListener("toggle", () => render(true));
  navigator.mediaDevices?.addEventListener("devicechange", () => { if (!capturing() && !state.connectionBusy) void enumerateCameras().catch((error) => failure(error)); });
  new ResizeObserver(() => { drawLandmarks(); if (state.session && JSON.stringify(geometry()) !== state.geometrySignature) geometryChanged(); }).observe($("preview-stage"));
  window.addEventListener("pagehide", () => {
    const old = state.session || state.retiredSession;
    state.generation += 1; stopFrameLoop(); flushPending();
    state.controllers.forEach((controller) => controller.abort());
    state.stream?.getTracks().forEach((track) => track.stop());
    if (old) void closeRemote(old, true).catch(() => {});
  });
  setInterval(() => {
    if (state.stream && state.lastFrameAt && performance.now() - state.lastFrameAt > 500 && state.camera === "receiving") { state.camera = "stalled"; state.readyConfirmed = false; flushPending(); clearOverlay(); notice("카메라의 새 프레임이 멈춰 관측을 보류합니다. 선택한 장치를 확인하세요.", "error"); render(true); }
    if (state.observed && performance.now() - state.observationAt > 500) { clearOverlay(); render(); }
  }, 100);
  setInterval(() => { if (state.tracker === "loading" || !state.health || state.health.error) void pollHealth(); }, 1000);
  setInterval(() => { void pollSession(); }, 1000);
  setInterval(() => {
    render();
    // 손·손가락 선택 변경으로 새 세션을 만든 직후, 동기화 타이머가 바쁜 상태에서 한 번 건너뛰면
    // 장치·기하가 영영 전송되지 않아 보정 버튼이 잠겼다(2026-11-25 실사용). 조건이 맞으면 다시 예약한다.
    if (state.session && state.stream && state.camera === "receiving" && !state.syncBlocked && !capturing()
        && !state.connectionBusy && !state.controlBusy && (state.geometryPending || state.deviceSignature !== deviceSignature())) scheduleSync();
  }, 200);
  updateGeometryUI(); render(true);
  void enumerateCameras().catch((error) => failure(error));
  void pollHealth();
})();
