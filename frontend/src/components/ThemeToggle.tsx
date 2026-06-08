import { useEffect, useState } from 'react';
import { getTheme, resolvedTheme, setTheme, type ThemeMode } from '@/lib/theme';

// 헤더 우측 아이콘 버튼. 클릭하면 라이트 ↔ 다크 (명시 설정). 현재 보이는 테마를
// 아이콘으로 표시. 토큰만 바뀌므로 마크업 영향 없음.
export function ThemeToggle() {
  const [mode, setMode] = useState<ThemeMode>(() => getTheme());
  const resolved = resolvedTheme(mode);

  useEffect(() => {
    setTheme(mode);
  }, [mode]);

  return (
    <button
      type="button"
      className="lm-header__iconbtn"
      aria-label={`테마 전환 (현재 ${resolved === 'dark' ? '다크' : '라이트'})`}
      title={`${resolved === 'dark' ? '라이트' : '다크'} 모드로`}
      onClick={() => setMode(resolved === 'dark' ? 'light' : 'dark')}
    >
      {resolved === 'dark' ? (
        // moon
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path
            d="M13.5 9.2A5.2 5.2 0 0 1 6.8 2.5 5.5 5.5 0 1 0 13.5 9.2Z"
            fill="currentColor"
          />
        </svg>
      ) : (
        // sun
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
          <circle cx="8" cy="8" r="3.1" />
          <path strokeLinecap="round" d="M8 1.4v1.6M8 13v1.6M1.4 8h1.6M13 8h1.6M3.3 3.3l1.1 1.1M11.6 11.6l1.1 1.1M12.7 3.3l-1.1 1.1M4.4 11.6l-1.1 1.1" />
        </svg>
      )}
    </button>
  );
}
