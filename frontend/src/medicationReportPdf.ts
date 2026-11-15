import { jsPDF } from 'jspdf';
import {
  reportCategoryLabels,
  reportDateLabel,
  reportNotice,
  reportTitle,
  type ReportItem,
} from './medicationReport';

// 선택 완료 시점의 보고서를 외부 전송 없이 한국어 이미지 기반 PDF로 내려받는다.
export async function downloadMedicationReport(
  items: ReportItem[],
  range: { start: string; end: string },
): Promise<void> {
  // 웹에서 쓰는 자체 호스팅 한국어 폰트를 기다려 PDF의 글자 깨짐을 막는다.
  const text = [
    reportTitle,
    reportNotice,
    ...items.map(
      // 실제 내보낼 이름과 날짜에 필요한 글리프만 미리 불러온다.
      function content(item) {
        return `${item.name} ${item.duration} ${item.medicines.join(', ')} ${item.dates.join(', ')}`;
      },
    ),
  ].join(' ');
  await Promise.all([
    document.fonts.load('400 26px "Noto Sans KR Variable"', text),
    document.fonts.load('600 44px "Noto Sans KR Variable"', text),
  ]);
  const canvas = document.createElement('canvas');
  canvas.width = 1240;
  canvas.height = 1754;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('PDF_CANVAS_UNAVAILABLE');
  const ctx = context;
  const pdf = new jsPDF({ unit: 'mm', format: 'a4', compress: true });
  pdf.setProperties({ title: reportTitle, creator: 'MEDKIT' });
  const margin = 84;
  const bottom = canvas.height - margin;
  let y = margin;
  let page = 1;

  // 매 페이지를 흰 배경으로 시작하며 같은 크기의 캔버스를 재사용한다.
  function clearPage() {
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    y = margin;
  }
  // 페이지마다 다른 이미지 식별자를 써 이전 페이지 그림이 재사용되지 않게 한다.
  function savePage() {
    ctx.font = '400 20px "Noto Sans KR Variable", sans-serif';
    ctx.fillStyle = '#63736c';
    ctx.fillText(String(page), canvas.width / 2, canvas.height - 42);
    pdf.addImage(canvas, 'PNG', 0, 0, 210, 297, `page-${page}`, 'FAST');
  }
  // 문장을 자르지 않고 다음 페이지로 넘길 공간을 확보한다.
  function ensureSpace(height: number) {
    // 한 페이지보다 큰 카드도 빈 페이지를 만들지 않고 페이지 안에서 이어 쓴다.
    if (y + Math.min(height, bottom - margin) <= bottom) return;
    savePage();
    pdf.addPage();
    page++;
    clearPage();
  }
  // 한국어·긴 약 이름·연속 날짜를 문자 경계에서 줄바꿈해 페이지 안에 표시한다.
  function wrap(text: string, size: number, weight: number): string[] {
    ctx.font = `${weight} ${size}px "Noto Sans KR Variable", sans-serif`;
    const lines: string[] = [];
    let line = '';
    for (const character of text) {
      if (
        character === '\n' ||
        (line &&
          ctx.measureText(line + character).width > canvas.width - margin * 2)
      ) {
        lines.push(line);
        line = character === '\n' ? '' : character;
      } else line += character;
    }
    if (line) lines.push(line);
    return lines;
  }
  // 제목 마지막 줄과 첫 본문 줄이 떨어지지 않게 실제 줄 높이로 페이지를 나눈다.
  function drawLines(
    lines: string[],
    size: number,
    weight: number,
    color: string,
    followingHeight = 0,
  ) {
    const font = `${weight} ${size}px "Noto Sans KR Variable", sans-serif`;
    for (const [index, value] of lines.entries()) {
      ensureSpace(
        size * 1.6 + (index === lines.length - 1 ? followingHeight : 0),
      );
      // 페이지 번호를 그리면서 바뀐 글꼴·색상을 각 본문 줄에 다시 적용한다.
      ctx.font = font;
      ctx.fillStyle = color;
      ctx.fillText(value, margin, y + size);
      y += size * 1.6;
    }
  }

  // 문서 제목·조회 기간·안내문도 본문과 같은 줄바꿈 규칙을 사용한다.
  function write(text: string, size = 26, weight = 400, color = '#163c34') {
    drawLines(wrap(text, size, weight), size, weight, color);
  }

  try {
    clearPage();
    write(reportTitle, 44, 600, '#206d5c');
    y += 18;
    write(`조회 기간 ${range.start} ~ ${range.end}`);
    write(reportNotice, 22, 400, '#63736c');
    for (const kind of ['prescription', 'cabinet'] as const) {
      const entries = items.filter(
        // 화면 보고서와 같은 두 분류와 선택된 내용만 내보낸다.
        function sameKind(item) {
          return item.kind === kind;
        },
      );
      if (!entries.length) continue;
      const cards = entries.map(
        // 실제 글꼴의 줄바꿈 결과로 카드가 차지할 높이를 먼저 구한다.
        function layout(item) {
          const title = `${item.name}${item.duration ? ` · ${item.duration}` : ''}`;
          const body =
            kind === 'prescription'
              ? `포함 약: ${item.medicines.length ? item.medicines.join(', ') : '개별 약 정보 없음'}`
              : `${reportDateLabel}: ${item.dates.join(', ')}`;
          const titleLines = wrap(title, 28, 600);
          const bodyLines = wrap(body, 26, 400);
          return {
            titleLines,
            bodyLines,
            height:
              16 + titleLines.length * 28 * 1.6 + bodyLines.length * 26 * 1.6,
          };
        },
      );
      const headingLines = wrap(reportCategoryLabels[kind], 32, 600);
      // 분류 제목과 첫 카드를 함께 넘겨 제목만 이전 페이지에 남지 않게 한다.
      ensureSpace(28 + headingLines.length * 32 * 1.6 + cards[0].height);
      y += 28;
      drawLines(headingLines, 32, 600, '#206d5c');
      for (const [index, card] of cards.entries()) {
        if (index > 0) ensureSpace(card.height);
        y += 16;
        drawLines(card.titleLines, 28, 600, '#163c34', 26 * 1.6);
        drawLines(card.bodyLines, 26, 400, '#63736c');
      }
    }
    savePage();
    await pdf.save(`${reportTitle}.pdf`, { returnPromise: true });
  } finally {
    // 원본 약품 내용을 담은 이미지 버퍼는 다운로드 후 메모리에서 해제한다.
    canvas.width = 0;
    canvas.height = 0;
  }
}
