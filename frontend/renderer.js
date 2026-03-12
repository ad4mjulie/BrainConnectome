import * as THREE from "three";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";
import { ConvexGeometry } from "three/addons/geometries/ConvexGeometry.js";

const COLORS = {
  inactive: new THREE.Color(0x7de2c3),
  incoming: new THREE.Color(0x9bff7a),
  outgoing: new THREE.Color(0xff9bd2),
};

function centerAndScalePositions(positions) {
  const n = Math.floor(positions.length / 3);
  if (!n) return { center: new THREE.Vector3(), scale: 1.0 };

  const xs = new Float32Array(n);
  const ys = new Float32Array(n);
  const zs = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    xs[i] = positions[i * 3];
    ys[i] = positions[i * 3 + 1];
    zs[i] = positions[i * 3 + 2];
  }

  const xsSorted = xs.slice().sort();
  const ysSorted = ys.slice().sort();
  const zsSorted = zs.slice().sort();
  const mid = Math.floor(n / 2);
  const center = new THREE.Vector3(
    n % 2 ? xsSorted[mid] : 0.5 * (xsSorted[mid - 1] + xsSorted[mid]),
    n % 2 ? ysSorted[mid] : 0.5 * (ysSorted[mid - 1] + ysSorted[mid]),
    n % 2 ? zsSorted[mid] : 0.5 * (zsSorted[mid - 1] + zsSorted[mid])
  );

  const radii = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const dx = positions[i * 3] - center.x;
    const dy = positions[i * 3 + 1] - center.y;
    const dz = positions[i * 3 + 2] - center.z;
    radii[i] = Math.sqrt(dx * dx + dy * dy + dz * dz);
  }
  const radiiSorted = radii.slice().sort();
  const p98 = radiiSorted[Math.min(n - 1, Math.floor(n * 0.98))] || 1e-6;
  const scale = 240.0 / p98;

  for (let i = 0; i < n; i++) {
    let dx = positions[i * 3] - center.x;
    let dy = positions[i * 3 + 1] - center.y;
    let dz = positions[i * 3 + 2] - center.z;
    const r = Math.sqrt(dx * dx + dy * dy + dz * dz);
    if (r > p98 && r > 1e-6) {
      const s = p98 / r;
      dx *= s;
      dy *= s;
      dz *= s;
    }
    positions[i * 3] = dx * scale;
    positions[i * 3 + 1] = dy * scale;
    positions[i * 3 + 2] = dz * scale;
  }

  return { center, scale };
}

