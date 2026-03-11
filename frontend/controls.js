import { OrbitControls } from "three/addons/controls/OrbitControls.js";

export function createOrbitControls(camera, domElement) {
  const controls = new OrbitControls(camera, domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.rotateSpeed = 0.6;
  controls.zoomSpeed = 1.0;
  controls.panSpeed = 0.6;
  controls.screenSpacePanning = true;
  controls.minDistance = 30;
  controls.maxDistance = 1200;
  return controls;
}

