import { describe, expect, it } from 'vitest';
import {
  parseProductCandidates,
  parseProductDetail,
  parseProductSuggestion,
} from './medicationProducts';

describe('식약처 제품 응답', function medicationProducts() {
  // 누락된 효능과 성분은 만들어 내지 않고 확인 필요 상태로 남긴다.
  it('부분 제품 정보를 보존한다', function partialProduct() {
    const result = parseProductDetail({
      productCode: '000000001',
      productName: '합성 제품',
      manufacturer: null,
      ingredients: [],
      indications: null,
      dosage: null,
      precautions: null,
      source: 'MFDS_PERMIT_DETAIL',
      checkedAt: '2026-11-10T00:00:00Z',
    });
    expect(result.ingredients).toEqual([]);
    expect(result.indications).toBe('');
    expect(result.precautions).toBe('');
  });

  // 품목코드나 공식 출처가 불명확한 응답은 연결하지 않는다.
  it('유효하지 않은 품목과 출처를 거부한다', function invalidProduct() {
    expect(function badCode() {
      parseProductCandidates({
        candidates: [{ productCode: 'bad', productName: '합성 제품' }],
      });
    }).toThrow();
    expect(function badSource() {
      parseProductDetail({
        productCode: '000000001',
        productName: '합성 제품',
        ingredients: [],
        source: 'UNKNOWN',
        checkedAt: '2026-11-10T00:00:00Z',
      });
    }).toThrow();
  });

  // 유사 후보를 확정 품목과 혼동하거나 잘못된 성분 상태로 표시하지 않는다.
  it('유사 후보와 성분 충돌 상태를 검증한다', function possibleProduct() {
    const candidate = {
      productCode: '000000001',
      productName: '합성 후보',
      ingredients: [],
      source: 'MFDS_PERMIT_DETAIL',
      checkedAt: '2026-11-10T00:00:00Z',
      matchStatus: 'POSSIBLE',
      ingredientsStatus: 'CONFLICT',
    };
    expect(parseProductSuggestion(candidate).ingredientsStatus).toBe(
      'CONFLICT',
    );
    const ingredient = {
      name: '합성 확인 성분',
      amount: null,
      unit: null,
      basis: null,
    };
    expect(
      parseProductSuggestion({ ...candidate, ingredients: [ingredient] })
        .ingredients,
    ).toEqual([ingredient]);
    // 수치가 들어온 충돌 응답은 성분명과 함께 표시하지 않는다.
    expect(function conflictAmount() {
      parseProductSuggestion({
        ...candidate,
        ingredients: [{ ...ingredient, amount: '123' }],
      });
    }).toThrow();
    // 성분이 없다는 상태에 이름이 섞인 모순 응답을 거부한다.
    expect(function noDataWithName() {
      parseProductSuggestion({
        ...candidate,
        ingredientsStatus: 'NO_DATA',
        ingredients: [ingredient],
      });
    }).toThrow();
    expect(function badMatch() {
      parseProductSuggestion({ ...candidate, matchStatus: 'MATCHED' });
    }).toThrow();
    expect(function badIngredients() {
      parseProductSuggestion({ ...candidate, ingredientsStatus: 'UNKNOWN' });
    }).toThrow();
  });

  // 원문은 해당 후보의 확인된 동일 성분에만 보존하고 다른 상태는 거절한다.
  it('성분정보 원문의 기준과 대상을 확인한다', function ingredientStatement() {
    const candidate = {
      productCode: '000000001',
      productName: '합성 후보',
      ingredients: [
        { name: '합성 성분', amount: null, unit: null, basis: null },
      ],
      source: 'MFDS_PERMIT_DETAIL',
      checkedAt: '2026-11-10T00:00:00Z',
      matchStatus: 'POSSIBLE',
      ingredientsStatus: 'CONFLICT',
    };
    const statement = {
      name: '합성 성분',
      basis: '이 약 40mL 중',
      componentInfo: '합성 유효성분으로서 12 그램',
    };
    expect(parseProductSuggestion(candidate).ingredientStatements).toEqual([]);
    expect(
      parseProductSuggestion({
        ...candidate,
        ingredientStatements: [statement],
      }).ingredientStatements,
    ).toEqual([statement]);
    expect(function unmatchedIngredient() {
      parseProductSuggestion({
        ...candidate,
        ingredientStatements: [{ ...statement, name: '다른 합성 성분' }],
      });
    }).toThrow();
    expect(function duplicateStatement() {
      parseProductSuggestion({
        ...candidate,
        ingredientStatements: [statement, statement],
      });
    }).toThrow();
    expect(function wrongStatus() {
      parseProductSuggestion({
        ...candidate,
        ingredientsStatus: 'SUCCEEDED',
        ingredientStatements: [statement],
      });
    }).toThrow();
    expect(function incompleteStatement() {
      parseProductSuggestion({
        ...candidate,
        ingredientStatements: [{ ...statement, basis: ' ' }],
      });
    }).toThrow();
    expect(function excessiveStatement() {
      parseProductSuggestion({
        ...candidate,
        ingredientStatements: [
          { ...statement, componentInfo: '가'.repeat(2001) },
        ],
      });
    }).toThrow();
  });
});
