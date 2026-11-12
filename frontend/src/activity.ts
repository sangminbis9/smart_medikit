import { z } from 'zod';
import { captureReviewSchema } from './CaptureReviews';

export const eventId = z.string().regex(/^[A-Za-z0-9_-]{1,128}$/);
export const eventSchema = z.object({
  eventId,
  packageId: z.string().min(1).max(128),
  medicineName: z.string().trim().min(1).max(200),
  occurredAt: z.iso.datetime({ offset: true }),
  eventType: z.literal('OUT').optional(),
  movementCertainty: z.enum(['CONFIRMED', 'ESTIMATED']).optional(),
  status: z.enum(['PENDING', 'TAKEN', 'NOT_TAKEN']),
  quantity: z.number().int().positive().nullable(),
  unit: z.enum(['TABLET', 'CAPSULE', 'OTHER']),
  recordedAt: z.iso.datetime({ offset: true }).nullable(),
});
export const activitySchema = z
  .object({
    pending: z.array(eventSchema).max(1000),
    history: z.array(eventSchema).max(1000),
    reviews: z.array(captureReviewSchema).default([]),
  })
  .superRefine(function validateActivity(data, context) {
    const ids = new Set<string>();
    for (const [group, events] of [
      ['pending', data.pending],
      ['history', data.history],
    ] as const) {
      for (const item of events) {
        // 상태와 수량이 충돌하는 응답은 빈 기록이나 확인된 사실로 표시하지 않는다.
        if (
          ids.has(item.eventId) ||
          (group === 'pending' &&
            (item.status !== 'PENDING' ||
              item.recordedAt !== null ||
              item.quantity !== null)) ||
          (group === 'history' &&
            (item.status === 'PENDING' || item.recordedAt === null)) ||
          (item.status === 'NOT_TAKEN' && item.quantity !== null) ||
          (item.status === 'TAKEN' &&
            (item.unit === 'OTHER') !== (item.quantity === null))
        ) {
          context.addIssue({
            code: 'custom',
            message: '반출 기록의 상태를 확인할 수 없습니다.',
          });
        }
        ids.add(item.eventId);
      }
    }
  });
export type Activity = z.infer<typeof activitySchema>;
export type OutboundEvent = z.infer<typeof eventSchema>;

// 기록 API 주소는 HTTPS 또는 로컬 개발 주소만 사용한다.
export function activityUrl(): string {
  const value =
    import.meta.env.VITE_ACTIVITY_API_URL?.trim().replace(/\/$/, '') || '';
  if (!value) return '';
  try {
    const parsed = new URL(value);
    if (
      (parsed.protocol !== 'https:' &&
        !(
          parsed.protocol === 'http:' &&
          ['localhost', '127.0.0.1'].includes(parsed.hostname)
        )) ||
      parsed.username ||
      parsed.password ||
      parsed.search ||
      parsed.hash
    )
      return '';
    return value;
  } catch {
    return '';
  }
}
