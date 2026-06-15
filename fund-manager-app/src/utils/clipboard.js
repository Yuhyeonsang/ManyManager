// 클립보드 복사 + Claude 웹버전에 바로 붙여넣을 텍스트 포맷팅

import * as Clipboard from 'expo-clipboard';

/**
 * 리포트 객체를 Claude 웹에 붙여넣기 좋은 형태의 프롬프트 텍스트로 변환
 * ETF와 일반 주식을 구분해서 포맷팅
 */
export function formatReportForClipboard(report) {
  if (!report) return '';

  const etf = report.etf_info;
  const isETF = !!etf;

  if (isETF) {
    return formatETFForClipboard(report, etf);
  }

  const f = report.financials || {};
  const lines = [
    '아래는 한국 주식 종목에 대한 자동 수집 리포트입니다.',
    '이 데이터를 바탕으로 투자자 관점에서 분석해 주세요.',
    '(매수/관망/매도 의견, 핵심 리스크, 추가로 확인할 지표)',
    '',
    `■ 종목명: ${report.name} (${report.ticker})`,
    `■ AI 자동 등급: ${report.grade}  (점수 ${report.score ?? '-'}/100)`,
    `■ 데이터 기준 시각: ${report.updated_at ?? '알 수 없음'}`,
    '',
    '── 주요 재무 지표 ──',
    `PER: ${f.per ?? '-'}`,
    `PBR: ${f.pbr ?? '-'}`,
    `ROE: ${f.roe ?? '-'}%`,
    `매출 성장률: ${f.revenue_growth ?? '-'}%`,
    `영업이익률: ${f.operating_margin ?? '-'}%`,
    `부채비율: ${f.debt_ratio ?? '-'}%`,
    '',
    '── AI 뉴스 요약 ──',
    report.news_summary ?? '(요약 없음)',
    '',
    '위 정보를 바탕으로:',
    '1) 단기(1개월) / 중기(6개월) 관점의 의견을 각각 알려주세요.',
    '2) 위 데이터에서 가장 위험해 보이는 신호 3가지를 짚어주세요.',
    '3) 투자 판단을 위해 추가로 확인하면 좋을 지표/뉴스를 알려주세요.',
  ];

  return lines.join('\n');
}

