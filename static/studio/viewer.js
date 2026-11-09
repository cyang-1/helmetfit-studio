import * as THREE from 'three';
import { OrbitControls } from '/static/vendor/OrbitControls.js';
import { STLLoader } from '/static/vendor/STLLoader.js';
import { alignedTranslation, placeModel } from './pose-math.js';
import { createPoseTools } from './pose-tools.js';

const colors = {
  model: 0xd9e1d6,
  original: 0xd9e1d6,
  fitted: 0xc2e9a4,
  ring: 0xbeec87,
  head: 0x6aacde,
  cutter: 0xffad5c
};
const count = (geometry) => geometry.getAttribute('position')?.count || 0;

function materials(mesh) {
  return Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : [];
}
async function originalAppearance(buffer, size) {
  const { GLTFLoader } = await import('./vendor/loaders/GLTFLoader.js');
  const manager = new THREE.LoadingManager();
  manager.setURLModifier((url) => {
    if (/^(blob:|data:)/i.test(url)) return url;
    throw Error('该 GLB 使用外部资源，请导入包含贴图的自包含 GLB。');
  });
  const asset = await new GLTFLoader(manager).parseAsync(buffer, '');
  const group = new THREE.Group();
  group.add(asset.scene);
  // glTF is Y-up. Only the appearance viewport normalizes size for orbit navigation.
  group.rotation.x = Math.PI / 2;
  group.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(group, true);
  if (bounds.isEmpty()) throw Error('原始模型中没有可预览的几何。');
  const span = bounds.getSize(new THREE.Vector3());
  const scale = size?.[2] && span.z > 0 ? size[2] / span.z : 230 / Math.max(span.x, span.y, span.z);
  group.scale.setScalar(scale);
  group.position.set(
    (-scale * (bounds.min.x + bounds.max.x)) / 2,
    (-scale * (bounds.min.y + bounds.max.y)) / 2,
    -scale * bounds.min.z
  );
  return group;
}
function dispose(object) {
  const used = new Set();
  object.traverse((child) => {
    if (child.geometry && !used.has(child.geometry)) {
      used.add(child.geometry);
      child.geometry.dispose();
    }
    for (const material of materials(child)) {
      if (used.has(material)) continue;
      used.add(material);
      for (const value of Object.values(material)) {
        if (value?.isTexture && !used.has(value)) {
          used.add(value);
          value.dispose();
          value.source?.data?.close?.();
        }
      }
      material.dispose();
    }
  });
}

