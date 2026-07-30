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
  const p = report.price_info || {};
  const hasPrice = p.current_price != null;

  // 실제 분석(뉴스 해석, 투자판단)은 Claude가 하므로 여기서는 원본 데이터만
  // 고정 포맷으로 즉시(AI 호출 없이) 조립 — 서버 /clipboard 왕복을 기다릴 필요 없음.
  const num = (v) => (v != null ? v.toLocaleString() : '-');
  const pct = (v) => (v != null ? `${v > 0 ? '+' : ''}${v}%` : '-');

  const lines = [
    '당신은 20년 경력의 친절한 주식 투자 선생님입니다.',
    '아래 종목 리포트(시세·이동평균·재무·뉴스·등급)를 근거로 초보 투자자도 이해할 수 있게 깊이 있고 구체적으로 분석해주세요.',
    '',
    '[필수 규칙]',
    '- 어려운 금융 용어는 쉬운 말 + 일상 비유로 풀어서 설명할 것.',
    '- 모든 숫자에 "좋다/보통/나쁘다" 판단과 이유를 붙일 것.',
    '- 리포트에 없는 정보는 추측하지 말고 "데이터 없음"으로 표시할 것.',
    '- 뉴스는 헤드라인만 보지 말고 실제 기사 내용을 찾아 링크와 함께 설명할 것.',
    '',
    `■ 종목명: ${report.name} (${report.ticker})`,
    `■ AI 자동 등급: ${report.grade}  (점수 ${report.score ?? '-'}/100)`,
    `■ 데이터 기준 시각: ${report.updated_at ?? '알 수 없음'}`,
    '',
    '── 시세 & 이동평균 ──',
    ...(hasPrice ? [
      `현재가: ${num(p.current_price)} (${pct(p.change_pct)})`,
      `52주 위치: ${p.position_52w_pct != null ? p.position_52w_pct + '%' : '-'} (0%=최저, 100%=최고)`,
      (p.price_52w_high != null || p.price_52w_low != null)
        ? `52주 최고/최저: ${num(p.price_52w_high)} / ${num(p.price_52w_low)}` : null,
      `이동평균: MA5 ${num(p.ma5)} · MA20 ${num(p.ma20)} · MA60 ${num(p.ma60)} · MA120 ${num(p.ma120)}`,
      p.momentum_10d_pct != null ? `10일 모멘텀: ${pct(p.momentum_10d_pct)}` : null,
      ...(p.signals?.length ? p.signals.map(s => `시그널: ${s}`) : []),
    ].filter(Boolean) : ['데이터 없음']),
    '',
    // 기준 시점(TTM/연간/분기말 등)을 같이 표기 — 다른 사이트와 대조할 때
    // "누가 틀린 게 아니라 기준이 다른 것"인지 바로 판단할 수 있게.
    '── 주요 재무 지표 (괄호=산정 기준) ──',
    `PER: ${f.per ?? '-'}${f.per_basis ? ` (${f.per_basis})` : ''}`,
    `PBR: ${f.pbr ?? '-'}${f.pbr_basis ? ` (${f.pbr_basis})` : ''}`,
    `ROE: ${f.roe ?? '-'}%${f.roe_basis ? ` (${f.roe_basis})` : ''}`,
    `매출 성장률: ${f.revenue_growth ?? '-'}%${f.revenue_growth_basis ? ` (${f.revenue_growth_basis})` : ''}`,
    `영업이익률: ${f.operating_margin ?? '-'}%${f.operating_margin_basis ? ` (${f.operating_margin_basis})` : ''}`,
    `부채비율: ${f.debt_ratio ?? '-'}%${f.debt_ratio_basis ? ` (${f.debt_ratio_basis})` : ''}`,
    '',
    '── 뉴스 ──',
    ...(report.news_items?.length
      ? report.news_items.flatMap(n => [
          `• [${n.impact ?? '중립'}] ${n.title}`,
          n.link ? `  링크: ${n.link}` : null,
        ].filter(Boolean))
      : [report.news_summary?.trim() || '(뉴스 없음)']),
    '',
    '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
    '위 데이터를 바탕으로 아래 순서대로 작성해주세요:',
    '',
    '【1】 📰 뉴스 분석 — 각 기사 실제 내용 요약 + 호재/악재/중립 판정 + 단기(1~2주)/중기(1~3개월) 영향',
    '【2】 📈 기술적 분석 — 현재가 vs MA5/20/60/120 관계로 정배열/역배열(상승/하락추세) 판단, 52주 위치로 고점/저점 근처인지 판단',
    '【3】 📊 재무제표 쉬운 해설 — PER/PBR/ROE/영업이익률/매출성장률/부채비율 각각 "무슨 뜻 → 이 종목 수치 → 좋은지 나쁜지" 3단계로',
    '【4】 🎯 기간별 투자의견 — 단기(1개월)/중기(3~6개월)/장기(1년+) 각각 매수·관망·매도 + 근거',
    '【5】 ⚠️ 핵심 리스크 Top 3',
    '【6】 🔍 추가로 확인하면 좋을 지표/뉴스',
    '【7】 ⚡ 최종 한 줄 결론',
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
      ? report.news_items.flatMap(n => [
          `  • [${n.impact ?? '중립'}] ${n.title}`,
          n.link ? `    링크: ${n.link}` : null,
        ].filter(Boolean))
      : [report.news_summary ?? '(뉴스 없음)']),
    '',
    ...(report.etf_constituent_news_items?.length ? [
      '[4-1] 구성종목 주요 뉴스',
      ...report.etf_constituent_news_items.flatMap(n => [
        `  • [${n.impact ?? '중립'}] ${n.title}`,
        n.link ? `    링크: ${n.link}` : null,
      ].filter(Boolean)),
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
