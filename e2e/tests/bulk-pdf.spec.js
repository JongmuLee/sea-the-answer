import { test, expect } from '@playwright/test';
import { pdfFixture } from '../pdf-fixture.js';

test('bulk preview, per-file validation, partial success and uncertain network response', async ({ page }) => {
  const documents = [], attempts = [], errors = [];
  let temporaryFailure = true;
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const send = (json, status = 200) => route.fulfill({ status, json });
    if (path === '/api/health') return send({ csrfToken: 'fixture', authenticated: true, user: { id: 'admin', name: 'Fixture', role: 'admin' } });
    if (path === '/api/documents') return send({ documents });
    if (path === '/api/operations') return send({ ships: [], records: [] });
    if (path !== '/api/changes') return send({ reports: [], skills: [], history: [] });
    const { kind, payload } = route.request().postDataJSON();
    expect(kind).toBe('document.save');
    attempts.push(payload);
    if (payload.document.title === 'bravo' && temporaryFailure) {
      temporaryFailure = false;
      return send({ error: { code: 'BUSY', message: '잠시 후 재시도' } }, 409);
    }
    documents.push({ ...payload.document, id: payload.document.id + '-revision',
      logical_id: payload.document.id, active: true, meta: payload.meta });
    if (payload.document.title === 'alpha') return route.abort('connectionreset');
    return send({ status: 'approved' });
  });
  await page.goto('/documents');
  await page.locator('[data-action=bulkDocuments]').click();
  const dialog = page.getByRole('dialog');
  await dialog.locator('input[type=file]').setInputFiles(['alpha', 'bravo', 'broken', 'missing'].map(name => ({
    name: name + '.pdf', mimeType: 'application/pdf',
    buffer: Buffer.from(name === 'broken' ? 'invalid PDF' : pdfFixture('Synthetic ' + name, '', 'Third page')),
  })));
  const group = name => dialog.getByRole('group', { name, exact: true });
  const common = group('공통 메타데이터');
  await common.getByLabel('발행기관').fill('Fixture');
  await common.getByLabel('언어').selectOption('ko');
  await common.getByText('선택 파일에 적용').click();
  await group('bravo.pdf').getByLabel('버전').fill('2');
  await group('bravo.pdf').getByLabel('열람 범위').selectOption('admin');
  await group('missing.pdf').getByLabel('문서명').fill('');
  await expect(group('missing.pdf')).toContainText('문서명 입력이 필요합니다');
  await group('alpha.pdf').getByText('추출 본문 미리보기').click();
  await expect(dialog.getByLabel('페이지별 추출 본문')).toHaveValue(/--- PAGE 3 ---\nThird page/);
  await expect(group('alpha.pdf')).toContainText('텍스트 없는 페이지 2');
  await dialog.getByLabel(/선택한 파일의 본문/).check();
  await dialog.getByRole('button', { name: /선택 파일 저장/ }).click();
  await expect(group('alpha.pdf').getByRole('status')).toHaveText('저장 완료');
  await expect(group('broken.pdf').getByRole('status')).toContainText('실패');
  await expect(group('bravo.pdf')).toContainText('잠시 후 재시도');
  await dialog.getByRole('button', { name: /실패 파일 재시도/ }).click();
  await expect(group('bravo.pdf').getByRole('status')).toHaveText('저장 완료');
  await expect(dialog.locator('input[type=file]')).toBeEnabled();
  expect(attempts.map(p => p.document.title)).toEqual(['alpha', 'bravo', 'bravo']);
  expect(attempts[1].document.id).toBe(attempts[2].document.id);
  expect(attempts[2]).toMatchObject({ expectedId: '', document: { version: '2', language: 'ko' }, meta: { scope: 'admin', issuer: 'Fixture' } });
  await dialog.getByRole('button', { name: '닫기' }).click();
  await expect(page.locator('.docrow')).toHaveCount(2);
  expect(errors).toEqual([]);
});
