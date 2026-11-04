import test from 'node:test';
import assert from 'node:assert/strict';
import { chartCoordinates, validatedPresentation } from '../src/lib/answer-presentation.js';

const evidence = [{ id: 'chunk-1', document_id: 'manual-1', text: 'ECA 밖의 황 한도는 0.50%이고 ECA 안은 0.10%입니다.' }];
const table = () => ({ type: 'table', source: 'documents', title: '황 한도 비교',
  columns: ['해역', '황 함유량'], rows: [{ cells: ['ECA 밖', '0.50%'],
    citations: [{ chunkId: 'chunk-1', quote: evidence[0].text }] }], truncated: false, totalRows: 1 });
const chart = () => ({ type: 'chart', source: 'operations', chartType: 'bar', title: 'CO₂ 추이',
  xLabel: '일자', yLabel: 'CO₂', unit: 't CO₂', points: [{ label: '2026-09-25', value: 31.14 }],
  truncated: false, totalPoints: 1 });

test('document tables require exact source citations and consistent cells', () => {
  const value = table();
  assert.equal(validatedPresentation(value, evidence), value);
  for (const mutate of [
    p => { p.rows[0].citations[0].chunkId = 'unavailable'; },
    p => { p.rows[0].citations[0].quote = '원문에 존재하지 않는 인용문입니다.'; },
    p => { p.rows[0].citations = []; },
    p => { p.rows[0].cells.push('추가 셀'); },
    p => { p.rows[0].cells[0] = null; },
    p => { p.columns = ['열 '.repeat(100)]; },
  ]) {
    const changed = table(); mutate(changed);
    assert.equal(validatedPresentation(changed, evidence), null);
  }
  assert.equal(validatedPresentation(value, []), null);
});

test('operation tables use their own source instead of invented document citations', () => {
  const value = table(); value.source = 'operations'; value.rows[0].citations = [];
  assert.equal(validatedPresentation(value), value);
  value.rows[0].citations = [{ chunkId: 'chunk-1', quote: evidence[0].text }];
  assert.equal(validatedPresentation(value, evidence), null);
});

test('presentation limits and truncation metadata reject misleading partial data', () => {
  const value = table(); value.truncated = true; value.totalRows = 70;
  assert.equal(validatedPresentation(value, evidence), value);
  value.truncated = false;
  assert.equal(validatedPresentation(value, evidence), null);
  value.totalRows = 0;
  assert.equal(validatedPresentation(value, evidence), null);
  value.totalRows = 51; value.truncated = false;
  value.rows = Array.from({ length: 51 }, () => structuredClone(table().rows[0]));
  assert.equal(validatedPresentation(value, evidence), null);
  const many = chart(); many.points = Array.from({ length: 101 }, (_, i) => ({ label: String(i), value: i }));
  many.totalPoints = 101;
  assert.equal(validatedPresentation(many), null);
});

test('charts accept only bounded actual operation values and supported types', () => {
  const value = chart(); assert.equal(validatedPresentation(value), value);
  for (const invalid of [NaN, Infinity, -1, '31.14', null, true]) {
    const changed = chart(); changed.points[0].value = invalid;
    assert.equal(validatedPresentation(changed), null);
  }
  for (const mutate of [
    p => { p.source = 'documents'; }, p => { p.chartType = 'javascript'; },
    p => { p.points = []; }, p => { p.points[0].label = ''; }, p => { p.unit = 't'.repeat(41); },
  ]) {
    const changed = chart(); mutate(changed);
    assert.equal(validatedPresentation(changed), null);
  }
});

test('HTML-looking cells and labels stay text data for the React renderer', () => {
  const value = table(); value.rows[0].cells[0] = '<img src=x onerror="alert(1)">';
  assert.equal(validatedPresentation(value, evidence).rows[0].cells[0], value.rows[0].cells[0]);
  assert.equal(validatedPresentation({ type: 'image', title: '미지원 그림', html: '<script>bad()</script>' }), null);
  for (const invalid of [null, undefined, {}, [], 'table']) assert.equal(validatedPresentation(invalid), null);
});

test('zero and single-point charts have finite positions and an honest zero baseline', () => {
  for (const type of ['line', 'bar']) {
    const plot = chartCoordinates([{ label: '한 점', value: 0 }], type);
    assert.ok(Number.isFinite(plot.points[0].x));
    assert.equal(plot.points[0].y, plot.baseline);
    assert.equal(plot.ticks[0].value, 0);
    assert.ok(plot.ticks.at(-1).value > 0);
  }
  const plot = chartCoordinates([{ label: 'A', value: 10 }, { label: 'B', value: 20 }], 'line');
  assert.ok(plot.points[1].y < plot.points[0].y);
  assert.equal(plot.points[1].y, plot.top);
  assert.ok(plot.points[0].x < plot.points[1].x);
});
