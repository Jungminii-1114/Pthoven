/**
 * 연습곡 낙하 가이드(web/practice.js) 테스트.
 * 실행: vision 폴더에서 node --test tests/test_practice.mjs
 * 브라우저·카메라 없이 순수 계산과 가짜 캔버스로 확인한다. 실제 화면 정합은 사람이 확인해야 한다.
 */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

function load() {
  const ctx = { console, Number, Math, Object, Array, Error };
  ctx.globalThis = ctx;
  vm.runInNewContext(fs.readFileSync(new URL("../web/practice.js", import.meta.url), "utf8"), ctx);
  return ctx.PthovenPractice;
}
const P = load();
const songsDir = new URL("../web/songs/", import.meta.url);
const readJson = (name) => JSON.parse(fs.readFileSync(new URL(name, songsDir), "utf8"));
const chart = (notes, extra = {}) => ({ schema: "pthoven-song-chart.v1", id: "t", title_ko: "테스트", time_signature: [4, 4], notes, ...extra });
const note = (id, key, onset, duration = 1) => ({ id, key, midi: 60, onset_quarter: onset, duration_quarter: duration });

test("기대 시각: 4분음표 BPM으로 onset을 ms로 바꾼다", () => {
  const s = P.schedule(chart([note("a", "C4", 0), note("b", "E4", 1.5, 0.5)]), 50, 1000);
  assert.equal(s[0].startMs, 1000); assert.equal(s[0].endMs, 2200);
  assert.equal(s[1].startMs, 1000 + 1.5 * 1200); assert.equal(s[1].column, 2);
});

test("타일 아랫변은 기대 시각에 정확히 건반 윗변에 닿는다", () => {
  const rect = { left: 100, top: 300, width: 800, height: 100 };
  const s = P.schedule(chart([note("a", "G4", 0)]), 60, 5000);
  const [tile] = P.layout(s, 5000, rect);
  assert.equal(tile.y + tile.h, 300); assert.equal(tile.status, "active");
  assert.equal(tile.x, 100 + 4 * 100 + 10); assert.equal(tile.w, 80);
  const [before] = P.layout(s, 5000 - P.FALL_MS / 2, rect);
  assert.equal(before.y + before.h, 150); assert.equal(before.status, "upcoming");
  assert.equal(P.layout(s, 5000 - P.FALL_MS - 1, rect).length, 0); // 화면 위 바깥
});

test("판정선을 지난 타일은 건반 윗변에서 잘리고 건반 안·아래에는 그리지 않는다", () => {
  const rect = { left: 100, top: 300, width: 800, height: 100 };
  const s = P.schedule(chart([note("a", "G4", 0, 2)]), 60, 5000); // 2000ms 음
  const [mid] = P.layout(s, 6000, rect);
  assert.equal(mid.y + mid.h, 300); assert.ok(mid.h > 0 && mid.h < (2000 * 300 / P.FALL_MS)); assert.equal(mid.status, "active");
  assert.equal(P.layout(s, 7000, rect).length, 0); // 음이 끝나면 완전히 사라진다
  for (let t = 4000; t <= 7200; t += 50) for (const tile of P.layout(s, t, rect)) assert.ok(tile.y + tile.h <= 300 + 1e-9);
  assert.equal(P.activeNotes(s, 6000).length, 1); assert.equal(P.activeNotes(s, 7000).length, 0);
});

test("카운트다운은 시작 전 남은 박을 올림으로 센다", () => {
  assert.equal(P.countdown(0, 3000, 1000), 3); assert.equal(P.countdown(2500, 3000, 1000), 1); assert.equal(P.countdown(3000, 3000, 1000), null);
});

test("데모 건반 밖의 음·동시음·길이 0은 거부한다", () => {
  assert.throws(() => P.validateChart(chart([note("a", "C#4", 0)])), /건반 밖/);
  assert.throws(() => P.validateChart(chart([note("a", "C4", 0), note("b", "D4", 0)])), /동시음/);
  assert.throws(() => P.validateChart(chart([note("a", "C4", 0, 0)])), /길이/);
  assert.throws(() => P.validateChart({ ...chart([note("a", "C4", 0)]), schema: "x" }), /지원하지 않는/);
});

