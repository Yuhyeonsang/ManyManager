// 클립보드 복사 + Claude 웹버전에 바로 붙여넣을 텍스트 포맷팅

import * as Clipboard from 'expo-clipboard';

/**
 * 리포트 객체를 Claude 웹에 붙여넣기 좋은 형태의 프롬프트 텍스트로 변환
 */
export function formatReportForClipboard(report) {
  if (!report) return '';

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

/**
 * 텍스트를 시스템 클립보드에 복사
 */
export async function copyToClipboard(text) {
  await Clipboard.setStringAsync(text);
}
