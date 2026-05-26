// =====================================================================
// 시드 입력 (Phase 2) — Seed
// PDF/TXT 업로드 → /api/documents → requirement 입력 → /api/ontologies POST.
// 분 단위 걸리는 ontology 폴링과 plaza 생성은 다음 화면(Casting)이 맡는다 —
// Seed 는 "광장 열기" 버튼을 짧게 잠그고 바로 Casting 으로 넘긴다.
// =====================================================================

import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ScreenHeader } from '@/components/chrome';
import { Button, ArrowGlyph } from '@/components/atoms';
import { api, ApiError, type DocumentResponse, type Preset } from '@/api/client';

// --------------------------------------------------------------------
// 인격 규모 옵션. id 는 백엔드 Preset literal 과 1:1.
// 비용/시간/참가자 수는 데모용 표기 — 실제 비용은 백엔드 LLM 콜 수에 의존.
// --------------------------------------------------------------------
interface SeedPlan {
  id: Preset;
  name: string;
  participants: number;
  rounds: number;
  minutes: number;
  cost: number;
  desc: string;
}

const SEED_PLANS: SeedPlan[] = [
  { id: 'quick', name: 'Quick', participants: 100, rounds: 15, minutes: 2.5, cost: 380, desc: '빠르게 윤곽 잡기' },
  { id: 'standard', name: 'Standard', participants: 300, rounds: 40, minutes: 18.3, cost: 1240, desc: '권장 · 진영 형성과 변곡' },
  { id: 'full', name: 'Full', participants: 500, rounds: 50, minutes: 37.5, cost: 2980, desc: '정밀 · 군중 두께 + 영향력 분포' },
];

// 광장 열기 버튼이 거치는 단계. idle 외에는 사용자 입력을 모두 잠근다.
// 폴링/plaza 생성은 Casting 으로 넘어가 거기서 처리하므로 Seed 단계엔
// 'starting' 이 짧게만 (createOntology 응답 한 라운드) 잡힌다.
type Phase = 'idle' | 'uploading' | 'starting';

// 백엔드가 받는 확장자만 화이트리스트. 옛 prototype 의 docx/md 는 제외 — 백엔드
// _ALLOWED_EXTENSIONS 와 일치시킨다.
const ACCEPT_FILE = '.pdf,.txt,application/pdf,text/plain';
const MAX_UPLOAD_MB = 5;

// --------------------------------------------------------------------
// FileDropZone — 비어있을 때.
// --------------------------------------------------------------------
function FileDropZone({ onUpload, disabled }: { onUpload: (f: File) => void; disabled: boolean }) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <div
      className={`lm-seed__drop${disabled ? ' is-disabled' : ''}`}
      onClick={() => {
        if (disabled) return;
        inputRef.current?.click();
      }}
      onDragOver={(e) => {
        if (disabled) return;
        e.preventDefault();
        e.currentTarget.classList.add('is-over');
      }}
      onDragLeave={(e) => {
        e.currentTarget.classList.remove('is-over');
      }}
      onDrop={(e) => {
        e.preventDefault();
        e.currentTarget.classList.remove('is-over');
        if (disabled) return;
        const f = e.dataTransfer.files[0];
        if (f) onUpload(f);
      }}
    >
      <div className="lm-seed__drop-icon">
        <svg viewBox="0 0 64 64" width="64" height="64" aria-hidden="true">
          <rect x="14" y="10" width="32" height="44" rx="2" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <line x1="20" y1="22" x2="40" y2="22" stroke="currentColor" strokeWidth="1.6" />
          <line x1="20" y1="30" x2="40" y2="30" stroke="currentColor" strokeWidth="1.6" />
          <line x1="20" y1="38" x2="34" y2="38" stroke="currentColor" strokeWidth="1.6" />
          <path d="M 44 36 L 52 44 L 44 52 M 52 44 L 38 44" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
      <div className="lm-seed__drop-text">
        <div className="lm-seed__drop-h">자료를 끌어다 놓거나 클릭하세요</div>
        <div className="lm-seed__drop-sub">보고서·정책안·기사 한 건이면 광장이 열립니다</div>
        <div className="lm-seed__drop-formats">
          <span>PDF</span>
          <span>TXT</span>
          <span className="lm-seed__drop-formats-sub">· 최대 {MAX_UPLOAD_MB} MB</span>
        </div>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT_FILE}
        style={{ display: 'none' }}
        disabled={disabled}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onUpload(f);
          // 같은 파일을 다시 선택할 수 있게 input 값을 비운다.
          e.target.value = '';
        }}
      />
    </div>
  );
}