test("선정한 3곡 차트: 흰 건반 C4~G4, 단음, 출처 표기 포함", () => {
  const index = readJson("index.json");
  assert.equal(index.songs.length, 3);
  assert.deepEqual(index.songs.map((s) => s.level), [1, 2, 3]);
  for (const entry of index.songs) {
    const c = P.validateChart(readJson(entry.file));
    assert.equal(c.id, entry.id); assert.equal(c.notes.length, entry.note_count);
    assert.ok(c.notes.every((n) => ["C4", "D4", "E4", "F4", "G4"].includes(n.key)));
    assert.ok(c.min_onset_gap_quarter >= 1, "데모 곡은 4분음표보다 촘촘한 음이 없어야 한다");
    assert.match(c.attribution, /mutopiaproject\.org/); assert.ok(c.license);
    assert.equal(c.evaluation_policy.assess_onsets, true);
  }
});

function fakeGuide() {
  let t = 0; const frames = [];
  const calls = [];
  const ctx2d = new Proxy({}, { get: (_, k) => (k in ctx2d_state ? ctx2d_state[k] : (...args) => calls.push([k, ...args])), set: (_, k, v) => { ctx2d_state[k] = v; return true; } });
  const ctx2d_state = {};
  const canvas = { clientWidth: 1000, clientHeight: 500, width: 0, height: 0, getContext: () => ctx2d };
  const keyboard = { style: { left: "100px", top: "300px", width: "800px", height: "120px" } };
  const changes = [];
  const guide = P.createGuide({ canvas, keyboard, now: () => t, raf: (fn) => { frames.push(fn); return frames.length; }, onChange: (s) => changes.push(s) });
  return { guide, set: (v) => { t = v; }, tick: () => { const fn = frames.shift(); fn?.(); }, frames, calls, changes };
}

test("가이드 실행: 카운트인 뒤 시작, 마지막 음 뒤 자동 완주", () => {
  const g = fakeGuide();
  const c = chart([note("a", "C4", 0), note("b", "D4", 1)]);
  g.guide.start(c, 60);
  const run = g.guide.run;
  assert.equal(run.t0, Math.max(4 * 1000, P.FALL_MS));
  assert.equal(g.guide.snapshot().countdown, 4);
  g.set(run.t0 + 1500); g.tick();
  assert.equal(g.guide.snapshot().passed, 2); assert.equal(g.guide.snapshot().next, "D4");
  g.set(run.endAt); g.tick();
  assert.equal(g.guide.snapshot().state, "finished");
});

test("B-02 입력은 그대로 기록만 하고 기대 음으로 바꾸지 않는다", () => {
  const g = fakeGuide();
  g.guide.noteInput({ event_id: "evt-0", input_status: "accepted_provisional", virtual_key: "C4", t_ms: 1 }); // 시작 전: 무시
  g.guide.start(chart([note("a", "C4", 0)]), 60);
  const t0 = g.guide.run.t0;
  g.guide.noteInput({ event_id: "evt-1", finger: "index", input_status: "accepted_provisional", virtual_key: "E4", midi_note: 64, t_ms: t0 + 120 });
  g.guide.noteInput({ event_id: "evt-2", finger: "index", input_status: "withheld", virtual_key: null, midi_note: null, t_ms: t0 + 900 });
  const out = g.guide.exportRun();
  assert.equal(out.schema, "pthoven-guide-run.v1"); assert.equal(out.contact_verified, false);
  // vm 컨텍스트의 배열은 프로토타입이 달라 JSON으로 비교한다.
  const plain = (v) => JSON.parse(JSON.stringify(v));
  assert.deepEqual(plain(out.observed_inputs.map((i) => [i.event_id, i.virtual_key, i.midi_note, i.offset_from_guide_t0_ms])), [["evt-1", "E4", 64, 120], ["evt-2", null, null, 900]]);
  assert.deepEqual(plain(out.expected.map((e) => [e.key, e.expected_onset_ms])), [["C4", t0]]);
  assert.equal(g.guide.run.marks.length, 1); // 보류 입력은 건반 표시를 만들지 않는다
  assert.equal("result" in out.observed_inputs[0], false);
});

test("정지하면 이후 입력을 기록하지 않고, 다시 시작하면 새 기록이 된다", () => {
  const g = fakeGuide();
  g.guide.start(chart([note("a", "C4", 0)]), 60);
  g.guide.stop();
  assert.equal(g.guide.snapshot().state, "stopped");
  g.guide.noteInput({ event_id: "evt-1", input_status: "accepted_provisional", virtual_key: "C4", t_ms: 5 });
  assert.equal(g.guide.exportRun().observed_inputs.length, 0);
  g.guide.start(chart([note("a", "C4", 0)]), 50);
  assert.equal(g.guide.snapshot().state, "running"); assert.equal(g.guide.run.bpm, 50);
  assert.throws(() => g.guide.start(chart([note("a", "C4", 0)]), 200), /BPM/);
});
