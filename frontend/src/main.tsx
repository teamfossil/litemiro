// =====================================================================
// 라이트미로 — 엔트리 포인트
// 프로토타입의 ReactDOM.createRoot(...).render(<App/>) 를 대체.
// BrowserRouter 로 감싸고 전역 CSS 를 import 한다.
// =====================================================================

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './styles/index.css';

const rootEl = document.getElementById('root');
if (!rootEl) throw new Error('#root 엘리먼트를 찾을 수 없습니다.');

createRoot(rootEl).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>
);
