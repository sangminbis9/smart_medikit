import { describe, expect, it } from 'vitest';
import {
  emptyData,
  expiryLabel,
  findPackages,
  hasStock,
  packageSchema,
  parseData,
  riskFor,
  stockLabel,
  dayKey,
} from './data';
import { makeDemo } from './demo';
import { readSettings } from './settings';

// 빈 입력과 불확실한 의료정보의 표시 경계를 검증한다.
describe('표시 정책', function policies() {
  // 서버가 없어도 가상의 수량과 날짜가 생기지 않아야 한다.
  it('빈 데이터는 포장·일정·알림 없이 시작한다', function empty() {
    expect(emptyData()).toMatchObject({
      packages: [],
      doses: [],
      alerts: [],
      box: { dimensions: null },
      synced_at: null,
    });
  });
  // 잘못된 재고 플래그가 있더라도 비정제에는 잔여량을 표시하지 않는다.
  it('시럽·제형 미확인에는 숫자나 퍼센트를 표시하지 않는다', function nonTablets() {
    const item = makeDemo().packages[0];
    item.medicines[0].form = 'OTHER';
    expect(hasStock(item)).toBe(false);
    expect(stockLabel(item)).toBeNull();
    item.medicines[0].form = 'UNKNOWN';
    expect(stockLabel(item)).toBeNull();
  });
  // 미확인 수량과 숫자 0은 서로 다른 상태다.
  it('미확인 수량과 소진 수량을 구분한다', function zeroQuantity() {
    const item = makeDemo().packages[0];
    item.inventory!.estimated_qty = null;
    expect(stockLabel(item)).toContain('미확인');
    item.inventory!.estimated_qty = 0;
    expect(stockLabel(item)).toBe('예상 잔여 0정');
  });
  // 처방약은 구성 약품 수와 무관하게 봉투 회분으로 표시한다.
  it('처방약은 정수가 아닌 회분을 표시한다', function prescription() {
    expect(stockLabel(makeDemo().packages[2])).toBe('예상 잔여 6회분');
  });
  // 임시 기준일을 실제 사용 가능 기한으로 바꿔 표시하지 않는다.
  it('임시 관리 날짜는 실제 유효기간으로 표시하지 않는다', function temporaryExpiry() {
    const item = makeDemo().packages[1];
    item.expiry = {
      actual_expiry: '2027-12-01',
      verified: false,
      temporary_management_date: '2027-01-01',
    };
    expect(expiryLabel(item)).toBe('임시 기준 2027.01.01');
    item.expiry.temporary_management_date = null;
    expect(expiryLabel(item)).toBe('유효기간 미확인');
  });
  // 날짜 경계를 기기의 로컬 시간대 대신 한국 날짜로 계산한다.
  it('자정 경계를 KST로 구분한다', function koreanMidnight() {
    expect(dayKey('2026-10-15T14:59:59Z')).toBe('2026-10-15');
    expect(dayKey('2026-10-15T15:00:00Z')).toBe('2026-10-16');
  });
  // 등록된 제품 정보만 검색하고 없는 증상의 약품을 만들어내지 않는다.
  it('효능 검색과 빈 검색 결과를 지원한다', function search() {
    expect(findPackages(makeDemo().packages, '두통')[0].package_id).toBe(
      'demo-p01',
    );
    expect(findPackages(makeDemo().packages, '없는 약품')).toEqual([]);
  });
  // 보관 검사와 반출 이력 경고를 같은 3D 관계로 취급하지 않는다.
  it('3D는 현재 보관 관계만 강조하고 빨강을 우선한다', function safetyScope() {
    const data = makeDemo();
    const alert = data.alerts[0];
    expect(riskFor('demo-p03', 'demo-p01', [alert], data.packages)).toBe(
      'amber',
    );
    expect(
      riskFor(
        'demo-p03',
        'demo-p01',
        [{ ...alert, scope: 'DAILY_WITHDRAWAL' }],
        data.packages,
      ),
    ).toBeNull();
    expect(
      riskFor(
        'demo-p03',
        'demo-p01',
        [alert, { ...alert, type: 'DUR_CONTRAINDICATION' }],
        data.packages,
      ),
    ).toBe('red');
    data.packages[2].state = 'OUT';
    expect(riskFor('demo-p03', 'demo-p01', [alert], data.packages)).toBeNull();
  });
});

// API 응답과 공개 설정의 오류가 빈 성공 화면으로 바뀌지 않도록 검증한다.
describe('입력 경계', function inputs() {
  // 선택 필드가 없는 포장도 미확인 상태로 조회할 수 있어야 한다.
  it('최소 포장 데이터가 있어도 실행된다', function partialPackage() {
    const item = packageSchema.parse({
      package_id: 'p1',
      box_id: 'b1',
      kind: 'OTC',
      state: 'STORED',
    });
    expect(item.position).toBeNull();
    expect(stockLabel(item)).toBeNull();
    expect(expiryLabel(item)).toBe('유효기간 미확인');
  });
  // 스키마 오류·중복 포장·다른 보관함 응답은 명시적 오류로 거부한다.
  it('잘못된 응답을 안전한 빈 목록으로 오해하지 않는다', function malformed() {
    // 배열이 아닌 포장 응답은 처리하지 않는다.
    expect(function invalid() {
      parseData({ packages: 'invalid' }, {}, 'box');
    }).toThrow();
    const data = makeDemo();
    // 요청한 보관함과 다른 응답을 거부한다.
    expect(function wrongBox() {
      parseData(data, { alerts: [] }, 'other');
    }).toThrow();
    data.packages.push(data.packages[0]);
    // 같은 포장 ID를 중복으로 수신하면 거부한다.
    expect(function duplicate() {
      parseData(data, { alerts: [] }, 'demo-box');
    }).toThrow();
  });
  // 치수나 주소를 비워 둔 기본 설정은 오류가 아니다.
  it('미설정은 허용하고 부분 치수와 비보안 주소는 거부한다', function settings() {
    expect(readSettings({}).issues).toEqual([]);
    expect(readSettings({ VITE_BOX_WIDTH: '300' }).issues).toHaveLength(1);
    expect(
      readSettings({ VITE_API_URL: 'http://example.com', VITE_BOX_ID: 'b1' })
        .issues,
    ).toHaveLength(1);
    expect(
      readSettings({
        VITE_API_URL: 'https://api.example.com',
        VITE_BOX_ID: 'b1',
        VITE_BOX_WIDTH: '300',
        VITE_BOX_DEPTH: '240',
        VITE_BOX_HEIGHT: '120',
      }).issues,
    ).toEqual([]);
  });
});
