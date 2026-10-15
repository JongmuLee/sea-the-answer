'use client';
import React, {useEffect,useRef,useState} from 'react';
import {useRouter} from 'next/navigation';
import {useWorkspace} from '../workspace/provider';
import {SkillResult} from './skill-result';

export default function SkillsPanel() {
  const {workspace}=useWorkspace();
  const state=workspace.state;
  const router=useRouter();
  const [catalog,setCatalog]=useState([]),[history,setHistory]=useState([]);
  const [skill,setSkill]=useState('selected-document-answer');
  const [question,setQuestion]=useState(''),[documents,setDocuments]=useState([]);
  const [ship,setShip]=useState(''),[from,setFrom]=useState(''),[to,setTo]=useState('');
  const [year,setYear]=useState(''),[template,setTemplate]=useState('mrv');
  const [useModel,setUseModel]=useState(false),[answerId,setAnswerId]=useState('');
  const [answer,setAnswer]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [saving,setSaving]=useState(false),[savedId,setSavedId]=useState('');
  const generation=useRef(0);
  const identity=state.user?.id;
  useEffect(()=>{
    const sequence=++generation.current;
    setAnswer(null);setError('');setBusy(false);setDocuments([]);setSavedId('');setSaving(false);setAnswerId('');setHistory([]);
    setShip('');setFrom('');setTo('');setYear('');setQuestion('');
    if (state.ready) {
      Promise.all([workspace.api.request('/api/skills'),workspace.api.request('/api/history')])
        .then(([c,h])=>{if(sequence===generation.current){setCatalog(c.skills);setHistory(h.history.filter(v=>!v.unavailable));}})
        .catch(e=>{if(sequence===generation.current)setError(e.message);});
    }
    return ()=>{generation.current++;};
  },[identity,state.ready,workspace]);
  const selected=catalog.find(v=>v.id===skill);
  const needsDocs=['selected-document-answer','checklist','report-draft'].includes(skill);
  const needsShip=['mrv-analysis','operation-summary','report-draft'].includes(skill);
  async function execute(e){
    e.preventDefault();e.stopPropagation();
    if(busy)return;
    const sequence=++generation.current;
    setBusy(true);setError('');setAnswer(null);setSavedId('');
    try {
      const body={skill,question,document_ids:needsDocs?documents:[],context:{ship,from,to},
        use_model:Boolean(selected?.optional_model && useModel),template,answer_id:skill==='qa-evaluation'?answerId:''};
      if(skill==='mrv-analysis' && year)body.year=Number(year);
      const value=await workspace.api.request('/api/skills/run',body);
      if(sequence!==generation.current)return;
      setAnswer(value);
      setHistory(old=>[{id:value.id,question:value.question,answer:value},...old].slice(0,100));
    } catch(e){if(sequence===generation.current)setError(e.message);}
    finally{if(sequence===generation.current)setBusy(false);}
  }
  async function saveDraft(){
    if(saving || savedId || !answer?.result?.draft)return;
    const sequence=generation.current;
    setSaving(true);setError('');
    try{
      const value=await workspace.api.request('/api/reports',answer.result.draft);
      if(sequence===generation.current){
        workspace.acceptSavedReport(value.report,identity);
        setSavedId(value.report.id);
      }
    }catch(e){if(sequence===generation.current)setError(e.message);}
    finally{if(sequence===generation.current)setSaving(false);}
  }
  function changeSkill(value){generation.current++;setSkill(value);setAnswer(null);setError('');setBusy(false);setSaving(false);setSavedId('');}
  return <div className="skills-page">
    <header><h1>업무 스킬</h1><p>필요한 업무와 자료를 선택해 실행하세요. AI 없이도 조회·계산·초안 생성이 가능합니다.</p></header>
    <form className="card pad skill-form" onSubmit={execute}>
      <label>실행할 스킬<select aria-label="실행할 스킬" value={skill} onChange={e=>changeSkill(e.target.value)} disabled={!catalog.length}>
        {catalog.map(v=><option key={v.id} value={v.id}>{v.name}</option>)}
      </select></label>
      <p className="small muted">{selected?.description}</p>
      <label>질문 또는 작업 목적<textarea value={question} onChange={e=>setQuestion(e.target.value)} maxLength={2000} required rows={3} placeholder="예: 연료 전환 전에 확인할 내용을 정리해줘" /></label>
      {needsDocs && <label>사용할 문서 {skill==='report-draft'?'(선택 사항)':'(필수)'}
        <select multiple size={Math.min(6,Math.max(2,state.docs.filter(d=>d.active).length))} aria-label="사용할 문서" value={documents} onChange={e=>setDocuments([...e.target.selectedOptions].map(o=>o.value))}>
          {state.docs.filter(d=>d.active).map(d=><option key={d.id} value={d.id}>{d.title} · {d.version}</option>)}
        </select><small>Ctrl 또는 Shift로 여러 문서를 선택할 수 있습니다. 최대 20개.</small>
      </label>}
      {needsShip && <label>선박<select aria-label="선박" value={ship} onChange={e=>setShip(e.target.value)}><option value="">선박 선택</option>
        {state.ships.map(s=><option key={s.id} value={s.id}>{s.name}{s.sample?' (가상 예시)':''}</option>)}
      </select></label>}
      {needsShip && skill!=='mrv-analysis' && <div className="skill-dates">
        <label>시작일<input type="date" value={from} onChange={e=>setFrom(e.target.value)} /></label>
        <label>종료일<input type="date" value={to} onChange={e=>setTo(e.target.value)} /></label>
      </div>}
      {skill==='mrv-analysis' && <><label>MRV 보고연도<input type="number" min={1900} max={2100} step={1} value={year} onChange={e=>setYear(e.target.value)} /></label><p className="small">MRV에 연결된 실제 선박만 계산합니다. 공개 DWT 후보·추정거리 기반 참고값이며 공식 CII가 아닙니다.</p></>}
      {skill==='report-draft' && <label>양식<select aria-label="양식" value={template} onChange={e=>setTemplate(e.target.value)}><option value="mrv">MRV 검토용 초안</option><option value="noon">Noon 검토용 초안 (마지막 기록일)</option></select></label>}
      {skill==='qa-evaluation' && <label>평가할 내 응답<select aria-label="평가할 내 응답" value={answerId} onChange={e=>setAnswerId(e.target.value)}><option value="">질의 이력 선택</option>{history.map(h=><option key={h.id} value={h.id}>{h.question}</option>)}</select></label>}
      {selected?.optional_model && <label className="inline"><input type="checkbox" checked={useModel} onChange={e=>setUseModel(e.target.checked)} /> 선택 문서의 설명을 AI로 정리{!state.health?.llmConfigured?' (AI 미설정 시 원문 발췌)':''}</label>}
      <button type="submit" className="btn primary" disabled={busy || !state.ready || !catalog.length}>{busy?'실행 중…':'스킬 실행'}</button>
    </form>
    {error && <p role="alert" className="notice warning">{error}</p>}
    {answer && <article className="card pad mt" aria-live="polite">
      <h2>{answer.skill.name}</h2><p>{answer.notice}</p>
      {answer.statements.map((s,i)=>{const e=answer.evidence.find(v=>v.id===s.chunkId);return <div key={`${s.chunkId}-${i}`} className="skill-evidence"><p>{s.text}</p><small>{e?.title} · {e?.version} · {e?.page?`p. ${e.page}`:'페이지 미제공'}</small><details><summary>인용 원문</summary><blockquote>{s.quote}</blockquote></details></div>;})}
      {!answer.statements.length && answer.evidence.length > 0 && <details><summary>검색된 원문 근거 확인</summary>{answer.evidence.map(e=><div key={e.id} className="skill-evidence"><p>{e.text}</p><small>{e.title} · {e.version} · {e.page?`p. ${e.page}`:'페이지 미제공'}</small></div>)}</details>}
      <SkillResult key={answer.id} answer={answer} />
      {answer.result?.draft && <div className="actions mt">
        <button type="button" className="btn primary" onClick={saveDraft} disabled={saving || Boolean(savedId)}>{savedId?'초안 저장됨':saving?'저장 중…':'새 보고서 초안으로 저장'}</button>
        {savedId && <button type="button" className="btn" onClick={()=>router.push('/reports/'+encodeURIComponent(savedId))}>저장한 초안 열기</button>}
      </div>}
      <p className="small muted">{answer.skill.version} · {answer.generation==='llm'?'AI 설명과 인용 검증':'기본 실행'} · {answer.status}</p>
    </article>}
  </div>;
}
