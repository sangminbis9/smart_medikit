import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { riskFor, type Alert, type Dimensions, type Package } from './data';
import { Badge, Icon } from './ui';

type Props = {
  items: Package[];
  alerts: Alert[];
  selected: string | null;
  dimensions: Dimensions | null;
  demo: boolean;
  onSelect: (id: string) => void;
};

// React의 포장 선택을 실제 Three.js 장면과 연결한다.
export default function Cabinet({
  items,
  alerts,
  selected,
  dimensions,
  demo,
  onSelect,
}: Props) {
  const host = useRef<HTMLDivElement>(null);
  const controls = useRef<OrbitControls | null>(null);
  const pin = useRef<THREE.Group | null>(null);
  const meshes = useRef(
    new Map<
      string,
      THREE.Mesh<THREE.BoxGeometry, THREE.MeshStandardMaterial>
    >(),
  );
  const edges = useRef(
    new Map<
      string,
      THREE.LineSegments<THREE.EdgesGeometry, THREE.LineBasicMaterial>
    >(),
  );
  const selection = useRef(onSelect);
  const [failed, setFailed] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  selection.current = onSelect;

  useEffect(
    // 보관함 장면을 만들고 화면 크기·입력·GPU 자원을 함께 관리한다.
    function buildScene() {
      const container = host.current;
      if (!container) return;
      let renderer: THREE.WebGLRenderer;
      try {
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      } catch {
        setFailed(true);
        return;
      }
      setFailed(false);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setClearColor(0xffffff, 0);
      container.appendChild(renderer.domElement);
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 100);
      camera.position.set(3.6, 4, 4.6);
      const orbit = new OrbitControls(camera, renderer.domElement);
      controls.current = orbit;
      orbit.target.set(0, 0, 0);
      orbit.enableDamping = true;
      orbit.enablePan = false;
      orbit.minDistance = 4;
      orbit.maxDistance = 13;
      orbit.minPolarAngle = 0.15;
      orbit.maxPolarAngle = Math.PI / 2.15;
      orbit.update();
      orbit.saveState();
      scene.add(new THREE.AmbientLight(0xffffff, 1.6));
      const light = new THREE.DirectionalLight(0xffffff, 1.5);
      light.position.set(-3, 8, 5);
      scene.add(light);
      // 치수가 없으면 단위 없는 예시 외곽만 그리고 약품 위치를 만들어내지 않는다.
      const size = dimensions || { width: 300, depth: 240, height: 120 };
      const scale = 4 / Math.max(size.width, size.depth, size.height);
      const width = size.width * scale;
      const depth = size.depth * scale;
      const height = Math.min(size.height * scale, 2.6);
      const geometries: THREE.BufferGeometry[] = [];
      const materials: THREE.Material[] = [];

      // 박스의 바닥과 낮은 벽을 같은 재질로 구성한다.
      function addBox(
        w: number,
        h: number,
        d: number,
        x: number,
        y: number,
        z: number,
        color: string,
        opacity = 1,
      ) {
        const geometry = new THREE.BoxGeometry(w, h, d);
        const material = new THREE.MeshStandardMaterial({
          color,
          roughness: 0.92,
          transparent: opacity < 1,
          opacity,
          depthWrite: opacity === 1,
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.set(x, y, z);
        scene.add(mesh);
        geometries.push(geometry);
        materials.push(material);
        return mesh;
      }
      addBox(width, 0.1, depth, 0, -0.06, 0, '#e2ebde');
      const wall = Math.max(0.18, height * 0.25);
      addBox(width, wall, 0.045, 0, wall / 2, -depth / 2, '#c5d9c5', 0.62);
      addBox(0.045, wall, depth, -width / 2, wall / 2, 0, '#d3e1ce', 0.62);
      addBox(width, wall, 0.045, 0, wall / 2, depth / 2, '#a4c3ac', 0.62);
      addBox(0.045, wall, depth, width / 2, wall / 2, 0, '#aac6ae', 0.62);
      const shadowGeometry = new THREE.CircleGeometry(width * 0.49, 64);
      const shadowMaterial = new THREE.MeshBasicMaterial({
        color: '#dfe8df',
        transparent: true,
        opacity: 0.7,
      });
      const shadow = new THREE.Mesh(shadowGeometry, shadowMaterial);
      shadow.rotation.x = -Math.PI / 2;
      shadow.position.y = -0.16;
      shadow.scale.y = depth / width;
      scene.add(shadow);
      geometries.push(shadowGeometry);
      materials.push(shadowMaterial);
      const boxes = meshes.current;
      const outlines = edges.current;
      boxes.clear();
      outlines.clear();
      for (const item of items) {
        if (
          item.state !== 'STORED' ||
          !dimensions ||
          !item.position ||
          !item.size_class
        )
          continue;
        const position = item.position;
        // 보정 범위를 벗어난 좌표는 실제 위치처럼 그리지 않고 목록에만 남긴다.
        if (
          position.x > size.width ||
          position.y > size.depth ||
          position.z_estimated > size.height
        )
          continue;
        const ratio = { SMALL: 0.23, MEDIUM: 0.3, LARGE: 0.4 }[item.size_class];
        const w = width * ratio;
        const d = depth * ratio * 0.95;
        const h = Math.min(height * 0.24, w * 0.23);
        const x = position.x * scale - width / 2;
        const z = position.y * scale - depth / 2;
        const mesh = addBox(
          w,
          h,
          d,
          x,
          position.z_estimated * scale + h / 2 + 0.015,
          z,
          item.base_color,
        );
        mesh.userData.packageId = item.package_id;
        boxes.set(item.package_id, mesh);
        const geometry = new THREE.EdgesGeometry(mesh.geometry);
        const material = new THREE.LineBasicMaterial({ color: '#789582' });
        const outline = new THREE.LineSegments(geometry, material);
        mesh.add(outline);
        outlines.set(item.package_id, outline);
        geometries.push(geometry);
        materials.push(material);
      }
      const marker = new THREE.Group();
      const stemGeometry = new THREE.CylinderGeometry(0.012, 0.012, 0.48, 8);
      const headGeometry = new THREE.SphereGeometry(0.11, 16, 12);
      const markerMaterial = new THREE.MeshBasicMaterial({ color: '#206d5c' });
      const stem = new THREE.Mesh(stemGeometry, markerMaterial);
      const head = new THREE.Mesh(headGeometry, markerMaterial);
      stem.position.y = 0.24;
      head.position.y = 0.53;
      marker.add(stem, head);
      marker.visible = false;
      scene.add(marker);
      pin.current = marker;
      geometries.push(stemGeometry, headGeometry);
      materials.push(markerMaterial);
      const ray = new THREE.Raycaster();
      const pointer = new THREE.Vector2();
      let startX = 0;
      let startY = 0;
      // 회전 드래그와 약품 클릭을 구분한다.
      function pointerDown(event: PointerEvent) {
        startX = event.clientX;
        startY = event.clientY;
      }
      // 클릭한 실제 포장 메시의 ID만 React에 전달한다.
      function pointerUp(event: PointerEvent) {
        if (Math.hypot(event.clientX - startX, event.clientY - startY) > 6)
          return;
        const rect = renderer.domElement.getBoundingClientRect();
        pointer.set(
          ((event.clientX - rect.left) / rect.width) * 2 - 1,
          -((event.clientY - rect.top) / rect.height) * 2 + 1,
        );
        ray.setFromCamera(pointer, camera);
        const hits = ray.intersectObjects([...boxes.values()], false);
        if (hits[0])
          selection.current(hits[0].object.userData.packageId as string);
      }
      // 카드와 별도로 키보드 회전 및 확대를 제공한다.
      function keyboard(event: KeyboardEvent) {
        const offset = camera.position.clone().sub(orbit.target);
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight')
          offset.applyAxisAngle(
            new THREE.Vector3(0, 1, 0),
            event.key === 'ArrowLeft' ? -0.15 : 0.15,
          );
        else if (event.key === '+' || event.key === '=')
          offset.multiplyScalar(0.9);
        else if (event.key === '-') offset.multiplyScalar(1.1);
        else if (event.key === 'Home') {
          event.preventDefault();
          orbit.reset();
          return;
        } else return;
        event.preventDefault();
        offset.clampLength(orbit.minDistance, orbit.maxDistance);
        camera.position.copy(orbit.target).add(offset);
        orbit.update();
      }
      // 반응형 캔버스는 부모의 실제 표시 크기를 따른다.
      function resize() {
        const w = container!.clientWidth;
        const h = container!.clientHeight;
        if (!w || !h) return;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
      }
      // 컨텍스트 손실 시 목록으로 계속 탐색할 수 있게 안내한다.
      function contextLost(event: Event) {
        event.preventDefault();
        setFailed(true);
      }
      const observer = new ResizeObserver(resize);
      observer.observe(container);
      resize();
      renderer.domElement.addEventListener('pointerdown', pointerDown);
      renderer.domElement.addEventListener('pointerup', pointerUp);
      renderer.domElement.addEventListener('webglcontextlost', contextLost);
      container.addEventListener('keydown', keyboard);
      let frame = 0;
      // 가려진 다른 포장을 반투명하게 하고 장면을 갱신한다.
      function render() {
        orbit.update();
        if (!document.hidden) renderer.render(scene, camera);
        frame = requestAnimationFrame(render);
      }
      render();
      // StrictMode 재마운트와 화면 이동에서도 GPU 자원을 남기지 않는다.
      return function dispose() {
        cancelAnimationFrame(frame);
        observer.disconnect();
        orbit.dispose();
        controls.current = null;
        pin.current = null;
        container.removeEventListener('keydown', keyboard);
        renderer.domElement.removeEventListener('pointerdown', pointerDown);
        renderer.domElement.removeEventListener('pointerup', pointerUp);
        renderer.domElement.removeEventListener(
          'webglcontextlost',
          contextLost,
        );
        for (const geometry of geometries) geometry.dispose();
        for (const material of materials) material.dispose();
        renderer.dispose();
        renderer.domElement.remove();
        boxes.clear();
        outlines.clear();
      };
    },
    [items, dimensions, resetKey],
  );

  useEffect(
    // 선택된 약품의 핀과 나머지 약품의 투명도를 갱신한다.
    function highlightSelection() {
      const target = selected ? meshes.current.get(selected) : undefined;
      if (pin.current) {
        pin.current.visible = Boolean(target);
        if (target)
          pin.current.position
            .copy(target.position)
            .add(
              new THREE.Vector3(0, target.geometry.parameters.height / 2, 0),
            );
      }
      for (const [id, mesh] of meshes.current) {
        const risk = riskFor(id, selected, alerts, items);
        const focused = id === selected;
        mesh.material.transparent = Boolean(selected && !focused);
        mesh.material.opacity = selected && !focused ? 0.45 : 1;
        mesh.material.depthWrite = !selected || focused;
        const color =
          risk === 'red'
            ? '#cf343e'
            : risk === 'amber'
              ? '#c37622'
              : focused
                ? '#206d5c'
                : '#789582';
        mesh.material.emissive.set(color);
        mesh.material.emissiveIntensity = focused ? 0.08 : 0;
        edges.current.get(id)?.material.color.set(color);
      }
    },
    [selected, alerts, items, dimensions, resetKey],
  );

  // 사용자가 명시적으로 시점을 초기화한다.
  function reset() {
    controls.current?.reset();
  }
  // WebGL 실패 후 사용자가 다시 렌더링을 시도한다.
  function retry() {
    setResetKey(resetKey + 1);
  }
  // 선택된 포장의 이름은 모바일 3D 아래에도 표시한다.
  const selectedItem = items.find(function selectedPackage(item) {
    return item.package_id === selected;
  });

  return (
    <section className="cabinet" aria-label="3D 보관함">
      <div className="flex items-center justify-between gap-2">
        <h2 className="cabinet-title">3D 보관함</h2>
        <Badge tone="amber">
          {demo ? '시연 위치' : dimensions ? '위치 추정' : '치수 미설정'}
        </Badge>
      </div>
      <div
        className="scene"
        ref={host}
        tabIndex={0}
        role="group"
        aria-label="3D 보관함. 드래그로 회전, 휠 또는 더하기·빼기로 확대, 좌우 화살표로 회전, Home으로 초기화"
      >
        {failed && (
          <div className="scene-fallback">
            <p>이 환경에서는 3D를 표시하지 못했어요.</p>
            <p>약품 목록에서 계속 확인할 수 있어요.</p>
            <button className="text-button" onClick={retry}>
              3D 다시 시도
            </button>
          </div>
        )}
        {!failed && !dimensions && (
          <span className="scene-hint">크기 입력 전 · 예시 비율</span>
        )}
      </div>
      <div className="scene-controls">
        <span className="scene-desktop-help">드래그 회전 / 휠 확대</span>
        <span className="scene-mobile-label">
          {selectedItem?.name || '보관함 카메라 등록 대기'}
        </span>
        <button className="soft-button" onClick={reset} disabled={failed}>
          <Icon name="rotate" />
          시점 초기화
        </button>
      </div>
    </section>
  );
}
