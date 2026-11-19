import { uuid } from './id.js';
import { extractPdf, PDF_BYTE_LIMIT, sectionsToText } from './pdf.js';
import { documentSavePayload } from './document-save.js';

export function newPdfRow(file) {
  return {
    id: 'doc-' + uuid(), file, selected: true, state: 'pending', uncertain: false,
    title: file.name.replace(/\.pdf$/i, ''), kind: 'onboard', language: '',
    issuer: '', version: '1', reference: '본문', scope: 'all', url: '',
    reviewedAt: new Date().toISOString().slice(0, 10),
  };
}

export function rowErrors(row) {
  const errors = [];
  if (row.file.size > PDF_BYTE_LIMIT) errors.push('파일당 최대 25MB입니다.');
  for (const [key, label, limit] of [
    ['title', '문서명', 200], ['version', '버전', 100], ['reference', '조항·참조명', 200],
  ]) {
    if (!row[key].trim()) errors.push(label + ' 입력이 필요합니다.');
    else if (row[key].length > limit) errors.push(label + ' 입력 한도는 ' + limit + '자입니다.');
  }
  if (row.url || row.kind === 'official-summary') {
    try {
      const url = new URL(row.url);
      if (!['http:', 'https:'].includes(url.protocol) ||
          (row.kind === 'official-summary' && url.protocol !== 'https:')) throw Error();
    } catch {
      errors.push(row.kind === 'official-summary' ? '공식 안내 요약의 HTTPS 원문 링크가 필요합니다.' : '원문 링크를 확인해 주세요.');
    }
  }
  return errors;
}

export function applyCommon(rows, changes) {
  return rows.map(row => row.selected && row.state !== 'saved' && !row.uncertain
    ? { ...row, ...changes } : row);
}

function unknownOutcome(error) {
  return ['NETWORK', 'RESPONSE', 'VERSION_CONFLICT'].includes(error.code) || error.status >= 500;
}

// File objects remain in the list. Extracted source and Base64 live only for this call.
export async function savePdfRow(row, { save, exists, update, extract = extractPdf }) {
  async function reconcile() {
    update({ uncertain: true, progress: '저장 여부 확인 중' });
    try {
      return await exists(row.id);
    } catch {
      throw Error('저장 여부를 확인하지 못했습니다. 연결을 확인한 뒤 다시 시도해 주세요.');
    }
  }
  if (row.uncertain && await reconcile()) return;
  const data = await extract(row.file, progress => update({ progress }));
  update({ emptyPages: data.emptyPages, pageCount: data.sections.length, progress: '저장 중' });
  const payload = documentSavePayload({ ...row, text: sectionsToText(data.sections) }, {
    id: row.id, file: data.file, today: row.reviewedAt,
  });
  if (payload.document.sections.length > 1500) throw Error('문서 절이 1,500개를 넘습니다. PDF를 나누어 주세요.');
  try {
    await save(payload);
  } catch (error) {
    if (!unknownOutcome(error)) {
      update({ uncertain: false });
      throw error;
    }
    if (!await reconcile()) throw Error('저장 완료를 확인할 수 없습니다. 같은 파일을 다시 시도해 주세요.');
  }
}

export async function savePdfBatch(rows, { updateRow, isOpen, ...services }, retry = false) {
  for (const row of rows) {
    if (!isOpen()) break;
    if (!row.selected || row.state !== (retry ? 'failed' : 'pending') || rowErrors(row).length) continue;
    const update = patch => updateRow(row.id, patch);
    update({ state: 'processing', progress: 'PDF 추출 중', error: '' });
    try {
      await savePdfRow(row, { ...services, update });
      update({ state: 'saved', uncertain: false, progress: '', selected: false });
    } catch (error) {
      update({ state: 'failed', progress: '', error: error.message });
    }
  }
}
