/* 연습곡 낙하 가이드: 곡 차트(pthoven-song-chart.v1)의 음을 가상 건반 위로 떨어뜨려 보여 준다.
 *
 * 표시 전용이다. 관측 입력을 기대 음에 맞춰 고치거나 맞음/틀림을 판정하지 않는다(PRD 3, B-03 범위).
 * B-02 입력은 받은 그대로 표시하고 기록만 한다. 시각은 모두 performance.now() 기준(ms)이며,
 * B-02 사건의 t_ms(복귀 프레임의 브라우저 시각)와 같은 시계다.
 */
"use strict";

(function (root) {
  const KEY_ORDER = ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"];
  const SOLFEGE = { C4: "도", D4: "레", E4: "미", F4: "파", G4: "솔", A4: "라", B4: "시", C5: "도" };
  const KEY_COLORS = { C4: "#f4a3a3", D4: "#f6c58f", E4: "#f2e28c", F4: "#9fdcae", G4: "#8fc9f2", A4: "#b3a6f0", B4: "#e7a6dd", C5: "#f4a3a3" };
  const FALL_MS = 2600;          // 타일이 화면 위에서 건반 윗변까지 내려오는 시간
  const END_HOLD_MS = 1200;      // 마지막 음 뒤 화면을 유지하는 시간
  const MARK_MS = 450;           // B-02 입력 표시 유지 시간
  const BPM_RANGE = [30, 90];

  function validateChart(chart) {
    if (!chart || chart.schema !== "pthoven-song-chart.v1") throw new Error("지원하지 않는 곡 차트입니다.");
    if (!Array.isArray(chart.notes) || !chart.notes.length) throw new Error("곡 차트에 음표가 없습니다.");
    let previous = -Infinity;
    for (const note of chart.notes) {
      if (!KEY_ORDER.includes(note.key)) throw new Error(`데모 건반 밖의 음입니다: ${note.key}`);
      if (!(note.onset_quarter > previous)) throw new Error("음표 시작 시각이 증가하지 않습니다(동시음 미지원).");
      if (!(note.duration_quarter > 0)) throw new Error("음 길이가 0 이하입니다.");
      previous = note.onset_quarter;
    }
    return chart;
  }

  /** 곡 차트 + 템포 + 시작 시각 → 음마다 기대 시각(ms). */
  function schedule(chart, bpm, t0Ms) {
    const quarterMs = 60000 / bpm;
    return chart.notes.map((note) => ({
      id: note.id, key: note.key, midi: note.midi, column: KEY_ORDER.indexOf(note.key),
      startMs: t0Ms + note.onset_quarter * quarterMs, endMs: t0Ms + (note.onset_quarter + note.duration_quarter) * quarterMs,
    }));
  }

  /** 시각 now의 타일 사각형. rect는 화면(CSS px) 위 가상 건반 영역, 타일 아랫변이 건반 윗변에 닿는 순간이 기대 시각이다.
   *  타일은 건반 윗변(판정선)에서 잘린다. 건반 안·아래로는 그리지 않는다(2026-11-28 실사용 피드백). */
  function layout(notes, nowMs, rect, fallMs = FALL_MS) {
    const hitY = rect.top, speed = hitY / fallMs, column = rect.width / KEY_ORDER.length, pad = Math.max(2, column * 0.1);
    const tiles = [];
    for (const note of notes) {
      const bottom = hitY + (nowMs - note.startMs) * speed;
      const height = Math.max(14, (note.endMs - note.startMs) * speed - 6);
      const top = bottom - height;
      if (bottom < 0 || top >= hitY) continue;
      const status = nowMs < note.startMs ? "upcoming" : "active";
      tiles.push({ id: note.id, key: note.key, status, x: rect.left + note.column * column + pad, y: top, w: column - pad * 2, h: Math.min(bottom, hitY) - top });
    }
    return tiles;
  }

  /** 지금 울려야 하는 음(기대 시각 ~ 끝 사이). 건반 칸 강조에 쓴다. */
  function activeNotes(notes, nowMs) {
    return notes.filter((note) => note.startMs <= nowMs && nowMs < note.endMs);
  }

  /** 시작 전 남은 박(3, 2, 1). 시작 뒤에는 null. */
  function countdown(nowMs, t0Ms, quarterMs) {
    if (nowMs >= t0Ms) return null;
    return Math.ceil((t0Ms - nowMs) / quarterMs);
  }

  function nextNote(notes, nowMs) {
    return notes.find((note) => note.endMs > nowMs) || null;
  }

  function createGuide({ canvas, keyboard, now = () => performance.now(), raf = (fn) => requestAnimationFrame(fn), onChange = () => {} }) {
    const ctx = canvas.getContext("2d");
    let run = null, handle = null, audio = null;

    function keyboardRect() {
      const s = keyboard.style, px = (v) => Number.parseFloat(v);
      const rect = { left: px(s.left), top: px(s.top), width: px(s.width), height: px(s.height) };
      return Object.values(rect).every(Number.isFinite) ? rect : null;
    }

    function click(at, accent) {
      if (!audio) return;
      try {
        const osc = audio.createOscillator(), gain = audio.createGain();
        osc.frequency.value = accent ? 1320 : 880;
        gain.gain.setValueAtTime(0.0001, at); gain.gain.exponentialRampToValueAtTime(0.12, at + 0.005); gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.08);
        osc.connect(gain).connect(audio.destination); osc.start(at); osc.stop(at + 0.1);
      } catch (_) { /* 소리 실패는 가이드 표시를 멈추지 않는다. */ }
    }

    function start(chart, bpm) {
      validateChart(chart);
      if (!(bpm >= BPM_RANGE[0] && bpm <= BPM_RANGE[1])) throw new Error(`템포는 ${BPM_RANGE[0]}~${BPM_RANGE[1]} BPM만 지원합니다.`);
      stop("restart");
      const quarterMs = 60000 / bpm, beats = Math.max(3, chart.time_signature?.[0] || 4);
      const t0 = now() + Math.max(beats * quarterMs, FALL_MS);
      const notes = schedule(chart, bpm, t0);
      run = { chart, bpm, quarterMs, t0, notes, inputs: [], marks: [], startedAt: now(), state: "running", endAt: notes[notes.length - 1].endMs + END_HOLD_MS };
      try {
        audio = audio || new (root.AudioContext || root.webkitAudioContext)();
        void audio.resume?.();
        const base = audio.currentTime + (t0 - now()) / 1000;
        for (let i = beats; i >= 1; i -= 1) { const at = base - i * quarterMs / 1000; if (at > audio.currentTime) click(at, i === beats); }
      } catch (_) { audio = null; }
      loop();
      onChange(snapshot());
    }

    function stop(reason = "user_stop") {
      if (handle != null && root.cancelAnimationFrame) root.cancelAnimationFrame(handle);
      handle = null;
      if (run && run.state === "running") { run.state = reason === "finished" ? "finished" : "stopped"; run.stoppedAt = now(); }
      draw();
      onChange(snapshot());
    }

    /** B-02 제안을 받은 그대로 기록·표시한다. 기대 음과 비교하지 않는다. */
    function noteInput(proposal) {
      if (!run || run.state !== "running" || !proposal) return;
      const accepted = proposal.input_status === "accepted_provisional";
      run.inputs.push({ event_id: proposal.event_id, finger: proposal.finger, virtual_key: proposal.virtual_key ?? null,
        midi_note: proposal.midi_note ?? null, input_status: proposal.input_status, t_ms: proposal.t_ms,
        offset_from_guide_t0_ms: Number.isFinite(proposal.t_ms) ? proposal.t_ms - run.t0 : null });
      if (accepted && KEY_ORDER.includes(proposal.virtual_key)) run.marks.push({ key: proposal.virtual_key, until: now() + MARK_MS });
      onChange(snapshot());
    }

    function snapshot() {
      if (!run) return { state: "idle" };
      const t = now(), next = nextNote(run.notes, t), done = run.notes.filter((n) => n.startMs <= t).length;
      return { state: run.state, songId: run.chart.id, title: run.chart.title_ko, bpm: run.bpm, total: run.notes.length,
        passed: Math.min(done, run.notes.length), next: next && run.state === "running" ? next.key : null,
        countdown: run.state === "running" ? countdown(t, run.t0, run.quarterMs) : null, inputs: run.inputs.length };
    }

    function exportRun() {
      if (!run) return null;
      return {
        schema: "pthoven-guide-run.v1", clock: "performance.now", contact_verified: false,
        note: "표시 전용 기록. 입력과 기대 음의 정렬·판정은 B-03(S-07)에서 한다.",
        song: { id: run.chart.id, title_ko: run.chart.title_ko, attribution: run.chart.attribution, derived_from: run.chart.derived_from },
        quarter_bpm: run.bpm, guide_t0_ms: run.t0, state: run.state, started_ms: run.startedAt, stopped_ms: run.stoppedAt ?? null,
        expected: run.notes.map((n) => ({ id: n.id, key: n.key, midi: n.midi, expected_onset_ms: n.startMs, expected_offset_ms: n.endMs })),
        observed_inputs: run.inputs.slice(),
      };
    }

    function resize() {
      const ratio = root.devicePixelRatio || 1, width = canvas.clientWidth, height = canvas.clientHeight;
      if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) { canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio); }
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      return { width, height };
    }

    function roundRect(x, y, w, h, r) {
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x, y, w, h, r); else ctx.rect(x, y, w, h);
    }

    function draw() {
      const { width, height } = resize();
      ctx.clearRect(0, 0, width, height);
      const rect = keyboardRect();
      if (!run || !rect || run.state !== "running") return;
      const t = now(), column = rect.width / KEY_ORDER.length, next = nextNote(run.notes, t);
      // 다음 음의 건반 열을 은은하게 표시한다.
      if (next) { ctx.fillStyle = "rgba(255,255,255,0.10)"; ctx.fillRect(rect.left + next.column * column, 0, column, rect.top); }
      // 타일이 판정선에 닿아 사라지는 동안에는 건반 칸 자체를 곡 색으로 채워 칠 건반을 보여 준다.
      for (const note of activeNotes(run.notes, t)) { ctx.globalAlpha = 0.45; ctx.fillStyle = KEY_COLORS[note.key]; ctx.fillRect(rect.left + note.column * column, rect.top, column, rect.height); }
      ctx.globalAlpha = 1;
      ctx.strokeStyle = "rgba(255,255,255,0.85)"; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(rect.left, rect.top); ctx.lineTo(rect.left + rect.width, rect.top); ctx.stroke();
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      for (const tile of layout(run.notes, t, rect)) {
        ctx.globalAlpha = tile.status === "active" ? 1 : 0.92;
        ctx.fillStyle = KEY_COLORS[tile.key]; roundRect(tile.x, tile.y, tile.w, tile.h, Math.min(8, tile.h / 2)); ctx.fill();
        if (tile.status === "active") { ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2; ctx.stroke(); }
        if (tile.h >= 16) { ctx.fillStyle = "#1d2b28"; ctx.font = "600 13px Pretendard, -apple-system, sans-serif"; ctx.fillText(SOLFEGE[tile.key], tile.x + tile.w / 2, tile.y + Math.min(tile.h - 10, 14)); }
      }
      ctx.globalAlpha = 1;
      run.marks = run.marks.filter((mark) => mark.until > t);
      for (const mark of run.marks) {
        const cx = rect.left + (KEY_ORDER.indexOf(mark.key) + 0.5) * column, life = (mark.until - t) / MARK_MS;
        ctx.strokeStyle = `rgba(145,234,208,${life.toFixed(2)})`; ctx.lineWidth = 3;
        ctx.beginPath(); ctx.arc(cx, rect.top, 10 + (1 - life) * 14, 0, Math.PI * 2); ctx.stroke();
      }
      const remaining = countdown(t, run.t0, run.quarterMs);
      if (remaining != null && remaining <= 4) {
        ctx.fillStyle = "rgba(255,255,255,0.9)"; ctx.font = "700 54px Pretendard, -apple-system, sans-serif";
        ctx.fillText(String(remaining), width / 2, rect.top / 2);
      }
    }

    function loop() {
      if (!run || run.state !== "running") return;
      if (now() >= run.endAt) { stop("finished"); return; }
      draw();
      handle = raf(loop);
    }

    return { start, stop, noteInput, snapshot, exportRun, draw, get run() { return run; } };
  }

  root.PthovenPractice = { KEY_ORDER, SOLFEGE, FALL_MS, BPM_RANGE, validateChart, schedule, layout, activeNotes, countdown, nextNote, createGuide };
})(globalThis);
