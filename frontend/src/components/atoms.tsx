// =====================================================================
// 라이트미로 — 공통 UI 컴포넌트 (atoms)
// 디자인 토큰을 기반으로 한 atomic 컴포넌트. screen-*에서 조합.
// (components.jsx → ES 모듈 + 타입. window.LM.* → lm.*)
// =====================================================================

import type { ReactNode } from 'react';
import { lm } from '@/data/mock';
import type { Expr, Pose, Prop, RoleId } from '@/data/types';

// --------------------------------------------------------------------
// Avatar — D02 / D06 사양
// head + shoulder + prop + expression. 사이즈에 따라 자동 디테일 OFF.
// size: lg(72+) / md(40~64) / sm(20~32) / dot(<=16)
// --------------------------------------------------------------------
export interface AvatarProps {
  roleId: RoleId;
  pose?: Pose;
  prop?: Prop;
  expr?: Expr;
  size?: number;
  shade?: number;
}

export function AvatarSVG({ roleId, pose = 'P1', prop = 'O0', expr = 'E1', size = 56, shade = 0 }: AvatarProps) {
  const role = lm.ROLE_BY_ID[roleId] || lm.ROLES[0];
  const color = lm.shadeHex(role.color, shade);
  const variant = size <= 16 ? 'dot' : size <= 32 ? 'sm' : size <= 64 ? 'md' : 'lg';

  if (variant === 'dot') {
    return (
      <svg viewBox="0 0 20 20" width={size} height={size} aria-label={role.name}>
        <circle cx="10" cy="10" r="9" fill={color} />
      </svg>
    );
  }

  const S = 100;
  const cx = S * 0.5;
  const cy = S * 0.42;
  const headR = S * 0.22;
  const sw = S * 1.0;
  const sy = cy + headR + S * 0.02;

  // pose: shoulder path + head offset
  let path = '';
  let hx = cx;
  let hy = cy;
  switch (pose) {
    case 'P2': // 손짓
      path = `M ${cx - sw * 0.4} ${S} C ${cx - sw * 0.4} ${sy + S * 0.06}, ${cx - sw * 0.18} ${sy - S * 0.02}, ${cx} ${sy - S * 0.02}
              C ${cx + sw * 0.22} ${sy - S * 0.06}, ${cx + sw * 0.46} ${sy}, ${cx + sw * 0.46} ${S} Z`;
      break;
    case 'P3': // 팔짱
      path = `M ${cx - sw * 0.32} ${S} C ${cx - sw * 0.32} ${sy + S * 0.1}, ${cx - sw * 0.16} ${sy + S * 0.02}, ${cx} ${sy + S * 0.02}
              C ${cx + sw * 0.16} ${sy + S * 0.02}, ${cx + sw * 0.32} ${sy + S * 0.1}, ${cx + sw * 0.32} ${S} Z`;
      break;
    case 'P4': // 도구 들기
      path = `M ${cx - sw * 0.38} ${S} C ${cx - sw * 0.38} ${sy + S * 0.06}, ${cx - sw * 0.18} ${sy - S * 0.02}, ${cx} ${sy - S * 0.02}
              C ${cx + sw * 0.2} ${sy - S * 0.02}, ${cx + sw * 0.4} ${sy + S * 0.04}, ${cx + sw * 0.4} ${S} Z`;
      break;
    case 'P5': // 고개 기울이기
      hx = cx - S * 0.04;
      hy = cy - S * 0.02;
      path = `M ${cx - sw * 0.42} ${S} C ${cx - sw * 0.42} ${sy + S * 0.06}, ${cx - sw * 0.18} ${sy - S * 0.02}, ${cx} ${sy - S * 0.02}
              C ${cx + sw * 0.18} ${sy - S * 0.02}, ${cx + sw * 0.42} ${sy + S * 0.06}, ${cx + sw * 0.42} ${S} Z`;
      break;
    case 'P6': // 반쯤 돌아섬
      hx = cx - S * 0.05;
      path = `M ${cx - sw * 0.38} ${S} C ${cx - sw * 0.4} ${sy + S * 0.1}, ${cx - sw * 0.22} ${sy}, ${cx - S * 0.02} ${sy - S * 0.02}
              C ${cx + sw * 0.12} ${sy - S * 0.02}, ${cx + sw * 0.34} ${sy + S * 0.06}, ${cx + sw * 0.34} ${S} Z`;
      break;
    case 'P1':
    default:
      path = `M ${cx - sw * 0.42} ${S} C ${cx - sw * 0.42} ${sy + S * 0.06}, ${cx - sw * 0.18} ${sy - S * 0.02}, ${cx} ${sy - S * 0.02}
              C ${cx + sw * 0.18} ${sy - S * 0.02}, ${cx + sw * 0.42} ${sy + S * 0.06}, ${cx + sw * 0.42} ${S} Z`;
  }

  const detailExpr = variant !== 'sm';
  const detailProp = variant !== 'sm';
  const inkc = '#1A1813';
  const lw = 1.8;

  // prop
  let propEl: ReactNode = null;
  if (detailProp) {
    if (prop === 'O1') {
      propEl = (
        <g>
          <circle cx={hx - headR * 0.42} cy={hy - S * 0.02} r={headR * 0.28} fill="none" stroke={inkc} strokeWidth={lw} />
          <circle cx={hx + headR * 0.42} cy={hy - S * 0.02} r={headR * 0.28} fill="none" stroke={inkc} strokeWidth={lw} />
          <line x1={hx - headR * 0.14} y1={hy - S * 0.02} x2={hx + headR * 0.14} y2={hy - S * 0.02} stroke={inkc} strokeWidth={lw} />
        </g>
      );
    } else if (prop === 'O2') {
      propEl = <rect x={cx + S * 0.3} y={S * 0.7} width={S * 0.18} height={S * 0.22} fill="#FAF7EE" stroke={inkc} strokeWidth={lw} />;
    } else if (prop === 'O3') {
      propEl = (
        <path
          d={`M ${hx - headR * 0.95} ${hy - headR * 0.55}
              Q ${hx} ${hy - headR * 1.55}, ${hx + headR * 0.95} ${hy - headR * 0.55}
              Q ${hx} ${hy - headR * 0.18}, ${hx - headR * 0.95} ${hy - headR * 0.55} Z`}
          fill={inkc}
        />
      );
    } else if (prop === 'O4') {
      propEl = (
        <path
          d={`M ${cx - S * 0.3} ${sy + S * 0.05} Q ${cx} ${sy + S * 0.18}, ${cx + S * 0.3} ${sy + S * 0.05}
              L ${cx + S * 0.3} ${sy + S * 0.18} Q ${cx} ${sy + S * 0.3}, ${cx - S * 0.3} ${sy + S * 0.18} Z`}
          fill={lm.shadeHex(color, -0.3)}
        />
      );
    } else if (prop === 'O5') {
      propEl = <circle cx={cx + S * 0.1} cy={sy + S * 0.2} r={Math.max(2, S * 0.04)} fill="#FAF7EE" stroke={inkc} strokeWidth={lw * 0.8} />;
    }
  }

  // expression
  const eyeY = hy + headR * 0.05;
  const eyeR = Math.max(1, headR * 0.1);
  const mouthY = hy + headR * 0.45;
  const mouthW = headR * 0.42;
  let mouth: ReactNode;
  if (!detailExpr || expr === 'E1') {
    mouth = <line x1={hx - mouthW} y1={mouthY} x2={hx + mouthW} y2={mouthY} stroke={inkc} strokeWidth={lw} strokeLinecap="round" />;
  } else if (expr === 'E2') {
    mouth = <path d={`M ${hx - mouthW} ${mouthY - 1} Q ${hx} ${mouthY + 4}, ${hx + mouthW} ${mouthY - 1}`} fill="none" stroke={inkc} strokeWidth={lw} strokeLinecap="round" />;
  } else if (expr === 'E3') {
    mouth = <path d={`M ${hx - mouthW} ${mouthY + 1} Q ${hx} ${mouthY - 2}, ${hx + mouthW} ${mouthY + 1}`} fill="none" stroke={inkc} strokeWidth={lw} strokeLinecap="round" />;
  }

  return (
    <svg viewBox={`0 0 ${S} ${S}`} width={size} height={size} aria-label={role.name}>
      <path d={path} fill={color} />
      <circle cx={hx} cy={hy} r={headR} fill={color} />
      {propEl}
      {detailExpr && (
        <g>
          <circle cx={hx - headR * 0.32} cy={eyeY} r={eyeR} fill={inkc} />
          <circle cx={hx + headR * 0.32} cy={eyeY} r={eyeR} fill={inkc} />
          {mouth}
        </g>
      )}
    </svg>
  );
}

