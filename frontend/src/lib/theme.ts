// 다크모드 테마 상태. <html data-theme> 속성 + localStorage 로 영속.
// system 은 속성 제거 → CSS @media (prefers-color-scheme) 가 OS 를 따른다.
// FOUC 가드는 index.html <head> 인라인 스크립트가 페인트 전에 적용.

export type ThemeMode = 'light' | 'dark' | 'system';

const KEY = 'litemiro-theme';

export function getTheme(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY);
    if (v === 'dark' || v === 'light') return v;
  } catch {
    /* localStorage 불가 환경 — system 으로 폴백 */
  }
  return 'system';
}

export function setTheme(mode: ThemeMode): void {
  const el = document.documentElement;
  if (mode === 'system') el.removeAttribute('data-theme');
  else el.setAttribute('data-theme', mode);
  try {
    localStorage.setItem(KEY, mode);
  } catch {
    /* 저장 실패는 무시 — 이번 세션엔 적용됨 */
  }
}

// 지금 실제로 보이는 테마(라이트/다크). 토글 아이콘 표시·다음 토글 방향 계산용.
export function resolvedTheme(mode: ThemeMode = getTheme()): 'light' | 'dark' {
  if (mode !== 'system') return mode;
  return typeof window !== 'undefined' &&
    window.matchMedia &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light';
}
