import { z } from 'zod';

// 식약처에서 확인한 품목코드만 제품 식별에 사용한다.
const productCode = z.string().regex(/^\d{9}$/);
const optionalText = z
  .string()
  .nullable()
  .optional()
  .transform(function value(text) {
    return text ?? '';
  });
const candidateSchema = z.object({
  productCode,
  productName: z.string().min(1),
  manufacturer: optionalText,
});
const linkSchema = z.object({
  planId: z.string().min(1),
  medicineId: z.string().min(1),
  productCode,
  productName: z.string().min(1),
});
const detailSchema = z.object({
  productCode,
  productName: z.string().min(1),
  manufacturer: optionalText,
  ingredients: z
    .array(
      z.object({
        name: z.string(),
        amount: z.unknown().optional(),
        unit: z.unknown().optional(),
        basis: z.unknown().optional(),
      }),
    )
    .default([]),
  indications: optionalText,
  dosage: optionalText,
  precautions: optionalText,
  source: z.literal('MFDS_PERMIT_DETAIL'),
  checkedAt: z.iso.datetime({ offset: true }),
});
const suggestionSchema = detailSchema
  .extend({
    matchStatus: z.literal('POSSIBLE'),
    ingredientsStatus: z.enum(['SUCCEEDED', 'NO_DATA', 'CONFLICT']),
    ingredientStatements: z
      .array(
        z
          .object({
            name: z.string().trim().min(1).max(2000),
            basis: z.string().trim().min(1).max(2000),
            componentInfo: z.string().trim().min(1).max(2000),
          })
          .strict(),
      )
      .max(100)
      .default([]),
  })
  // 수치 충돌 상태에서는 확인되지 않은 함량을 후보로 받아들이지 않는다.
  .superRefine(function validateConflict(product, context) {
    if (
      product.ingredientsStatus === 'CONFLICT' &&
      product.ingredients.some(
        // 충돌 상태에 성분량·단위·기준이 들어왔는지 검사한다.
        function hasAmount(item) {
          return item.amount != null || item.unit != null || item.basis != null;
        },
      )
    )
      context.addIssue({
        code: 'custom',
        message: '충돌한 성분의 함량을 확인할 수 없습니다.',
      });
    if (product.ingredientsStatus === 'NO_DATA' && product.ingredients.length)
      context.addIssue({
        code: 'custom',
        message: '성분 없음 상태와 제공된 성분이 일치하지 않습니다.',
      });
    // 원문 성분정보는 같은 후보의 확인된 주성분에만 붙인다.
    const keys = new Set<string>();
    for (const statement of product.ingredientStatements) {
      const key = statement.name
        .normalize('NFKC')
        .replace(/\s/g, '')
        .toLowerCase();
      const matches = product.ingredients.filter(function sameIngredient(item) {
        return (
          item.name.normalize('NFKC').replace(/\s/g, '').toLowerCase() === key
        );
      });
      if (
        product.ingredientsStatus !== 'CONFLICT' ||
        matches.length !== 1 ||
        keys.has(key)
      )
        context.addIssue({
          code: 'custom',
          message: '성분정보 원문과 확인된 성분이 일치하지 않습니다.',
        });
      keys.add(key);
    }
  });

export type ProductCandidate = z.infer<typeof candidateSchema>;
export type ProductLink = z.infer<typeof linkSchema>;
export type ProductDetail = z.infer<typeof detailSchema>;
export type ProductSuggestion = z.infer<typeof suggestionSchema>;
export type MedicationIdentity = { name: string; strength: string };
export type MedicationProducts = {
  available: boolean;
  search: (name: string) => Promise<ProductCandidate[]>;
  links: () => Promise<ProductLink[]>;
  detail: (code: string) => Promise<ProductDetail>;
  isCurrent: (
    planId: string,
    medicineId: string,
    expected: MedicationIdentity,
  ) => boolean;
  link: (
    planId: string,
    medicineId: string,
    code: string,
    expected: MedicationIdentity,
  ) => Promise<ProductLink>;
  autoLink: (
    planId: string,
    medicineId: string,
    expected: MedicationIdentity,
  ) => Promise<ProductLink>;
  suggestion: (
    planId: string,
    medicineId: string,
    expected: MedicationIdentity,
  ) => Promise<ProductSuggestion>;
};

// 제품 검색 응답을 검증해 신뢰할 수 없는 후보는 표시하지 않는다.
export function parseProductCandidates(value: unknown): ProductCandidate[] {
  return z.object({ candidates: z.array(candidateSchema) }).parse(value)
    .candidates;
}

// 기존 복용약과 명시적으로 연결된 품목만 읽는다.
export function parseProductLinks(value: unknown): ProductLink[] {
  return z.object({ links: z.array(linkSchema) }).parse(value).links;
}

// 연결 성공 응답의 대상과 품목을 검증한다.
export function parseProductLink(value: unknown): ProductLink {
  return linkSchema.parse(value);
}

// 식약처 상세 응답의 제품과 표시 필드를 검증한다.
export function parseProductDetail(value: unknown): ProductDetail {
  return detailSchema.parse(value);
}

// 확정 연결과 구분되는 유사 제품의 상태·출처를 검증한다.
export function parseProductSuggestion(value: unknown): ProductSuggestion {
  return suggestionSchema.parse(value);
}
