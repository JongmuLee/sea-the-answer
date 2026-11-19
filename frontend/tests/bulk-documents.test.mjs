import test from 'node:test';
import assert from 'node:assert/strict';
import { newPdfRow, rowErrors, applyCommon, savePdfBatch } from '../src/lib/bulk-documents.js';

function fixture(names) {
  const rows = names.map(name => newPdfRow(new File(['%PDF-1.4'], name)));
  const calls = [];
  const services = {
    isOpen: () => true,
    updateRow(id, patch) { Object.assign(rows.find(row => row.id === id), patch); },
    async extract(file) {
      return { file: { name: file.name, base64: 'JVBERg==' }, emptyPages: [2],
        sections: [{ page: 1, text: '합성 자료' }, { page: 3, text: 'End' }] };
    },
    async save(payload) { calls.push(payload); },
    async exists() { return false; },
  };
  return { rows, calls, services };
}

test('selected files receive common values, keep individual edits and independent logical IDs', () => {
  const { rows } = fixture(['same.pdf', 'same.pdf', 'skip.pdf']);
  rows[2].selected = false;
  const applied = applyCommon(rows, { language: 'en', issuer: 'Demo' });
  applied[1].reference = 'Article 2';
  assert.deepEqual(applied.map(row => row.issuer), ['Demo', 'Demo', '']);
  assert.deepEqual(applied.map(row => row.reference), ['본문', 'Article 2', '본문']);
  assert.equal(new Set(rows.map(row => row.id)).size, 3);
  assert.deepEqual(rowErrors({ ...rows[0], version: ' ', title: '', reference: '' }).length, 3);
  assert.equal(rowErrors({ ...rows[0], kind: 'official-summary' }).length, 1);
  assert.equal(rowErrors({ ...rows[0], file: { size: 25 * 1024 ** 2 + 1 } }).length, 1);
});

test('extracts and saves serially, skips invalid rows, retries just the failed PDF', async () => {
  const { rows, services, calls } = fixture(['bad.pdf', 'ok.pdf', 'incomplete.pdf']);
  rows[1].language = 'en';
  rows[2].version = '';
  const extract = services.extract, order = [];
  let broken = true;
  services.extract = async file => {
    order.push('extract ' + file.name);
    if (file.name === 'bad.pdf' && broken) throw Error('PDF 추출 실패');
    return extract(file);
  };
  services.save = async payload => {
    order.push('save ' + payload.file.name);
    calls.push(payload);
  };
  await savePdfBatch(rows, services);
  assert.deepEqual(rows.map(r => r.state), ['failed', 'saved', 'pending']);
  assert.equal(rows[0].error, 'PDF 추출 실패');
  broken = false;
  await savePdfBatch(rows, services, true);
  assert.deepEqual(order, ['extract bad.pdf', 'extract ok.pdf', 'save ok.pdf', 'extract bad.pdf', 'save bad.pdf']);
  assert.deepEqual(calls.map(p => p.document.language), ['en', 'ko']);
  assert.deepEqual(calls[0].document.sections.map(s => s.page), [1, 3]);
  assert.deepEqual(rows[1].emptyPages, [2]);
  assert.ok(calls.every(p => p.expectedId === ''));
  assert.equal(calls[1].document.id, rows[0].id);
});

test('a confirmed rejection after an uncertain request unlocks file metadata', async () => {
  const { rows, services } = fixture(['retry.pdf']);
  Object.assign(rows[0], { state: 'failed', uncertain: true });
  services.save = async () => { throw Object.assign(Error('Invalid metadata'), { status: 400 }); };
  await savePdfBatch(rows, services, true);
  assert.equal(rows[0].state, 'failed');
  assert.equal(rows[0].uncertain, false);
  assert.equal(rows[0].error, 'Invalid metadata');
});

for (const committed of [true, false]) {
  test('lost response, failed confirmation, then retry with original ID. Committed = ' + committed, async () => {
    const { rows, services, calls } = fixture(['same.pdf']);
    const [row] = rows;
    services.save = async payload => {
      calls.push(payload);
      if (calls.length === 1) throw Object.assign(Error('timeout'), { code: 'NETWORK' });
    };
    let lookups = 0;
    services.exists = async id => {
      assert.equal(id, row.id);
      if (++lookups === 1) throw Error('offline');
      return committed;
    };
    await savePdfBatch(rows, services);
    assert.equal(row.state, 'failed');
    assert.equal(row.uncertain, true);
    await savePdfBatch(rows, services, true);
    assert.equal(row.state, 'saved');
    assert.equal(calls.length, committed ? 1 : 2);
    assert.ok(calls.every(p => p.document.id === row.id && p.expectedId === ''));
  });
}
