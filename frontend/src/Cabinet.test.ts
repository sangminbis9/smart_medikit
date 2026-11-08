import { describe, expect, it } from 'vitest';
import { cabinetSceneSize } from './Cabinet';

describe('3D 보관함 치수', function cabinetDimensions() {
  // 전달된 세 치수는 화면에서도 동일한 비율을 유지한다.
  it('실제 보관함의 폭·깊이·높이 비율을 유지한다', function boxRatio() {
    const size = cabinetSceneSize({ width: 305, depth: 165, height: 190 });
    expect(size.width).toBeCloseTo(4);
    expect(size.depth / size.width).toBeCloseTo(165 / 305);
    expect(size.height / size.width).toBeCloseTo(190 / 305);
  });

  // 높은 보관함도 높이 상한으로 눌리지 않는다.
  it('높이가 가장 긴 보관함도 비율을 유지한다', function tallBox() {
    const size = cabinetSceneSize({ width: 150, depth: 100, height: 300 });
    expect(size.height).toBeCloseTo(4);
    expect(size.width / size.height).toBeCloseTo(0.5);
    expect(size.depth / size.height).toBeCloseTo(1 / 3);
  });
});