export class ConnectomeRenderer {
  constructor(container) {
    this.container = container;
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.FogExp2(0x000000, 0.0032);

    this.camera = new THREE.PerspectiveCamera(55, 1, 0.1, 5000);
    this.camera.position.set(0, 0, 520);

    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      powerPreference: "high-performance",
    });
    this.renderer.setClearColor(0x000000, 1.0);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    container.appendChild(this.renderer.domElement);

    const ambient = new THREE.AmbientLight(0xffffff, 0.75);
    this.scene.add(ambient);
    const dir = new THREE.DirectionalLight(0xffffff, 0.45);
    dir.position.set(0.5, 0.8, 0.6);
    this.scene.add(dir);

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();

    this.neuronIds = [];
    this.neuronTypes = [];
    this.positions = null; // Float32Array (N*3)
    this.outEdges = null; // Array<Array<edgeIdx>>
    this.inEdges = null;

    this.synPre = null; // Int32Array (M)
    this.synPost = null;

    this.neuronMesh = null; // InstancedMesh
    this.synapseLines = null; // LineSegments
    this.brainGroup = new THREE.Group();
    this.scene.add(this.brainGroup);

    this.center = new THREE.Vector3();
    this.scale = 1.0;
    this.highlightOutgoing = null;

    this.selectedIndex = null;

    this.neuronActivity = null; // Float32Array per instance
    this.neuronVisible = null; // Float32Array per instance

    this.maxPulses = 2200;
    this.pulses = []; // {a,b,t0,dur}
    this.pulsePositions = new Float32Array(this.maxPulses * 3);
    this.pulseGeom = new THREE.BufferGeometry();
    this.pulseGeom.setAttribute("position", new THREE.BufferAttribute(this.pulsePositions, 3));
    const pulseMat = new THREE.PointsMaterial({
      color: 0xff6a4a,
      size: 2.2,
      transparent: true,
      opacity: 0.95,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    this.pulsePoints = new THREE.Points(this.pulseGeom, pulseMat);
    this.scene.add(this.pulsePoints);

    this._tmpA = new THREE.Vector3();
    this._tmpB = new THREE.Vector3();
    this._tmpScale = new THREE.Vector3();
    this._tmpQuat = new THREE.Quaternion();

    this.flashUntil = new Float64Array(0);

    this.neuronShaders = null;
    this.synapseShaders = null;

    this.clock = new THREE.Clock();
    this.isolatedIndex = null;
    this.signalStrength = 0.5;
    this.pulseSpeed = 1.6;
    this.pulseStrength = 0.8;

    this.targetCameraPos = new THREE.Vector3().copy(this.camera.position);
    this.targetLookAt = new THREE.Vector3(0, 0, 0);
    this.cameraLerp = 1.0;

    this.resize();
    window.addEventListener("resize", () => this.resize());
  }

  getDomElement() {
    return this.renderer.domElement;
  }

  resize() {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    this.camera.aspect = w / Math.max(h, 1);
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
  }

  setConnectome(neurons, connections) {
    if (!this.neuronShaders || !this.synapseShaders) {
      throw new Error("Shaders not loaded; call loadShaders() first.");
    }
    this.neuronIds = neurons.neuron_ids;
    this.neuronTypes = neurons.types || new Array(this.neuronIds.length).fill(null);

    // Flatten positions to Float32Array for fast math.
    const posList = neurons.positions;
    const N = posList.length;
    const flat = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      const p = posList[i];
      flat[i * 3] = p[0];
      flat[i * 3 + 1] = p[1];
      flat[i * 3 + 2] = p[2];
    }
    const result = centerAndScalePositions(flat);
    this.center.copy(result.center);
    this.scale = result.scale;
    this.positions = flat;

    this.generateBrainShell();
  }

  generateBrainShell() {
    this.brainGroup.clear();
    if (!this.positions) return;

    // Use a subset of points for the hull to keep it smooth/fast
    const points = [];
    const step = Math.max(1, Math.floor(this.positions.length / 3 / 400));
    for (let i = 0; i < this.positions.length / 3; i += step) {
      points.push(new THREE.Vector3(
        this.positions[i * 3],
        this.positions[i * 3 + 1],
        this.positions[i * 3 + 2]
      ));
    }

    try {
      const geom = new ConvexGeometry(points);
      const mat = new THREE.MeshPhysicalMaterial({
        color: 0x88bbff,
        metalness: 0.1,
        roughness: 0.1,
        transparent: true,
        opacity: 0.35,
        side: THREE.DoubleSide,
        depthWrite: false,
        transmission: 0.2, // Less transparent
        thickness: 1.0,
      });
      const mesh = new THREE.Mesh(geom, mat);

      // Add a wireframe for that "digital brain" look
      const wireMat = new THREE.MeshBasicMaterial({
        color: 0xbbddff,
        wireframe: true,
        transparent: true,
        opacity: 0.03,
      });
      const wire = new THREE.Mesh(geom, wireMat);

      this.brainGroup.add(mesh);
      this.brainGroup.add(wire);
    } catch (e) {
      console.warn("Could not generate brain shell:", e);
    }
  }

  async loadMesh(roi) {
    const loader = new OBJLoader();
    const clean_roi = roi.replace("(", "_").replace(")", "_");
    try {
      const obj = await new Promise((resolve, reject) => {
        loader.load(`/mesh/${clean_roi}`, resolve, undefined, reject);
      });

      const mat = new THREE.MeshPhongMaterial({
        color: 0x444444,
        transparent: true,
        opacity: 0.15,
        side: THREE.DoubleSide,
        depthWrite: false,
      });

      obj.traverse((child) => {
        if (child.isMesh) {
          child.material = mat;
        }
      });

      // Align to biological coords then apply simulator's centering/scaling
      obj.position.sub(this.center).multiplyScalar(this.scale);
      obj.scale.multiplyScalar(this.scale);

      this.brainGroup.add(obj);
    } catch (e) {
      console.warn(`Could not load mesh for ${roi}:`, e);
    }

    this.synPre = Int32Array.from(connections.pre);
    this.synPost = Int32Array.from(connections.post);

    this._buildAdjacency(N, this.synPre, this.synPost);
    this.flashUntil = new Float64Array(N);
    this._buildNeuronMesh(N);
    this._buildSynapseLines(N, this.synPre, this.synPost);
  }

  _buildAdjacency(N, pre, post) {
    const outEdges = new Array(N);
    const inEdges = new Array(N);
    for (let i = 0; i < N; i++) {
      outEdges[i] = [];
      inEdges[i] = [];
    }
    for (let e = 0; e < pre.length; e++) {
      outEdges[pre[e]].push(e);
      inEdges[post[e]].push(e);
    }
    this.outEdges = outEdges;
    this.inEdges = inEdges;
  }

  _buildNeuronMesh(N) {
    if (this.neuronMesh) this.scene.remove(this.neuronMesh);

    const geom = new THREE.SphereGeometry(0.85, 18, 18);
    const activityAttr = new THREE.InstancedBufferAttribute(new Float32Array(N), 1);
    geom.setAttribute("activity", activityAttr);
    this.neuronActivity = activityAttr.array;
    const visibleAttr = new THREE.InstancedBufferAttribute(new Float32Array(N).fill(1.0), 1);
    geom.setAttribute("visible", visibleAttr);
    this.neuronVisible = visibleAttr.array;

    const mat = new THREE.ShaderMaterial({
      vertexShader: this.neuronShaders.vert,
      fragmentShader: this.neuronShaders.frag,
      transparent: true,
      depthWrite: false,
      uniforms: {
        activityLevel: { value: 1.0 },
      },
    });

    const mesh = new THREE.InstancedMesh(geom, mat, N);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);

    let maxDegree = 0;
    const degrees = new Float32Array(N);
    for (let i = 0; i < N; i++) {
      const d = (this.outEdges?.[i]?.length ?? 0) + (this.inEdges?.[i]?.length ?? 0);
      degrees[i] = d;
      if (d > maxDegree) maxDegree = d;
    }
    if (maxDegree <= 0) maxDegree = 1;

    const colors = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      const x = this.positions[i * 3];
      const y = this.positions[i * 3 + 1];
      const z = this.positions[i * 3 + 2];
      const size = 0.7 + 1.0 * Math.sqrt(degrees[i] / maxDegree);
      const m = new THREE.Matrix4();
      this._tmpA.set(x, y, z);
      this._tmpScale.set(size, size, size);
      m.compose(this._tmpA, this._tmpQuat, this._tmpScale);
      mesh.setMatrixAt(i, m);
      colors[i * 3] = COLORS.inactive.r;
      colors[i * 3 + 1] = COLORS.inactive.g;
      colors[i * 3 + 2] = COLORS.inactive.b;
    }
    mesh.instanceColor = new THREE.InstancedBufferAttribute(colors, 3);
    mesh.instanceColor.needsUpdate = true;

    this.neuronMesh = mesh;
    this.scene.add(mesh);
  }

  _buildSynapseLines(N, pre, post) {
    if (this.synapseLines) this.scene.remove(this.synapseLines);

    const M = pre.length;
    const positions = new Float32Array(M * 2 * 3);
    const tpos = new Float32Array(M * 2);
    const mask = new Float32Array(M * 2).fill(1.0);
    for (let e = 0; e < M; e++) {
      const a = pre[e];
      const b = post[e];
      const ax = this.positions[a * 3];
      const ay = this.positions[a * 3 + 1];
      const az = this.positions[a * 3 + 2];
      const bx = this.positions[b * 3];
      const by = this.positions[b * 3 + 1];
      const bz = this.positions[b * 3 + 2];
      const o = e * 6;
      positions[o] = ax;
      positions[o + 1] = ay;
      positions[o + 2] = az;
      positions[o + 3] = bx;
      positions[o + 4] = by;
      positions[o + 5] = bz;
      tpos[e * 2] = 0.0;
      tpos[e * 2 + 1] = 1.0;
      mask[e * 2] = 1.0;
      mask[e * 2 + 1] = 1.0;
    }

    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geom.setAttribute("tpos", new THREE.BufferAttribute(tpos, 1));
    geom.setAttribute("mask", new THREE.BufferAttribute(mask, 1));
    const mat = new THREE.ShaderMaterial({
      vertexShader: this.synapseShaders.vert,
      fragmentShader: this.synapseShaders.frag,
      transparent: true,
      depthWrite: false,
      uniforms: {
        time: { value: 0.0 },
        signalStrength: { value: 0.5 },
      },
    });
    const lines = new THREE.LineSegments(geom, mat);
    this.synapseLines = lines;
    this.scene.add(lines);
  }

  pick(clientX, clientY) {
    if (!this.neuronMesh) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const x = ((clientX - rect.left) / rect.width) * 2 - 1;
    const y = -(((clientY - rect.top) / rect.height) * 2 - 1);
    this.pointer.set(x, y);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObject(this.neuronMesh, false);
    if (!hits.length) return null;
    return hits[0].instanceId ?? null;
  }

  selectNeuron(index) {
    this.selectedIndex = index;
    this._updateHighlights(index);
    return this._selectedMetadata(index);
  }

  _selectedMetadata(i) {
    const neuronId = this.neuronIds[i];
    const type = this.neuronTypes[i] ?? null;
    const outCount = this.outEdges?.[i]?.length ?? 0;
    const inCount = this.inEdges?.[i]?.length ?? 0;
    return { index: i, neuronId, type, outCount, inCount };
  }

  _updateHighlights(i) {
    if (this.highlightIncoming) this.scene.remove(this.highlightIncoming);
    if (this.highlightOutgoing) this.scene.remove(this.highlightOutgoing);
    this.highlightIncoming = null;
    this.highlightOutgoing = null;
    if (i == null || !this.outEdges || !this.inEdges) return;

    const build = (edgeIdxs, color) => {
      const positions = new Float32Array(edgeIdxs.length * 2 * 3);
      for (let k = 0; k < edgeIdxs.length; k++) {
        const e = edgeIdxs[k];
        const a = this.synPre[e];
        const b = this.synPost[e];
        const o = k * 6;
        positions[o] = this.positions[a * 3];
        positions[o + 1] = this.positions[a * 3 + 1];
        positions[o + 2] = this.positions[a * 3 + 2];
        positions[o + 3] = this.positions[b * 3];
        positions[o + 4] = this.positions[b * 3 + 1];
        positions[o + 5] = this.positions[b * 3 + 2];
      }
      const geom = new THREE.BufferGeometry();
      geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      const mat = new THREE.LineBasicMaterial({
        color,
        transparent: true,
        opacity: 0.55,
        depthWrite: false,
      });
      return new THREE.LineSegments(geom, mat);
    };

    const incoming = this.inEdges[i];
    const outgoing = this.outEdges[i];
    if (incoming.length) {
      this.highlightIncoming = build(incoming, COLORS.incoming);
      this.scene.add(this.highlightIncoming);
    }
    if (outgoing.length) {
      this.highlightOutgoing = build(outgoing, COLORS.outgoing);
      this.scene.add(this.highlightOutgoing);
    }
  }

  isolateSelected(idx, enable = true) {
    if (idx == null || !this.neuronVisible || !this.synapseLines) return;
    this.isolatedIndex = enable ? idx : null;
    const N = this.neuronIds.length;
    if (!enable) {
      this.neuronVisible.fill(1.0);
      this.synapseLines.geometry.attributes.mask.array.fill(1.0);
    } else {
      this.neuronVisible.fill(0.0);
      this.synapseLines.geometry.attributes.mask.array.fill(0.0);
      const neighbors = new Set();
      const outE = this.outEdges?.[idx] ?? [];
      const inE = this.inEdges?.[idx] ?? [];
      const selectedEdges = [...outE, ...inE];
      for (const e of selectedEdges) {
        const a = this.synPre[e];
        const b = this.synPost[e];
        neighbors.add(a);
        neighbors.add(b);
        const o = e * 2;
        this.synapseLines.geometry.attributes.mask.array[o] = 1.0;
        this.synapseLines.geometry.attributes.mask.array[o + 1] = 1.0;
      }
      this.neuronVisible[idx] = 1.0;
      neighbors.forEach((n) => {
        this.neuronVisible[n] = 1.0;
      });
    }
    this.neuronMesh.geometry.attributes.visible.needsUpdate = true;
    this.synapseLines.geometry.attributes.mask.needsUpdate = true;
  }

  updateActivity(activity, spikes) {
    if (!this.neuronMesh) return;
    const N = this.neuronIds.length;
    const now = performance.now();
    const spikeSet = new Set(spikes);
    for (let i = 0; i < N; i++) {
      const spikeFlash = this.flashUntil[i] && this.flashUntil[i] > now ? 1.0 : 0.0;
      const val = Math.min(1.0, activity[i] + (spikeSet.has(i) ? 1.0 : 0.0) + spikeFlash);
      this.neuronActivity[i] = val;
    }
    this.neuronMesh.geometry.attributes.activity.needsUpdate = true;
    this._spawnPulses(spikes);
  }

  _spawnPulses(spikeIdxs) {
    if (!this.outEdges || !this.positions) return;
    const now = performance.now();

    // Cap spawned pulses per update to avoid overload on bursts.
    let budget = 260;
    for (let s = 0; s < spikeIdxs.length && budget > 0; s++) {
      const pre = spikeIdxs[s];
      budget = this._spawnPulsesFrom(pre, budget, now);
    }
  }

  _spawnPulsesFrom(pre, budget, now) {
    const edges = this.outEdges?.[pre];
    if (!edges || edges.length === 0) return budget;

    // Pick a few outgoing edges (random-ish).
    const picks = Math.min(6, edges.length, budget);
    for (let k = 0; k < picks; k++) {
      const e = edges[(Math.random() * edges.length) | 0];
      const post = this.synPost[e];
      this.pulses.push({ a: pre, b: post, t0: now, dur: 260 + Math.random() * 200 });
      budget--;
      if (this.pulses.length > this.maxPulses) this.pulses.shift();
    }
    return budget;
  }

  _updatePulses() {
    const now = performance.now();
    let w = 0;
    for (let r = 0; r < this.pulses.length; r++) {
      const p = this.pulses[r];
      const t = (now - p.t0) / p.dur;
      if (t >= 1) continue;

      const ax = this.positions[p.a * 3];
      const ay = this.positions[p.a * 3 + 1];
      const az = this.positions[p.a * 3 + 2];
      const bx = this.positions[p.b * 3];
      const by = this.positions[p.b * 3 + 1];
      const bz = this.positions[p.b * 3 + 2];
      const px = ax + (bx - ax) * t;
      const py = ay + (by - ay) * t;
      const pz = az + (bz - az) * t;

      const o = w * 3;
      this.pulsePositions[o] = px;
      this.pulsePositions[o + 1] = py;
      this.pulsePositions[o + 2] = pz;
      w++;
      if (w >= this.maxPulses) break;
    }
    // Keep only the survivors.
    this.pulses = this.pulses.filter((p) => (now - p.t0) / p.dur < 1).slice(-this.maxPulses);
    this.pulseGeom.setDrawRange(0, w);
    this.pulseGeom.attributes.position.needsUpdate = true;
  }

  render(controls) {
    if (controls) controls.update();
    const delta = this.clock.getDelta();
    const t = this.clock.elapsedTime;
    if (this.synapseLines && this.synapseLines.material.uniforms?.time) {
      this.synapseLines.material.uniforms.time.value = t * this.pulseSpeed;
    }
    if (this.synapseLines && this.synapseLines.material.uniforms?.signalStrength) {
      this.synapseLines.material.uniforms.signalStrength.value = this.signalStrength;
    }
    if (this.neuronMesh && this.neuronMesh.material.uniforms?.activityLevel) {
      this.neuronMesh.material.uniforms.activityLevel.value = 1.0;
    }
    this._updatePulses();
    this._updateCamera(delta);
    this.renderer.render(this.scene, this.camera);
  }

  _updateCamera(dt) {
    if (this.cameraLerp >= 1.0) return;
    this.cameraLerp = Math.min(1.0, this.cameraLerp + dt * 2.5);
    const t = 1.0 - Math.pow(1.0 - this.cameraLerp, 3); // easeOutCubic

    this.camera.position.lerpVectors(this.startCameraPos, this.targetCameraPos, t);
    // Smoothly update controls target if they exist
    // This is tricky with OrbitControls, but we can set them directly
  }

  flyTo(index) {
    if (index == null || !this.positions) return;
    const x = this.positions[index * 3];
    const y = this.positions[index * 3 + 1];
    const z = this.positions[index * 3 + 2];

    this.startCameraPos = this.camera.position.clone();

    // Calculate a nice offset position
    const offset = new THREE.Vector3(0, 40, 100);
    this.targetCameraPos.set(x, y, z).add(offset);
    this.targetLookAt.set(x, y, z);
    this.cameraLerp = 0;

    // Update OrbitControls target
    return { x, y, z };
  }

  stimulateVisual(index) {
    if (!this.positions) return;
    this.flashNeuron(index, 500);
    const now = performance.now();
    this._spawnPulsesFrom(index, 18, now);
  }

  flashNeuron(index, ms = 150) {
    if (!this.flashUntil || index == null) return;
    if (index < 0 || index >= this.flashUntil.length) return;
    this.flashUntil[index] = performance.now() + ms;
  }

  isolateSelected(idx, enable = true) {
    if (!this.neuronVisible || !this.synapseLines) return;
    this.isolatedIndex = enable ? idx : null;
    if (!enable || idx == null) {
      this.neuronVisible.fill(1.0);
      this.synapseLines.geometry.attributes.mask.array.fill(1.0);
    } else {
      this.neuronVisible.fill(0.0);
      this.synapseLines.geometry.attributes.mask.array.fill(0.0);
      const neighbors = new Set();
      const outE = this.outEdges?.[idx] ?? [];
      const inE = this.inEdges?.[idx] ?? [];
      const selectedEdges = [...outE, ...inE];
      for (const e of selectedEdges) {
        const a = this.synPre[e];
        const b = this.synPost[e];
        neighbors.add(a);
        neighbors.add(b);
        const o = e * 2;
        this.synapseLines.geometry.attributes.mask.array[o] = 1.0;
        this.synapseLines.geometry.attributes.mask.array[o + 1] = 1.0;
      }
      this.neuronVisible[idx] = 1.0;
      neighbors.forEach((n) => {
        this.neuronVisible[n] = 1.0;
      });
    }
    this.neuronMesh.geometry.attributes.visible.needsUpdate = true;
    this.synapseLines.geometry.attributes.mask.needsUpdate = true;
  }

  setIsolation(idx) {
    if (idx == null) this.isolateSelected(null, false);
    else this.isolateSelected(idx, true);
  }

  setPulseStrength(v) {
    this.signalStrength = v;
    if (this.synapseLines?.material.uniforms?.signalStrength) {
      this.synapseLines.material.uniforms.signalStrength.value = v;
    }
  }

  setHopIsolation(hops) {
    this.hopDepth = hops;
  }

  toggleSynapses(show) {
    if (this.synapseLines) {
      this.synapseLines.visible = show;
    }
  }

  captureScreenshot() {
    const dataURL = this.renderer.domElement.toDataURL("image/png");
    const a = document.createElement("a");
    a.href = dataURL;
    a.download = "connectome.png";
    a.click();
  }

  peekMetadata(i) {
    return this._selectedMetadata(i);
  }

  async loadShaders() {
    if (this.neuronShaders && this.synapseShaders) return;
    const load = async (path) => fetch(path).then((r) => r.text());
    const [nVert, nFrag, sVert, sFrag] = await Promise.all([
      load("/static/shaders/neuron.vert"),
      load("/static/shaders/neuron.frag"),
      load("/static/shaders/synapse.vert"),
      load("/static/shaders/synapse.frag"),
    ]);
    this.neuronShaders = { vert: nVert, frag: nFrag };
    this.synapseShaders = { vert: sVert, frag: sFrag };
  }
}
