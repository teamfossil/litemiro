# LiteMiro (라이트미로) — 프론트엔드

여론 시뮬레이션 프로토타입을 **Vite + React + TypeScript + react-router-dom** 구조로 이식한 프로젝트입니다. 디자인은 원본 프로토타입과 1:1로 동일하게 보존되었습니다(재디자인 없음).

## 실행

```bash
npm install
npm run dev      # 개발 서버 (http://localhost:5173)
npm run build    # 타입 체크(tsc -b) + 프로덕션 빌드(vite)
npm run preview  # 빌드 결과 미리보기
```

## 구조

```
src/
  main.tsx              # 엔트리. BrowserRouter + 전역 CSS import
  App.tsx               # <Routes> 라우터 + AppShell
  data/
    types.ts            # 전역 타입 (Role, Anchor, PlazaNode, ReportData ...)
    mock.ts             # 데이터 + 함수 (data.js 이식). 집계 객체 `lm` export
  components/
    atoms.tsx           # AvatarSVG, Pill, Badge, Button, Stat ... (components.jsx)
    chrome.tsx          # BrandMark, AppHeader, AppShell, ScreenHeader (chrome.jsx)
  lib/
    nav.ts              # pathForScreen / useScreenNav — onNavigate 매핑
  screens/
    Landing.tsx         # 랜딩 (screen-landing.jsx)
    Seed.tsx            # 시드 (screen-seed.jsx)
    Casting.tsx         # 캐스팅 (screen-casting.jsx)
    Live.tsx            # 진행 (screen-live.jsx)
    Plaza.tsx           # 종료 광장 — 시그니처 (screen-plaza.jsx)
    Report.tsx          # 결과 리포트 (screen-report.jsx)
  styles/
    index.css           # 나머지 CSS 를 프로토타입 로드 순서대로 @import
    tokens.css, components.css, chrome.css, screen-*.css, stubs.css
```

## 라우트

| 경로 | 화면 |
|------|------|
| `/` | 랜딩 |
| `/seed` | 시드 |
| `/casting/:plazaId` | 캐스팅 |
| `/live/:plazaId` | 진행 |
| `/plaza/:plazaId` | 종료 광장 |
| `/report/:plazaId` | 결과 리포트 |

데모는 단일 광장이므로 `plazaId = 'demo'` 로 고정됩니다.

## 이식 시 변경된 패턴

- `window.LM.*` 전역 객체 → `import { lm } from '@/data/mock'`
- 별칭 훅(`useStateLive`, `useMemoPlaza` …) → 표준 `import { useState } from 'react'`
- `Object.assign(window, {...})` export → ES 모듈 export
- 해시 라우터(`window.location.hash`) → react-router-dom + `useScreenNav()`
- 각 화면의 `onNavigate(id)` prop → `useScreenNav()` 훅
- CSS 는 그대로 복사(디자인 1:1 보존)
