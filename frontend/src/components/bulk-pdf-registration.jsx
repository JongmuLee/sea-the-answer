'use client';

import { useRef, useState } from 'react';
import { applyCommon, newPdfRow, rowErrors, savePdfBatch } from '../lib/bulk-documents';
import { extractPdf, sectionsToText } from '../lib/pdf';
import { UiText } from './ui-language';

const kinds = [['onboard', '선내 문서·규정 원문'], ['official-summary', '공식 안내 요약'], ['sample', '가상 샘플']];
const languages = [['auto', '자동 판별'], ['ko', '한국어'], ['en', '영어']];
const scopes = [['all', '로그인 사용자'], ['operator', '선원·관리자'], ['admin', '관리자만']];
const states = { pending: '등록 대기', processing: '처리 중', saved: '저장 완료', failed: '실패' };
const unchanged = ['keep', '개별 값 유지'];

function Choice({ label, value, items, onChange }) {
  return <label className="field"><UiText>{label}</UiText>
    <select value={value} onChange={e => onChange(e.target.value)}>
      {items.map(([key, name]) => <option key={key} value={key}><UiText>{name}</UiText></option>)}
    </select>
  </label>;
}

function Input({ label, value, onChange, ...props }) {
  return <label className="field"><UiText>{label}</UiText>
    <input {...props} value={value} onChange={e => onChange(e.target.value)} />
  </label>;
}

