// =====================================================================
// 시드 입력 (Phase 2) — Seed
// (screen-seed.jsx → ES 모듈 + 타입. useStateSeed/useRefSeed → useState/useRef)
// =====================================================================

import { useRef, useState } from 'react';
import { ScreenHeader } from '@/components/chrome';
import { Button, ArrowGlyph } from '@/components/atoms';
import { useScreenNav } from '@/lib/nav';

// --------------------------------------------------------------------
// 인격 규모 옵션
// --------------------------------------------------------------------
interface SeedPlan {
  id: string;
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

interface SeedFile {
  name: string;
  type: string;
  sizeKB: number;
  pages: number;
  summary: string;
  entitiesPreview: string[];
}

const SAMPLE_FILE: SeedFile = {
  name: '주4일제_도입_정책분석.pdf',
  type: 'PDF',
  sizeKB: 482,
  pages: 14,
  summary:
    '주 4일제 도입을 둘러싼 한국 사회의 입장 분포. 정세훈 의원 발의안, 최영민 기자 비판 보도, 한지영 칼럼, 박서경 교수 OECD 비교, 전국노동연대 시범사업 성명을 다룬다.',
  entitiesPreview: ['정세훈 의원', '최영민 기자', '한지영', '박서경 교수', '전국노동연대'],
};

// --------------------------------------------------------------------
// FileDropZone — 비어있을 때.
// --------------------------------------------------------------------
function FileDropZone({ onUpload }: { onUpload: (f: File) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <div
      className="lm-seed__drop"
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        e.currentTarget.classList.add('is-over');
      }}
      onDragLeave={(e) => {
        e.currentTarget.classList.remove('is-over');
      }}
      onDrop={(e) => {
        e.preventDefault();
        e.currentTarget.classList.remove('is-over');
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
          <span>DOCX</span>
          <span>TXT</span>
          <span>MD</span>
          <span className="lm-seed__drop-formats-sub">· 최대 30 MB</span>
        </div>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.docx,.txt,.md,application/pdf"
        style={{ display: 'none' }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onUpload(f);
        }}
      />
    </div>
  );
}

// --------------------------------------------------------------------
// FilePreview — 업로드된 파일 카드 (실/샘플 공통).
// --------------------------------------------------------------------
function FilePreview({ file, onReplace }: { file: SeedFile; onReplace: () => void }) {
  return (
    <div className="lm-seed__file">
      <div className="lm-seed__file-head">
        <div className="lm-seed__file-icon">
          <span className="lm-seed__file-ext">{file.type}</span>
        </div>
        <div className="lm-seed__file-info">
          <div className="lm-seed__file-name">{file.name}</div>
          <div className="lm-seed__file-meta">
            <span>{file.sizeKB} KB</span>
            <span>{file.pages}쪽</span>
            <span>한국어 · 자동감지</span>
          </div>
        </div>
        <div className="lm-seed__file-actions">
          <Button kind="ghost" size="sm" onClick={onReplace}>
            교체
          </Button>
        </div>
      </div>

      <div className="lm-seed__file-summary">
        <div className="lm-seed__file-summary-label">자동 요약</div>
        <p>{file.summary}</p>
      </div>

      <div className="lm-seed__file-entities">
        <div className="lm-seed__file-entities-label">추출된 인격 (캐스팅 후보)</div>
        <div className="lm-seed__file-entities-list">
          {file.entitiesPreview.map((e, i) => (
            <span key={i} className="lm-seed__file-entity">
              {e}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// PlanCard — 인격 규모 옵션. 한 줄 정렬.
// --------------------------------------------------------------------
function PlanCard({ plan, selected, onSelect }: { plan: SeedPlan; selected: boolean; onSelect: (id: string) => void }) {
  return (
    <button type="button" onClick={() => onSelect(plan.id)} className={`lm-seed__plan${selected ? ' is-active' : ''}`}>
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
// ScreenSeed — 메인.
// --------------------------------------------------------------------
export default function Seed() {
  const go = useScreenNav();
  const [file, setFile] = useState<SeedFile | null>(null);
  const [planId, setPlanId] = useState('standard');
  const plan = SEED_PLANS.find((p) => p.id === planId)!;

  const handleUpload = (f: File) => {
    // 실 제품: 업로드 → 서버 추출 → entity preview. 프로토타입: 파일명만 받아 샘플로 채움.
    setFile({
      ...SAMPLE_FILE,
      name: f.name || SAMPLE_FILE.name,
      type: (f.name?.split('.').pop() || 'PDF').toUpperCase(),
      sizeKB: Math.round((f.size || 482000) / 1024),
    });
  };
  const handleReplace = () => setFile(null);
  const canStart = !!file;

  return (
    <div className="lm-seed">
      <div className="lm-seed__pad">
        <ScreenHeader
          eyebrow="Phase 2 · 시드 입력"
          title="시뮬레이션할 이슈를 올려주세요."
          subtitle="보고서·정책안·기사 — 자료 한 건이면 가상 광장이 열립니다."
        />

        <div className="lm-seed__grid">
          {/* LEFT — UPLOAD / PREVIEW */}
          <section className="lm-seed__left">
            <div className="lm-seed__section-head">
              <span className="lm-seed__section-tag">01 · INPUT</span>
              <span className="lm-seed__section-h">자료 업로드</span>
            </div>

            {file ? <FilePreview file={file} onReplace={handleReplace} /> : <FileDropZone onUpload={handleUpload} />}
          </section>

          {/* RIGHT — PLAN PICKER */}
          <section className="lm-seed__right">
            <div className="lm-seed__section-head">
              <span className="lm-seed__section-tag">02 · SCALE</span>
              <span className="lm-seed__section-h">인격 규모</span>
            </div>

            <div className="lm-seed__plans">
              {SEED_PLANS.map((p) => (
                <PlanCard key={p.id} plan={p} selected={planId === p.id} onSelect={setPlanId} />
              ))}
            </div>

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
                onClick={() => canStart && go('casting')}
                trailing={<ArrowGlyph dir="right" />}
              >
                광장 열기
              </Button>
            </footer>
          </section>
        </div>
      </div>
    </div>
  );
}