export function createViewer(host, onPose = () => {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.localClippingEnabled = true;
  host.append(renderer.domElement);
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 10000);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera);
  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xfff8e9, 0x697b7e, 2.6));
  const key = new THREE.DirectionalLight(0xf5ffec, 2.2);
  key.position.set(-240, -270, 360);
  const rim = new THREE.DirectionalLight(0xbfe5ff, 1.8);
  rim.position.set(210, 150, 230);
  scene.add(key, rim);
  const grid = new THREE.GridHelper(720, 36, 0x666d70, 0x565b5d);
  grid.material.transparent = true;
  grid.material.opacity = 0.5;
  grid.rotation.x = Math.PI / 2;
  grid.position.z = -0.3;
  scene.add(grid);
  const loader = new STLLoader();
  const entries = new Map();
  const tasks = new Map();
  const sectionPlane = new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0);
  let options = { mode: 'model', head: true, cutter: true, section: false, depth: 50 };
  let pose = {};
  let sourceDimensions = null;
  let retainCompareView = false;
  let inspectionIndex = -1;
  let framePending = false;

  function render() {
    if (framePending) return;
    framePending = true;
    requestAnimationFrame(() => {
      framePending = false;
      renderer.render(scene, camera);
    });
  }
  controls.addEventListener('change', render);
  const poseTools = createPoseTools({
    scene, camera, canvas: renderer.domElement, orbit: controls, render,
    getMesh: () => entries.get('model')?.mesh,
    getPose: () => pose,
    onPose(next, commit) {
      pose = { ...pose, ...next };
      onPose(next, commit);
    }
  });
  // Let the gizmo claim its handles before OrbitControls sees a pointer down.
  controls.connect(renderer.domElement);
  function currentBounds() {
    const box = new THREE.Box3();
    for (const entry of entries.values()) {
      if (entry.mesh.visible) box.union(new THREE.Box3().setFromObject(entry.mesh));
    }
    if (box.isEmpty()) box.set(new THREE.Vector3(-100, -100, 0), new THREE.Vector3(100, 100, 220));
    return box;
  }
  function frame(front = false) {
    const sphere = currentBounds().getBoundingSphere(new THREE.Sphere());
    const vfov = THREE.MathUtils.degToRad(camera.fov / 2);
    const distance =
      (sphere.radius / Math.sin(Math.min(vfov, Math.atan(Math.tan(vfov) * camera.aspect)))) * 1.13;
    const direction = (
      front === 'rear'
        ? new THREE.Vector3(0.8, 1.4, 0.55)
        : front
          ? new THREE.Vector3(0, -1, 0.015)
          : new THREE.Vector3(0.72, -1.55, 0.53)
    ).normalize();
    controls.target.copy(sphere.center);
    camera.position.copy(sphere.center).addScaledVector(direction, distance);
    camera.near = Math.max(0.1, distance / 3000);
    camera.far = Math.max(4000, distance * 8);
    controls.maxDistance = distance * 5;
    camera.updateProjectionMatrix();
    controls.update();
    render();
  }
  function applyVisibility() {
    for (const [name, { mesh }] of entries) {
      const overlay = name === 'head' || name === 'cutter';
      mesh.visible = overlay
        ? (options.mode === 'model' || options.mode === 'fitted') && options[name]
        : options.mode === name;
      mesh.traverse((child) =>
        materials(child).forEach((mat) => {
          mat.clippingPlanes = options.section ? [sectionPlane] : [];
        })
      );
      // Reveal the reference overlay through the outer shell in alignment mode.
      if (name === 'model') {
        mesh.material.transparent = (options.head || options.cutter) && !options.section;
        mesh.material.opacity = mesh.material.transparent ? 0.55 : 1;
        mesh.material.depthWrite = !mesh.material.transparent;
      }
    }
    const bounds = currentBounds();
    sectionPlane.constant = THREE.MathUtils.lerp(bounds.min.x, bounds.max.x, options.depth / 100);
    render();
  }
  function applyPose(params) {
    if (poseTools.dragging) return;
    pose = params;
    const model = entries.get('model')?.mesh;
    if (!model) return;
    placeModel(model, pose);
    poseTools.sync();
    applyVisibility();
  }
  function remove(name) {
    const entry = entries.get(name);
    if (!entry) return;
    scene.remove(entry.mesh);
    dispose(entry.mesh);
    entries.delete(name);
    if (name === 'model') poseTools.sync();
    render();
  }
  async function load(name, url) {
    if (tasks.get(name)?.url === url) return tasks.get(name).promise;
    if (entries.get(name)?.url === url && !tasks.has(name)) return;
    tasks.get(name)?.controller.abort();
    if (!url) {
      tasks.delete(name);
      remove(name);
      return;
    }
    remove(name);
    const controller = new AbortController();
    const task = { url, controller };
    tasks.set(name, task);
    task.promise = (async () => {
      try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) throw Error('预览文件读取失败，请刷新后重试');
        const bytes = await response.arrayBuffer();
        let mesh;
        if (name === 'glb') mesh = await originalAppearance(bytes, sourceDimensions);
        else {
          const geometry = loader.parse(bytes);
          if (!count(geometry)) {
            geometry.dispose();
            throw Error('预览文件没有有效网格');
          }
          geometry.computeBoundingBox();
          const overlay = name === 'head' || name === 'cutter';
          mesh = new THREE.Mesh(
            geometry,
            new THREE.MeshStandardMaterial({
              color: colors[name],
              side: THREE.DoubleSide,
              roughness: 0.65,
              metalness: overlay ? 0 : 0.2,
              transparent: overlay,
              opacity: name === 'cutter' ? 0.27 : name === 'head' ? 0.69 : 1,
              depthWrite: !overlay
            })
          );
        }
        if (tasks.get(name) !== task) {
          dispose(mesh);
          return;
        }
        mesh.renderOrder = name === 'model' ? 1 : name === 'cutter' ? 3 : 0;
        scene.add(mesh);
        entries.set(name, { mesh, url });
        if (name === 'model') applyPose(pose);
        applyVisibility();
        if (name === options.mode) {
          if (!retainCompareView) frame();
          retainCompareView = false;
        }
      } catch (error) {
        if (error.name !== 'AbortError') throw error;
      } finally {
        if (tasks.get(name) === task) tasks.delete(name);
      }
    })();
    return task.promise;
  }
  const observer = new ResizeObserver(() => {
    if (!host.clientWidth || !host.clientHeight) return;
    camera.aspect = host.clientWidth / host.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(host.clientWidth, host.clientHeight, false);
    render();
  });
  observer.observe(host);
  frame();
  return {
    frame,
    load,
    applyPose,
    releasePoseTool() { poseTools.setTool('orbit'); },
    async centerToHead(url) {
      const model = entries.get('model')?.mesh;
      if (!model || !url) throw Error('请等待头盔和当前头模加载完成。');
      const head = entries.get('head');
      let box;
      if (head?.url === url) box = head.mesh.geometry.boundingBox;
      else {
        const response = await fetch(url);
        if (!response.ok) throw Error('当前头模读取失败，请重试。');
        const geometry = loader.parse(await response.arrayBuffer());
        try {
          geometry.computeBoundingBox();
          box = geometry.boundingBox.clone();
        } finally { geometry.dispose(); }
      }
      if (entries.get('model')?.mesh !== model || box.isEmpty()) throw Error('模型已切换，请重新居中。');
      return alignedTranslation(model, box);
    },
    sourceSize(dimensions) {
      sourceDimensions = dimensions;
    },
    focusInspection(points) {
      const locations = points.filter(
        (p) => Array.isArray(p) && p.length === 3 && p.every(Number.isFinite)
      );
      inspectionIndex = (inspectionIndex + 1) % Math.max(1, locations.length);
      const valid = locations[inspectionIndex];
      if (!valid) {
        frame('rear');
        return;
      }
      const at = new THREE.Vector3(...valid);
      const center = currentBounds().getCenter(new THREE.Vector3());
      const normal = at.clone().sub(center);
      if (!normal.lengthSq()) normal.set(0.8, 1.4, 0.55);
      controls.target.copy(at);
      camera.position
        .copy(at)
        .addScaledVector(
          normal.normalize(),
          Math.max(35, currentBounds().getSize(new THREE.Vector3()).length() * 0.28)
        );
      camera.updateProjectionMatrix();
      controls.update();
      render();
      return inspectionIndex + 1;
    },
    settings(next) {
      const switched = options.mode !== next.mode;
      if (switched) {
        const sourceModes = ['model', 'original', 'glb'];
        retainCompareView = Boolean(
          next.compareAligned &&
            entries.has(options.mode) &&
            sourceModes.includes(options.mode) &&
            sourceModes.includes(next.mode)
        );
      }
      options = { ...options, ...next };
      poseTools.setTool(options.mode === 'model' ? (options.poseTool || 'orbit') : 'orbit');
      applyVisibility();
      if (switched && !retainCompareView) frame();
    },
    clear() {
      poseTools.setTool('orbit');
      retainCompareView = false;
      inspectionIndex = -1;
      for (const task of tasks.values()) task.controller.abort();
      tasks.clear();
      for (const name of [...entries.keys()]) remove(name);
    }
  };
}