// --------------------------------------------------------------------
// Atomic UI — Pill, Badge, RoleChip, Button
// --------------------------------------------------------------------
export interface PillProps {
  children: ReactNode;
  color?: string;
  active?: boolean;
  onClick?: () => void;
  size?: 'sm' | 'md' | 'lg';
}

export function Pill({ children, color, active = false, onClick, size = 'md' }: PillProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`lm-pill lm-pill--${size}${active ? ' is-active' : ''}`}
      style={(color ? { '--pill-dot': color } : undefined) as unknown as React.CSSProperties}
    >
      {color && <span className="lm-pill__dot" />}
      <span>{children}</span>
    </button>
  );
}

export function Badge({ children, tone = 'default' }: { children: ReactNode; tone?: string }) {
  return <span className={`lm-badge lm-badge--${tone}`}>{children}</span>;
}

export function RoleSwatch({ roleId, size = 12 }: { roleId: RoleId; size?: number }) {
  const role = lm.ROLE_BY_ID[roleId];
  if (!role) return null;
  return (
    <span
      className="lm-swatch"
      style={{
        width: `calc(${size}px * var(--scale))`,
        height: `calc(${size}px * var(--scale))`,
        background: role.color,
      }}
    />
  );
}

export interface ButtonProps {
  children: ReactNode;
  kind?: 'primary' | 'secondary' | 'ghost' | 'link';
  size?: 'sm' | 'md' | 'lg';
  onClick?: () => void;
  trailing?: ReactNode;
  disabled?: boolean;
}

