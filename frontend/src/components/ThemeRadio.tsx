import { useState } from 'react';
import { getTheme, setTheme, type ThemeMode } from '@/lib/theme';

const OPTS: { id: ThemeMode; label: string }[] = [
  { id: 'system', label: '시스템' },
  { id: 'light', label: '라이트' },
  { id: 'dark', label: '다크' },
];

// 테마 라디오 그룹 — 시스템/라이트/다크. 토큰만 바뀌므로 마크업 영향 없음.
export function ThemeRadio() {
  const [mode, setMode] = useState<ThemeMode>(() => getTheme());

  const choose = (m: ThemeMode) => {
    setMode(m);
    setTheme(m);
  };

  return (
    <div className="lm-theme-radio" role="radiogroup" aria-label="테마">
      {OPTS.map((o) => (
        <button
          key={o.id}
          type="button"
          role="radio"
          aria-checked={mode === o.id}
          className={`lm-theme-radio__opt${mode === o.id ? ' is-active' : ''}`}
          onClick={() => choose(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