// --------------------------------------------------------------------
// FilePreview — 업로드된 파일 카드. /api/documents 가 반환한 메타만 보여준다.
// 옛 prototype 의 자동 요약/엔티티 프리뷰는 백엔드 응답에 없어 제거.
// --------------------------------------------------------------------
function FilePreview({ doc, onReplace, disabled }: { doc: DocumentResponse; onReplace: () => void; disabled: boolean }) {
  const ext = (doc.filename.split('.').pop() || 'FILE').toUpperCase();
  const sizeKB = Math.max(1, Math.round(doc.size_bytes / 1024));
  return (
    <div className="lm-seed__file">
      <div className="lm-seed__file-head">
        <div className="lm-seed__file-icon">
          <span className="lm-seed__file-ext">{ext}</span>
        </div>
        <div className="lm-seed__file-info">
          <div className="lm-seed__file-name">{doc.filename}</div>
          <div className="lm-seed__file-meta">
            <span>{sizeKB.toLocaleString()} KB</span>
            <span>{doc.mime_type}</span>
            <span>{doc.sha256.slice(0, 10)}…</span>
          </div>
        </div>
        <div className="lm-seed__file-actions">
          <Button kind="ghost" size="sm" onClick={onReplace} disabled={disabled}>
            교체
          </Button>
        </div>
      </div>

      <div className="lm-seed__file-summary">
        <div className="lm-seed__file-summary-label">업로드 확인</div>
        <p>
          문서가 서버에 저장되었습니다. 아래 시뮬레이션 목적을 한 줄 적고 광장을 열면
          Phase 1 인격 생성이 시작됩니다. 추출된 인격은 캐스팅 화면에서 확인할 수 있어요.
        </p>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// PlanCard — 인격 규모 옵션. 한 줄 정렬.
// --------------------------------------------------------------------
function PlanCard({
  plan,
  selected,
  onSelect,
  disabled,
}: {
  plan: SeedPlan;
  selected: boolean;
  onSelect: (id: Preset) => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(plan.id)}
      className={`lm-seed__plan${selected ? ' is-active' : ''}`}
      disabled={disabled}
    >
      <span className="lm-seed__plan-radio" aria-hidden="true">
        <span className="lm-seed__plan-radio-dot" />
      </span>
      <span className="lm-seed__plan-name">{plan.name}</span>
      <span className="lm-seed__plan-stats">
        <span className="lm-seed__plan-stat">
          <b>{plan.participants}</b>명
        </span>
        <span className="lm-seed__plan-divider">·</span>
        <span className="lm-seed__plan-stat">
          <b>{plan.rounds}</b>R
        </span>
        <span className="lm-seed__plan-divider">·</span>
        <span className="lm-seed__plan-stat">
          약 <b>{plan.minutes}</b>분
        </span>
      </span>
      <span className="lm-seed__plan-desc">{plan.desc}</span>
      <span className="lm-seed__plan-cost">₩ {plan.cost.toLocaleString()}</span>
    </button>
  );
}

// --------------------------------------------------------------------
// 단계 표기 — 사용자에게 "지금 뭐 하는 중" 한 줄.
// --------------------------------------------------------------------
function phaseLabel(phase: Phase): string {
  switch (phase) {
    case 'uploading':
      return '문서를 서버로 보내는 중…';
    case 'starting':
      return 'Phase 1 시작 요청 중…';
    default:
      return '';
  }
}

// --------------------------------------------------------------------
// ScreenSeed — 메인.
// --------------------------------------------------------------------
export default function Seed() {
  const navigate = useNavigate();
  const [doc, setDoc] = useState<DocumentResponse | null>(null);
  const [requirement, setRequirement] = useState('');
  const [planId, setPlanId] = useState<Preset>('standard');
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState<string | null>(null);

  const plan = SEED_PLANS.find((p) => p.id === planId)!;
  const busy = phase !== 'idle';
  // 입력 항목 다 채워졌고 작업 중이 아닐 때만 열린다. requirement 1~500 자
  // 제한은 백엔드 Pydantic 과 일치 — 여기서 미리 잘라 422 를 피한다.
  const requirementTrimmed = requirement.trim();
  const canStart =
    !busy && doc !== null && requirementTrimmed.length >= 1 && requirementTrimmed.length <= 500;

  const handleUpload = async (f: File) => {
    if (busy) return;
    setError(null);

    // 클라 측 사전 검증 — 백엔드도 같은 한도를 보지만 큰 파일을 보내 5MB 가
    // 넘는 걸 확인하는 라운드트립을 줄인다.
    if (f.size > MAX_UPLOAD_MB * 1024 * 1024) {
      setError(`파일이 너무 큽니다 — 최대 ${MAX_UPLOAD_MB} MB`);
      return;
    }

    setPhase('uploading');
    try {
      const res = await api.uploadDocument(f);
      setDoc(res);
    } catch (e) {
      setError(formatError(e, '업로드 실패'));
    } finally {
      setPhase('idle');
    }
  };

  const handleReplace = () => {
    if (busy) return;
    setDoc(null);
    setError(null);
  };

  const handleStart = async () => {
    if (!canStart || doc === null) return;
    setError(null);

    // POST 만 하고 응답이 떨어지면 곧장 Casting 으로 넘긴다. 분 단위 폴링과
    // plaza 생성은 거기서 처리 — Seed 화면이 길게 막혀 보이지 않도록.
    setPhase('starting');
    try {
      const created = await api.createOntology({
        document_id: doc.document_id,
        requirement: requirementTrimmed,
        preset: planId,
      });
      const search = new URLSearchParams({
        ontology: created.ontology_id,
        preset: planId,
        rounds: String(plan.rounds),
        label: doc.filename,
      });
      navigate(`/casting/new?${search.toString()}`);
    } catch (e) {
      setError(formatError(e, '인격 생성 요청 실패'));
      setPhase('idle');
    }
  };

  return (
    <div className="lm-seed">
      <div className="lm-seed__pad">
        <ScreenHeader
          eyebrow="Phase 2 · 시드 입력"
          title="시뮬레이션할 이슈를 올려주세요."
          subtitle="보고서·정책안·기사 — 자료 한 건이면 가상 광장이 열립니다."
        />

        <div className="lm-seed__grid">
          {/* LEFT — UPLOAD / PREVIEW + REQUIREMENT */}
          <section className="lm-seed__left">
            <div className="lm-seed__section-head">
              <span className="lm-seed__section-tag">01 · INPUT</span>
              <span className="lm-seed__section-h">자료 업로드</span>
            </div>

            {doc ? (
              <FilePreview doc={doc} onReplace={handleReplace} disabled={busy} />
            ) : (
              <FileDropZone onUpload={handleUpload} disabled={busy} />
            )}

            {doc && (
              <div className="lm-seed__requirement">
                <label className="lm-seed__requirement-label" htmlFor="lm-seed-requirement">
                  시뮬레이션 목적 (1~500자)
                </label>
                <textarea
                  id="lm-seed-requirement"
                  className="lm-seed__requirement-textarea"
                  value={requirement}
                  onChange={(e) => setRequirement(e.target.value.slice(0, 500))}
                  placeholder="예) 주 4일제 도입에 대한 시민 반응을 보고 싶다"
                  rows={3}
                  disabled={busy}
                />
                <div className="lm-seed__requirement-count">{requirement.length} / 500</div>
              </div>
            )}
          </section>

          {/* RIGHT — PLAN PICKER */}
          <section className="lm-seed__right">
            <div className="lm-seed__section-head">
              <span className="lm-seed__section-tag">02 · SCALE</span>
              <span className="lm-seed__section-h">인격 규모</span>
            </div>

            <div className="lm-seed__plans">
              {SEED_PLANS.map((p) => (
                <PlanCard
                  key={p.id}
                  plan={p}
                  selected={planId === p.id}
                  onSelect={setPlanId}
                  disabled={busy}
                />
              ))}
            </div>

            {(busy || error) && (
              <div className={`lm-seed__status${error ? ' is-error' : ''}`} role="status">
                {error ?? phaseLabel(phase)}
              </div>
            )}

            <footer className="lm-seed__footer">
              <div className="lm-seed__footer-left">
                <div className="lm-seed__footer-label">총 비용 · 소요 시간</div>
                <div className="lm-seed__footer-amount">
                  ₩ {plan.cost.toLocaleString()}
                  <span className="lm-seed__footer-min">· 약 {plan.minutes}분</span>
                </div>
              </div>
              <Button
                kind="primary"
                size="lg"
                disabled={!canStart}
                onClick={handleStart}
                trailing={<ArrowGlyph dir="right" />}
              >
                {phase === 'idle' ? '광장 열기' : phaseLabel(phase)}
              </Button>
            </footer>
          </section>
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// 사용자에게 표시할 에러 메시지로 정규화. ApiError 면 status + body 일부,
// 그 외엔 fallback 문구.
// --------------------------------------------------------------------
function formatError(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const detail = err.message.length > 200 ? err.message.slice(0, 200) + '…' : err.message;
    return `${fallback} (${err.status}): ${detail}`;
  }
  if (err instanceof Error) return `${fallback}: ${err.message}`;
  return fallback;
}