function formatETFForClipboard(report, etf) {
  const pct = (v) => v != null ? `${v > 0 ? '+' : ''}${v}%` : 'N/A';
  const num = (v) => v != null ? v.toLocaleString() : 'N/A';

  const lines = [
    `당신은 ETF 투자 전문가입니다. 아래 ETF 리포트를 분석해 초보 투자자도 이해할 수 있게 설명해주세요.`,
    `⚠️ 리포트에 없는 구성종목·수치는 절대 추측하지 마세요. 없으면 "데이터 없음"으로 표시하세요.`,
    '',
    `════════════════════════════════════════`,
    `📊 ETF 분석 리포트 | ${report.name} (${report.ticker})`,
    `💧 물타기 점수: ${etf.water_score ?? '-'}/100  |  🔥 불타기 점수: ${etf.fire_score ?? '-'}/100  |  기준: ${report.updated_at ?? ''}`,
    `════════════════════════════════════════`,
    '',
    '[1] ETF 기본 정보',
    etf.fund_family    ? `  - 운용사: ${etf.fund_family}` : null,
    etf.benchmark_index ? `  - 기초지수: ${etf.benchmark_index}` : null,
    etf.constituents?.length ? `  - 주요 구성종목: ${etf.constituents.join(', ')}` : '  - 구성종목: 데이터 없음',
    etf.total_assets_billion != null ? `  - 총운용자산(AUM): ${num(etf.total_assets_billion)}억원` : null,
    etf.expense_ratio_pct != null ? `  - 운용보수: ${etf.expense_ratio_pct}%` : null,
    etf.nav != null ? `  - NAV(순자산가치): ${num(etf.nav)}원` : null,
    etf.nav_diff_pct != null ? `  - 괴리율(NAV대비): ${etf.nav_diff_pct > 0 ? '+' : ''}${etf.nav_diff_pct}%` : null,
    '',
    '[2] 수익률 성과',
    `  - 1개월: ${pct(etf.return_1m)}`,
    `  - 3개월: ${pct(etf.return_3m)}`,
    `  - 6개월: ${pct(etf.return_6m)}`,
    `  - 1년:   ${pct(etf.return_1y)}`,
    '',
    '[3] 시세 & 기술적 분석',
    (etf.price_52w_high && etf.price_52w_low)
      ? `  - 52주 최고: ${num(etf.price_52w_high)}원 / 최저: ${num(etf.price_52w_low)}원` : null,
    etf.daily_volume != null ? `  - 거래량(당일): ${num(etf.daily_volume)}주` : null,
    etf.avg_volume_20d != null ? `  - 20일 평균거래량: ${Math.round(etf.avg_volume_20d).toLocaleString()}주` : null,
    etf.change_pct != null ? `  - 당일 등락률: ${pct(etf.change_pct)}` : null,
    etf.market_cap_billion != null ? `  - 시가총액: ${num(etf.market_cap_billion)}억원` : null,
    '',
    '[3-1] 매수 타이밍 기술적 분석',
    etf.position_52w_pct != null
      ? `  - 52주 위치: ${etf.position_52w_pct}% (0%=52주 최저, 100%=52주 최고)`
      : (etf.price_52w_high && etf.price_52w_low ? '  - 52주 고저: 데이터 있음 (위치% 계산 중)' : '  - 52주 위치: 데이터 없음'),
    etf.ma20 != null ? `  - MA20(20일선): ${num(Math.round(etf.ma20))}원` : '  - MA20: 데이터 없음',
    etf.ma60 != null ? `  - MA60(60일선): ${num(Math.round(etf.ma60))}원` : '  - MA60: 데이터 없음',
    etf.momentum_10d_pct != null ? `  - 10일 모멘텀: ${pct(etf.momentum_10d_pct)}` : '  - 10일 모멘텀: 데이터 없음',
    etf.water_score != null
      ? `  - 💧 물타기(저점매수) 종합점수: ${etf.water_score}/100`
      : '  - 💧 물타기 점수: 계산 불가',
    ...(etf.water_reasons?.map(r => `       · ${r}`) ?? []),
    etf.fire_score != null
      ? `  - 🔥 불타기(모멘텀추가) 종합점수: ${etf.fire_score}/100`
      : '  - 🔥 불타기 점수: 계산 불가',
    ...(etf.fire_reasons?.map(r => `       · ${r}`) ?? []),
    '',
    '[4] ETF 자체 뉴스',
    ...(report.news_items?.length
      ? report.news_items.map(n => `  • [${n.impact ?? '중립'}] ${n.title}`)
      : [report.news_summary ?? '(뉴스 없음)']),
    '',
    ...(report.etf_constituent_news_items?.length ? [
      '[4-1] 구성종목 주요 뉴스',
      ...report.etf_constituent_news_items.map(n => `  • [${n.impact ?? '중립'}] ${n.title}`),
      '',
    ] : []),
    `════════════════════════════════════════`,
    `아래 6가지를 순서대로 분석해주세요:`,
    '',
    `【1】 📰 ETF 뉴스 + 구성종목 뉴스 종합 분석`,
    `각 뉴스가 ETF에 미치는 영향 + 🟢호재/🔴악재/🟡중립 판정 + 뉴스 종합 방향성`,
    '',
    `【2】 📦 ETF 핵심 구조`,
    `기초지수 설명 + 구성종목 특성(리포트에 있는 것만) + 집중 위험 + 운용보수 평가`,
    '',
    `【3】 📊 기술적 분석 & 타이밍`,
    `52주 위치 + MA20/MA60 추세 + 모멘텀 + 수익률 평가 + 물타기/불타기 점수 해설 (점수 없으면 생략)`,
    '',
    `【4】 🔭 모멘텀 전망`,
    `단기(1~4주) / 중기(1~3개월) 방향성 + 모멘텀 꺾일 리스크 2가지 + 강화 트리거 2가지`,
    '',
    `【5】 💰 구체적 매수 전략`,
    `물타기/불타기/관망 중 선택 + 1차/2차/3차 분할매수 플랜 + 손절 기준 + 목표 수익률`,
    '',
    `【6】 ⚡ 최종 한 줄 요약`,
    `"지금 [ETF명]은 [물타기/불타기/관망] — [핵심 이유]"`,
  ].filter(l => l !== null);

  return lines.join('\n');
}

/**
 * 텍스트를 시스템 클립보드에 복사
 */
export async function copyToClipboard(text) {
  await Clipboard.setStringAsync(text);
}
