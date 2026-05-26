// =====================================================================
// ApiStatusBadge — 화면 우하단에 백엔드 연결 상태 표시.
// loading: 점 한 칸 / ok: 버전 / down: 빨간 점 + 사유 tooltip.
// 디자인 다듬기 전 디버깅용. 프로덕션에서는 NODE_ENV=production 등으로 가린다.
// =====================================================================

import type { CSSProperties } from 'react';
import { useApiHealth } from './useApiHealth';

const baseStyle: CSSProperties = {
  position: 'fixed',
  right: 12,
  bottom: 12,
  zIndex: 9999,
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  fontSize: 11,
  padding: '4px 8px',
  borderRadius: 6,
  background: 'rgba(0,0,0,0.6)',
  color: '#fff',
  pointerEvents: 'auto',
  userSelect: 'none',
};

export function ApiStatusBadge() {
  const state = useApiHealth();
  if (state.kind === 'loading') {
    return (
      <div style={baseStyle} aria-live="polite">
        api: …
      </div>
    );
  }
  if (state.kind === 'down') {
    return (
      <div
        style={{ ...baseStyle, background: '#7a1f1f' }}
        title={state.reason}
        aria-live="polite"
      >
        api: down
      </div>
    );
  }
  return (
    <div style={{ ...baseStyle, background: '#1f5b2e' }} aria-live="polite">
      api: ok v{state.data.version}
    </div>
  );
}
