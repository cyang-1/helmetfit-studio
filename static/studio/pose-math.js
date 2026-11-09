import * as THREE from 'three';

const radians = THREE.MathUtils.degToRad;
const degrees = THREE.MathUtils.radToDeg;
const clamp = THREE.MathUtils.clamp;
const cache = new WeakMap();
const turn = (angle) => ((angle + 180) % 360 + 360) % 360 - 180;
const rounded = (n) => Math.round(n * 1e6) / 1e6;

export function rotationFor(pose) {
  return new THREE.Quaternion().setFromEuler(
    new THREE.Euler(radians(pose.rx || 0), radians(pose.ry || 0), radians(pose.rz || 0), 'ZYX')
  );
}

export function anglesFor(q, previous) {
  // Blender XYZ applies X, then Y, then Z. Retain the closest Euler branch
  // around +/-90°, where the same physical rotation has multiple representations.
  const m = new THREE.Matrix4().makeRotationFromQuaternion(q).elements;
  const cosY = Math.hypot(m[0], m[1]);
  const y = degrees(Math.atan2(-m[2], cosY));
  if (cosY < 1e-10) {
    const x = previous.rx || 0;
    return { rx: x, ry: y, rz: turn(degrees(Math.atan2(-m[4], m[5])) + Math.sign(y) * x) };
  }
  const x = degrees(Math.atan2(m[6], m[10]));
  const z = degrees(Math.atan2(m[1], m[0]));
  const candidates = [[x, y, z], [turn(x + 180), turn(180 - y), turn(z + 180)]];
  const previousAngles = [previous.rx || 0, previous.ry || 0, previous.rz || 0];
  const distance = (values) => values.reduce((sum, v, i) => sum + turn(v - previousAngles[i]) ** 2, 0);
  const [rx, ry, rz] = distance(candidates[1]) < distance(candidates[0]) ? candidates[1] : candidates[0];
  return { rx, ry, rz };
}

function rotatedBounds(geometry, q) {
  const last = cache.get(geometry);
  if (last && last.q.equals(q)) return last.box;
  const a = geometry.getAttribute('position');
  const m = new THREE.Matrix4().makeRotationFromQuaternion(q).elements;
  let minX = Infinity, minY = Infinity, minZ = Infinity, maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  for (let i = 0; i < a.count; i++) {
    const x = a.getX(i), y = a.getY(i), z = a.getZ(i);
    const px = m[0]*x + m[4]*y + m[8]*z;
    const py = m[1]*x + m[5]*y + m[9]*z;
    const pz = m[2]*x + m[6]*y + m[10]*z;
    minX = Math.min(minX, px); maxX = Math.max(maxX, px);
    minY = Math.min(minY, py); maxY = Math.max(maxY, py);
    minZ = Math.min(minZ, pz); maxZ = Math.max(maxZ, pz);
  }
  const box = new THREE.Box3(new THREE.Vector3(minX,minY,minZ), new THREE.Vector3(maxX,maxY,maxZ));
  cache.set(geometry, { q:q.clone(), box });
  return box;
}

export function placeModel(mesh, pose) {
  const q = rotationFor(pose);
  const b = rotatedBounds(mesh.geometry, q);
  const s = pose.scale || 1;
  mesh.quaternion.copy(q);
  mesh.scale.setScalar(s);
  mesh.position.set(
    (pose.dx || 0) - s*(b.min.x + b.max.x)/2,
    (pose.dy || 0) - s*(b.min.y + b.max.y)/2,
    (pose.dz || 0) - s*b.min.z
  );
  mesh.updateMatrixWorld(true);
}

export function centerOfModel(mesh) {
  return rotatedBounds(mesh.geometry, mesh.quaternion)
    .getCenter(new THREE.Vector3()).multiplyScalar(mesh.scale.x).add(mesh.position);
}

export function poseFromHandle(mesh, handle, localPivot, previous, mode, axis) {
  const channel = axis === 'Y' ? 'y' : axis === 'Z' ? 'z' : 'x';
  const s = mode === 'scale' ? rounded(clamp(handle.scale[channel], .1, 6)) : previous.scale || 1;
  let {rx = 0, ry = 0, rz = 0} = previous;
  if (mode === 'rotate') {
    const angles = anglesFor(handle.quaternion, previous);
    rx = rounded(angles.rx);
    ry = rounded(angles.ry);
    rz = rounded(angles.rz);
  }
  // Use the exact Euler values recorded in the form. placeModel then reuses
  // this orientation's tight bounds instead of scanning a large mesh twice.
  const q = rotationFor({rx, ry, rz});
  const box = rotatedBounds(mesh.geometry, q);
  const origin = handle.position.clone().sub(localPivot.clone().multiplyScalar(s).applyQuaternion(q));
  const center = box.getCenter(new THREE.Vector3()).multiplyScalar(s).add(origin);
  return {
    rx, ry, rz, scale: s, dx: rounded(clamp(center.x, -300, 300)), dy: rounded(clamp(center.y, -300, 300)),
    dz: rounded(clamp(origin.z + s*box.min.z, -300, 300))
  };
}

export function alignedTranslation(mesh, headBounds) {
  const center = headBounds.getCenter(new THREE.Vector3());
  const height = rotatedBounds(mesh.geometry, mesh.quaternion).getSize(new THREE.Vector3()).z * mesh.scale.x;
  const dz = center.z - height / 2;
  if ([center.x,center.y,dz].some((v) => Math.abs(v) > 300)) throw Error('居中位移超出 ±300 mm，请先核对模型缩放。');
  return { dx: rounded(center.x), dy: rounded(center.y), dz: rounded(dz) };
}
