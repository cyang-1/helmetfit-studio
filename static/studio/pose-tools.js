import * as THREE from 'three';
import { TransformControls } from './vendor/controls/TransformControls.js';
import { centerOfModel, poseFromHandle, placeModel } from './pose-math.js';

export function createPoseTools({scene, camera, canvas, orbit, render, getMesh, getPose, onPose}) {
  const handle = new THREE.Object3D();
  const gizmo = new TransformControls(camera, canvas);
  let tool = 'orbit';
  let start = null;
  let changed = false;
  scene.add(handle, gizmo.getHelper());
  gizmo.setSize(.94);
  gizmo.enabled = false;

  function sync() {
    if (gizmo.dragging) return;
    const mesh = getMesh();
    if (!mesh || tool === 'orbit') {
      if (gizmo.object) gizmo.detach();
      gizmo.enabled = false;
      return;
    }
    handle.position.copy(centerOfModel(mesh));
    handle.quaternion.copy(mesh.quaternion);
    handle.scale.setScalar(mesh.scale.x);
    handle.updateMatrixWorld(true);
    gizmo.enabled = true;
    if (gizmo.object !== handle) gizmo.attach(handle);
    render();
  }

  gizmo.addEventListener('mouseDown', () => {
    const mesh = getMesh();
    if (!mesh) return;
    start = { local: mesh.worldToLocal(handle.position.clone()), pose: { ...getPose() } };
    changed = false;
  });
  gizmo.addEventListener('objectChange', () => {
    const mesh = getMesh();
    if (!mesh || !start) return;
    const next = poseFromHandle(mesh, handle, start.local, getPose(), tool, gizmo.axis);
    handle.scale.setScalar(next.scale);
    placeModel(mesh, next);
    changed = true;
    onPose(next, false);
    render();
  });
  gizmo.addEventListener('dragging-changed', ({ value }) => {
    orbit.enabled = !value;
    if (!value) {
      const publish = changed && start;
      start = null;
      changed = false;
      if (publish) onPose(getPose(), true);
      sync();
    }
  });
  gizmo.addEventListener('change', render);
  canvas.addEventListener('pointercancel', () => {
    if (gizmo.dragging) gizmo.pointerUp(null);
  });
  return {
    sync,
    get dragging() { return gizmo.dragging; },
    setTool(next) {
      if (next !== tool) {
        if (gizmo.dragging) gizmo.pointerUp(null);
        tool = next;
        if (tool !== 'orbit') {
          gizmo.setMode(tool);
          gizmo.setSpace('world');
          // Every scale grip changes all three dimensions, with one uniform scale.
          gizmo.showXY = gizmo.showXZ = gizmo.showYZ = tool !== 'scale';
          gizmo.showXYZE = false;
        }
      }
      sync();
    }
  };
}