export default function BulkPdfRegistration({ save, exists, refresh, isOpen }) {
  const [rows, setRows] = useState([]);
  const [common, setCommon] = useState({ kind: 'keep', language: 'keep', issuer: '' });
  const [preview, setPreview] = useState(null);
  const [confirmed, confirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  const [notice, setNotice] = useState('');

  function updateRow(id, patch) {
    setRows(current => current.map(row => row.id === id ? { ...row, ...patch } : row));
  }
  function edit(id, patch) {
    confirm(false);
    updateRow(id, patch);
  }
  function apply() {
    const changes = {};
    if (common.kind !== 'keep') changes.kind = common.kind;
    if (common.language !== 'keep') changes.language = common.language === 'auto' ? '' : common.language;
    if (common.issuer.trim()) changes.issuer = common.issuer.trim();
    setRows(current => applyCommon(current, changes));
    confirm(false);
  }
  async function run(action) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setNotice('');
    setPreview(null);
    try { await action(); }
    catch (error) { setNotice(error.message); }
    finally {
      running.current = false;
      setBusy(false);
    }
  }
  function inspect(row) {
    return run(async () => {
      try {
        const data = await extractPdf(row.file, progress => updateRow(row.id, { progress }), { includeFile: false });
        updateRow(row.id, { progress: '', pageCount: data.sections.length, emptyPages: data.emptyPages });
        setPreview({ id: row.id, text: sectionsToText(data.sections) });
      } catch (error) {
        updateRow(row.id, { state: 'failed', progress: '', error: error.message });
      }
    });
  }
  function register(retry) {
    return run(async () => {
      await savePdfBatch(rows, { save, exists, updateRow, isOpen }, retry);
      try { await refresh(); }
      catch { setNotice('문서 목록을 갱신하지 못했습니다. 문서 화면에서 새로고침해 주세요.'); }
    });
  }
  const ready = state => rows.filter(row => row.selected && row.state === state && !rowErrors(row).length).length;
  const pending = ready('pending'), failed = ready('failed');
  const editable = rows.filter(row => row.state !== 'saved');
  return <div>
    <label className="field"><UiText>PDF 파일 선택 (파일당 최대 25MB)</UiText>
      <input type="file" multiple accept=".pdf,application/pdf" disabled={busy} onChange={e => {
        const added = Array.from(e.target.files, newPdfRow);
        e.target.value = '';
        setRows(current => [...current, ...added]);
        confirm(false);
      }} />
    </label>
    {rows.length > 0 && <>
      <fieldset className="mt" disabled={busy}>
        <legend><UiText>공통 메타데이터</UiText></legend>
        <div className="grid2">
          <Choice label="문서 종류" value={common.kind} items={[unchanged, ...kinds]} onChange={kind => setCommon({ ...common, kind })} />
          <Choice label="언어" value={common.language} items={[unchanged, ...languages]} onChange={language => setCommon({ ...common, language })} />
          <Input label="발행기관" value={common.issuer} maxLength={200} onChange={issuer => setCommon({ ...common, issuer })} />
        </div>
        <div className="actions mt">
          <label><input type="checkbox" checked={!!editable.length && editable.every(row => row.selected)}
            onChange={e => { const selected = e.target.checked; setRows(rows.map(row => row.state === 'saved' ? row : { ...row, selected })); confirm(false); }} /> <UiText>전체 선택</UiText></label>
          <button type="button" className="btn" onClick={apply}><UiText>선택 파일에 적용</UiText></button>
        </div>
      </fieldset>
      {rows.map(row => {
        const errors = rowErrors(row);
        const locked = busy || row.state === 'saved' || row.uncertain;
        return <fieldset className="mt" key={row.id}>
          <legend><label><input type="checkbox" checked={row.selected} disabled={busy || row.state === 'saved'}
            onChange={e => edit(row.id, { selected: e.target.checked })} /> {row.file.name}</label></legend>
          <fieldset disabled={locked} style={{ border: 0, padding: 0 }}>
            <div className="grid2">
              <Input label="문서명" value={row.title} maxLength={200} onChange={title => edit(row.id, { title })} />
              <Input label="버전" value={row.version} maxLength={100} onChange={version => edit(row.id, { version })} />
              <Input label="조항·참조명" value={row.reference} maxLength={200} onChange={reference => edit(row.id, { reference })} />
              <Choice label="문서 종류" value={row.kind} items={kinds} onChange={kind => edit(row.id, { kind })} />
              <Choice label="언어" value={row.language || 'auto'} items={languages} onChange={val => edit(row.id, { language: val === 'auto' ? '' : val })} />
              <Input label="발행기관" value={row.issuer} maxLength={200} onChange={issuer => edit(row.id, { issuer })} />
              <Choice label="열람 범위" value={row.scope} items={scopes} onChange={scope => edit(row.id, { scope })} />
              <Input label="외부 원문 링크" value={row.url} type="url" onChange={url => edit(row.id, { url })} />
            </div>
          </fieldset>
          {!!errors.length && <p className="inline-error">{errors.join(' ')}</p>}
          <p role="status"><UiText>{states[row.state]}</UiText> {row.progress} {row.error}</p>
          {row.pageCount != null && <p className="small">본문 추출 {row.pageCount}페이지
            {!!row.emptyPages.length && <> · 텍스트 없는 페이지 {row.emptyPages.join(', ')}</>}</p>}
          {row.state !== 'saved' && <button type="button" className="btn" disabled={busy} onClick={() => inspect(row)}><UiText>추출 본문 미리보기</UiText></button>}
          {preview?.id === row.id && <label className="field"><UiText>페이지별 추출 본문</UiText>
            <textarea readOnly rows={8} value={preview.text} /></label>}
        </fieldset>;
      })}
      <p className="small muted mt"><UiText>저장 완료 후 검색 색인이 별도로 진행됩니다. 처리 중에는 창을 열어 두세요.</UiText></p>
      <label className="checkline"><input type="checkbox" disabled={busy} checked={confirmed} onChange={e => confirm(e.target.checked)} />
        <UiText>선택한 파일의 본문·페이지, 버전과 열람 범위를 확인했습니다.</UiText></label>
      <div className="actions mt">
        <button type="button" className="btn primary" disabled={busy || !confirmed || !pending} onClick={() => register(false)}><UiText>선택 파일 저장</UiText> ({pending})</button>
        {rows.some(row => row.state === 'failed') && <button type="button" className="btn" disabled={busy || !confirmed || !failed} onClick={() => register(true)}><UiText>실패 파일 재시도</UiText> ({failed})</button>}
      </div>
    </>}
    {notice && <p role="alert">{notice}</p>}
  </div>;
}
