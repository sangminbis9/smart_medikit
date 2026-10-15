import { dayKey, packageSchema, type Data } from './data';

// Figma의 가상 포장을 매번 새로 만들어 실제 데이터와 메모리까지 분리한다.
export function makeDemo(): Data {
  const today = dayKey();
  const packages = [
    packageSchema.parse({
      package_id: 'demo-p01',
      box_id: 'demo-box',
      kind: 'OTC',
      state: 'STORED',
      name: '타이레놀정 500mg',
      medicines: [
        {
          medicine_id: 'demo-m01',
          name: '타이레놀정 500mg',
          form: 'TABLET',
          ingredients: ['아세트아미노펜'],
          indications: ['두통'],
        },
      ],
      size_class: 'LARGE',
      base_color: '#a8cab0',
      inventory: { stock_enabled: true, initial_qty: 10, estimated_qty: 8 },
      expiry: { actual_expiry: '2027-06-30', verified: true },
      position: { x: 140, y: 100, z_estimated: 35, certainty: 'ESTIMATED' },
      note: '메모  개봉한 포장을 먼저 확인하세요.',
    }),
    packageSchema.parse({
      package_id: 'demo-p02',
      box_id: 'demo-box',
      kind: 'OTC',
      state: 'STORED',
      name: '일반의약품 B',
      medicines: [
        {
          medicine_id: 'demo-m02',
          name: '일반의약품 B',
          form: 'TABLET',
          indications: ['복통'],
        },
      ],
      size_class: 'MEDIUM',
      base_color: '#e8e4d5',
      inventory: { stock_enabled: true },
      position: { x: 77, y: 160, z_estimated: 0, certainty: 'UNCERTAIN' },
      note: '확인 필요  유효기간을 확인해주세요.\n포장 정보를 아직 확인하지 못했어요.',
    }),
    packageSchema.parse({
      package_id: 'demo-p03',
      box_id: 'demo-box',
      kind: 'PRESCRIPTION',
      state: 'STORED',
      name: '처방 받은 약봉투',
      medicines: [
        { medicine_id: 'demo-m03', name: '시연 약품 C', indications: ['복통'] },
      ],
      size_class: 'SMALL',
      base_color: '#e3d2a0',
      remaining_doses: 6,
      position: { x: 220, y: 140, z_estimated: 0, certainty: 'ESTIMATED' },
      note: '일정 메모  복약 탭에서 일정을 확인하세요.\n반출 기록은 실제 복용과 구분해요.',
    }),
  ];
  return {
    box: {
      box_id: 'demo-box',
      dimensions: { width: 300, depth: 240, height: 120 },
      device_status: 'UNKNOWN',
    },
    packages,
    doses: [
      {
        dose_id: 'demo-d01',
        package_id: 'demo-p03',
        scheduled_at: `${today}T08:00:00+09:00`,
        result: 'CONFIRMED',
      },
      {
        dose_id: 'demo-d02',
        package_id: 'demo-p03',
        scheduled_at: `${today}T13:00:00+09:00`,
        result: 'ESTIMATED',
      },
      {
        dose_id: 'demo-d03',
        package_id: 'demo-p03',
        scheduled_at: `${today}T19:00:00+09:00`,
        result: 'SCHEDULED',
      },
    ],
    alerts: [
      {
        alert_id: 'demo-a01',
        type: 'DUPLICATE_INGREDIENT',
        title: '동일 성분 중복 가능성',
        body: '보관 중인 약품 2개에 같은 성분이 포함된 경우의 예시입니다. 실제 함께 복용했는지는 확인되지 않았어요.',
        scope: 'STORED',
        package_ids: ['demo-p01', 'demo-p03'],
        occurred_at: `${today}T15:02:00+09:00`,
        ack_state: 'PENDING',
        source_event_id: 'demo-e01',
      },
      {
        alert_id: 'demo-a02',
        type: 'REGISTRATION',
        title: '등록 정보를 확인해주세요',
        body: '새 포장의 초기 수량이 추정값이에요. 등록된 정보와 실제 포장 수량을 확인해주세요.',
        scope: null,
        package_ids: ['demo-p01'],
        occurred_at: `${today}T15:02:00+09:00`,
        ack_state: 'PENDING',
        source_event_id: 'demo-e02',
      },
      {
        alert_id: 'demo-a03',
        type: 'EXPIRY',
        title: '유효기간 확인이 필요해요',
        body: '일반의약품 B의 유효기간이 미확인이에요. 포장에 표시된 날짜를 확인해주세요.',
        scope: null,
        package_ids: ['demo-p02'],
        occurred_at: `${today}T09:00:00+09:00`,
        ack_state: 'PENDING',
        source_event_id: 'demo-e03',
      },
    ],
    synced_at: null,
  };
}
