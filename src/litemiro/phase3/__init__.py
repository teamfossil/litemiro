"""Phase 3 — JSONL 산출을 보고서(Markdown) 로 변환.

세 컴포넌트의 직렬 파이프라인:

```
RoundEvent JSONL
   └─ DataAggregator → AggregationResult (카테고리별 통계 dict)
       └─ PatternAnalyzer (Qwen-plus) → PartialInsights (카테고리별 텍스트)
           └─ ReportComposer (Opus, 실패시 Qwen 폴백) → Markdown
```

PDF 변환(`ReportFormatter`) 은 별도 후속 이슈에서 다룬다.
"""

from __future__ import annotations

from litemiro.phase3.data_aggregator import DataAggregator
from litemiro.phase3.models import (
    AggregationResult,
    CategoryInsight,
    PartialInsights,
    ReportConfig,
)
from litemiro.phase3.pattern_analyzer import PatternAnalyzer
from litemiro.phase3.report_composer import ComposedReport, ReportComposer

__all__ = [
    "AggregationResult",
    "CategoryInsight",
    "ComposedReport",
    "DataAggregator",
    "PartialInsights",
    "PatternAnalyzer",
    "ReportComposer",
    "ReportConfig",
]