export function Button({ children, kind = 'primary', size = 'md', onClick, trailing, disabled }: ButtonProps) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={`lm-btn lm-btn--${kind} lm-btn--${size}`}>
      <span>{children}</span>
      {trailing}
    </button>
  );
}

export function ArrowGlyph({ dir = 'right', size = 10 }: { dir?: 'right' | 'left' | 'up' | 'down'; size?: number }) {
  const rot = { right: 45, left: 225, up: -45, down: 135 }[dir];
  return (
    <span
      className="lm-arrow"
      style={{
        width: `calc(${size}px * var(--scale))`,
        height: `calc(${size}px * var(--scale))`,
        transform: `rotate(${rot}deg)`,
      }}
    />
  );
}

// --------------------------------------------------------------------
// SectionLabel — eyebrow + 큰 제목 (스크린 헤딩 표준)
// --------------------------------------------------------------------
export function SectionLabel({ eyebrow, title, sub }: { eyebrow?: ReactNode; title?: ReactNode; sub?: ReactNode }) {
  return (
    <div className="lm-section">
      {eyebrow && <div className="lm-section__eyebrow">{eyebrow}</div>}
      {title && <h1 className="lm-section__title">{title}</h1>}
      {sub && <p className="lm-section__sub">{sub}</p>}
    </div>
  );
}

// --------------------------------------------------------------------
// Stat — 큰 숫자 + 라벨
// --------------------------------------------------------------------
export function Stat({
  label,
  value,
  delta,
  align = 'left',
}: {
  label: ReactNode;
  value: ReactNode;
  delta?: ReactNode;
  align?: 'left' | 'right';
}) {
  return (
    <div className={`lm-stat lm-stat--${align}`}>
      <div className="lm-stat__label">{label}</div>
      <div className="lm-stat__value">{value}</div>
      {delta && <div className="lm-stat__delta">{delta}</div>}
    </div>
  );
}

// --------------------------------------------------------------------
// HighlightText — 마커펜 강조
// --------------------------------------------------------------------
export function HL({ children, tone = 'yellow' }: { children: ReactNode; tone?: string }) {
  return <em className={`lm-hl lm-hl--${tone}`}>{children}</em>;
}
