/* ==========================================================================
   Custom Zero-Dependency WebGL GLSL Aurora Shader + 3D Projection Stage
   Renders:
   1. Fullscreen GLSL Volumetric Obsidian Jade / Cyan / Indigo Aurora +
      Radial Chromatic Break Pulse + Film Grain (#bgShaderCanvas)
   2. 3D Subdivided Triangulated Icosahedron Cipher Orb with Z-camera
      punch-through portal transition (Apekon style) (#fg3dCanvas)
   3. 3D Orbital MCP Constellation with 7 Satellite Agent Nodes, 14 Orbiting
      Tool Beacons & Animated Energy Pulse Packets (Cyera AI Guardian style)
   4. Deep Space Parallax Particles & Focus Industries 3D Speed Grid
   ========================================================================== */
(function (global) {
  'use strict';

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function smoothstep(e0, e1, x) {
    const t = clamp((x - e0) / (e1 - e0), 0, 1);
    return t * t * (3 - 2 * t);
  }
  function easeOutExpo(t) { return t >= 1 ? 1 : 1 - Math.pow(2, -10 * clamp(t, 0, 1)); }
  function easeInOutCubic(t) {
    t = clamp(t, 0, 1);
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  /* ------------------------------------------------------------------------
     Subdivided Icosahedron Mesh Generator (Triangulated Apekon Cipher Orb)
     ------------------------------------------------------------------------ */
  function buildSubdividedIcosahedron(subdivisions) {
    const phi = (1 + Math.sqrt(5)) / 2;
    let verts = [
      [-1,  phi, 0], [ 1,  phi, 0], [-1, -phi, 0], [ 1, -phi, 0],
      [ 0, -1,  phi], [ 0,  1,  phi], [ 0, -1, -phi], [ 0,  1, -phi],
      [ phi, 0, -1], [ phi, 0,  1], [-phi, 0, -1], [-phi, 0,  1]
    ].map(normalize3);

    let faces = [
      [0,11,5],[0,5,1],[0,1,7],[0,7,10],[0,10,11],
      [1,5,9],[5,11,4],[11,10,2],[10,7,6],[7,1,8],
      [3,9,4],[3,4,2],[3,2,6],[3,6,8],[3,8,9],
      [4,9,5],[2,4,11],[6,2,10],[8,6,7],[9,8,1]
    ];

    for (let s = 0; s < subdivisions; s++) {
      const midCache = new Map();
      const nextFaces = [];
      const getMid = (i1, i2) => {
        const key = i1 < i2 ? `${i1}_${i2}` : `${i2}_${i1}`;
        if (midCache.has(key)) return midCache.get(key);
        const a = verts[i1], b = verts[i2];
        const m = normalize3([(a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5, (a[2] + b[2]) * 0.5]);
        const idx = verts.length;
        verts.push(m);
        midCache.set(key, idx);
        return idx;
      };
      for (const [a, b, c] of faces) {
        const ab = getMid(a, b);
        const bc = getMid(b, c);
        const ca = getMid(c, a);
        nextFaces.push([a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]);
      }
      faces = nextFaces;
    }

    // Unique wireframe edges
    const edgeSet = new Set();
    const edges = [];
    for (const [a, b, c] of faces) {
      const pairs = [[a, b], [b, c], [c, a]];
      for (const [u, v] of pairs) {
        const key = u < v ? `${u}_${v}` : `${v}_${u}`;
        if (!edgeSet.has(key)) {
          edgeSet.add(key);
          edges.push([u, v]);
        }
      }
    }
    return { verts, faces, edges };
  }

  function normalize3(v) {
    const l = Math.hypot(v[0], v[1], v[2]) || 1;
    return [v[0] / l, v[1] / l, v[2] / l];
  }

  function rotateXYZ(p, rx, ry, rz) {
    let x = p[0], y = p[1], z = p[2];
    // Rotate X
    const cx = Math.cos(rx), sx = Math.sin(rx);
    let y1 = y * cx - z * sx;
    let z1 = y * sx + z * cx;
    y = y1; z = z1;
    // Rotate Y
    const cy = Math.cos(ry), sy = Math.sin(ry);
    let x2 = x * cy + z * sy;
    let z2 = -x * sy + z * cy;
    x = x2; z = z2;
    // Rotate Z
    const cz = Math.cos(rz), sz = Math.sin(rz);
    let x3 = x * cz - y * sz;
    let y3 = x * sz + y * cz;
    return [x3, y3, z];
  }

  /* ------------------------------------------------------------------------
     GLSL Shaders for Volumetric Obsidian Jade Aurora Backdrop
     ------------------------------------------------------------------------ */
  const VERT_SRC = `
    attribute vec2 aPos;
    varying vec2 vUv;
    void main() {
      vUv = aPos * 0.5 + 0.5;
      gl_Position = vec4(aPos, 0.0, 1.0);
    }
  `;

  const FRAG_SRC = `
    precision mediump float;
    varying vec2 vUv;
    uniform vec2 uRes;
    uniform float uTime;
    uniform vec2 uMouse;
    uniform float uBreak;     // Apekon portal break chromatic shockwave (0..1)
    uniform float uActMood;   // Smooth act color mood parameter

    float hash(vec2 p) {
      return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
    }

    float noise(vec2 p) {
      vec2 i = floor(p);
      vec2 f = fract(p);
      vec2 u = f * f * (3.0 - 2.0 * f);
      return mix(
        mix(hash(i + vec2(0.0, 0.0)), hash(i + vec2(1.0, 0.0)), u.x),
        mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x),
        u.y
      );
    }

    float fbm(vec2 p) {
      float v = 0.0;
      float a = 0.5;
      mat2 rot = mat2(0.8, 0.6, -0.6, 0.8);
      for (int i = 0; i < 4; i++) {
        v += a * noise(p);
        p = rot * p * 2.04 + vec2(1.7, 9.2);
        a *= 0.5;
      }
      return v;
    }

    void main() {
      vec2 uv = vUv;
      vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / min(uRes.x, uRes.y);
      p += (uMouse - 0.5) * 0.08;

      float t = uTime * 0.18;
      float n1 = fbm(p * 1.85 + vec2(t, -t * 0.65));
      float n2 = fbm(p * 2.65 - vec2(t * 0.8, t * 0.5) + n1 * 1.25);

      // Deep Obsidian Void Base
      vec3 baseVoid = vec3(0.022, 0.036, 0.072);
      vec3 jade     = vec3(0.028, 0.757, 0.376);
      vec3 cyan     = vec3(0.024, 0.714, 0.831);
      vec3 indigo   = vec3(0.388, 0.400, 0.945);

      // Flowing aurora curtains
      float ribbon1 = smoothstep(0.32, 0.86, n2);
      float ribbon2 = smoothstep(0.40, 0.90, fbm(p * 2.1 + vec2(-t * 0.6, t * 0.9)));

      vec3 col = baseVoid;
      col += jade * ribbon1 * (0.20 + 0.08 * sin(uActMood * 1.4));
      col += cyan * ribbon2 * 0.16;
      col += indigo * smoothstep(0.55, 0.95, n1) * 0.12;

      // Center radial luminescence
      float d = length(p);
      float centerGlow = exp(-d * d * 2.2);
      col += mix(jade, cyan, 0.45) * centerGlow * (0.14 + uBreak * 0.55);

      // Apekon Portal Break Ring & Chromatic Shockwave
      if (uBreak > 0.005) {
        float ringR = uBreak * 1.35;
        float ringG = uBreak * 1.28;
        float ringB = uBreak * 1.21;
        float w = 0.055 + uBreak * 0.08;
        float rC = exp(-pow((d - ringR) / w, 2.0));
        float gC = exp(-pow((d - ringG) / w, 2.0));
        float bC = exp(-pow((d - ringB) / w, 2.0));
        col += vec3(rC * 0.35, gC * 0.95, bC * 0.85) * (1.0 - uBreak * 0.55) * 0.78;
      }

      // Subtle tactile film grain
      float grain = (hash(gl_FragCoord.xy + fract(uTime * 17.0) * 100.0) - 0.5) * 0.026;
      col += grain;

      gl_FragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
    }
  `;

  /* ------------------------------------------------------------------------
     Main WebGLStage Controller
     ------------------------------------------------------------------------ */
  class WebGLStage {
    constructor(bgCanvas, fgCanvas) {
      this.bgCanvas = bgCanvas;
      this.fgCanvas = fgCanvas;
      this.ctx = fgCanvas.getContext('2d');
      this.gl = null;
      this.glProg = null;
      this.width = window.innerWidth;
      this.height = window.innerHeight;
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);

      this.mouseX = 0.5;
      this.mouseY = 0.5;
      this.smoothMX = 0.5;
      this.smoothMY = 0.5;

      // Geometry assets
      this.orbMesh = buildSubdividedIcosahedron(2); // 320 crisp triangles!
      this.stars = this._initStars(180);
      this.agentSpecs = this._initAgentNodes();
      this.mcpOrbitals = this._initMcpOrbitals(14);

      this._initWebGL();
      this.resize();
    }

    _initWebGL() {
      const gl = this.bgCanvas.getContext('webgl', { alpha: false, antialias: false })
        || this.bgCanvas.getContext('experimental-webgl');
      if (!gl) return;
      this.gl = gl;

      const compile = (type, src) => {
        const s = gl.createShader(type);
        gl.shaderSource(s, src);
        gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
          console.warn('GLSL error:', gl.getShaderInfoLog(s));
          return null;
        }
        return s;
      };

      const vs = compile(gl.VERTEX_SHADER, VERT_SRC);
      const fs = compile(gl.FRAGMENT_SHADER, FRAG_SRC);
      if (!vs || !fs) { this.gl = null; return; }

      const prog = gl.createProgram();
      gl.attachShader(prog, vs);
      gl.attachShader(prog, fs);
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
        this.gl = null;
        return;
      }

      this.glProg = prog;
      const buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, 1,1]), gl.STATIC_DRAW);
      const loc = gl.getAttribLocation(prog, 'aPos');
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

      this.uRes = gl.getUniformLocation(prog, 'uRes');
      this.uTime = gl.getUniformLocation(prog, 'uTime');
      this.uMouse = gl.getUniformLocation(prog, 'uMouse');
      this.uBreak = gl.getUniformLocation(prog, 'uBreak');
      this.uActMood = gl.getUniformLocation(prog, 'uActMood');
    }

    _initStars(count) {
      const list = [];
      let seed = 1337;
      const rand = () => {
        seed = (seed * 16807) % 2147483647;
        return (seed - 1) / 2147483646;
      };
      for (let i = 0; i < count; i++) {
        list.push({
          x: (rand() - 0.5) * 14,
          y: (rand() - 0.5) * 9,
          z: rand() * 14 + 1.5,
          size: rand() * 1.7 + 0.5,
          hue: rand() < 0.55 ? '52,211,153' : (rand() < 0.8 ? '34,211,238' : '129,140,248'),
          alpha: rand() * 0.55 + 0.2
        });
      }
      return list;
    }

    _initAgentNodes() {
      // 7 local AI agent satellites on a non-overlapping 3D rosette (51.4deg min separation)
      const count = 7;
      return Array.from({ length: count }, (_, i) => ({
        baseAngle: (i / count) * Math.PI * 2 - 0.35,
        depthPhase: i * 1.15,
        pulseOffset: i * 0.14
      }));
    }

    _initMcpOrbitals(count) {
      const arr = [];
      for (let i = 0; i < count; i++) {
        const phi = Math.acos(1 - 2 * (i + 0.5) / count);
        const theta = Math.PI * (1 + Math.sqrt(5)) * i;
        arr.push({ phi, theta, r: 0.92 + (i % 3) * 0.16 });
      }
      return arr;
    }

    resize() {
      this.width = window.innerWidth;
      this.height = window.innerHeight;
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);

      // BG WebGL can safely run at 0.8x DPR for silky 60fps shader fill-rate
      const bgScale = Math.min(this.dpr, 1.25);
      this.bgCanvas.width = Math.floor(this.width * bgScale);
      this.bgCanvas.height = Math.floor(this.height * bgScale);

      this.fgCanvas.width = Math.floor(this.width * this.dpr);
      this.fgCanvas.height = Math.floor(this.height * this.dpr);
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

      if (this.gl) {
        this.gl.viewport(0, 0, this.bgCanvas.width, this.bgCanvas.height);
      }
    }

    setMouse(nx, ny) {
      this.mouseX = clamp(nx, 0, 1);
      this.mouseY = clamp(ny, 0, 1);
    }

    render(t, clockSec) {
      this.smoothMX = lerp(this.smoothMX, this.mouseX, 0.08);
      this.smoothMY = lerp(this.smoothMY, this.mouseY, 0.08);

      // Compute Apekon portal break progress (10.5s -> 12.6s)
      const portalBreak = t >= 10.5 && t <= 13.0
        ? smoothstep(10.5, 12.2, t) * (1 - smoothstep(12.2, 13.0, t))
        : 0;

      // 1. Render GLSL Aurora Backdrop (or crisp Canvas2D gradient fallback)
      if (this.gl && this.glProg) {
        const gl = this.gl;
        gl.useProgram(this.glProg);
        gl.uniform2f(this.uRes, this.bgCanvas.width, this.bgCanvas.height);
        gl.uniform1f(this.uTime, clockSec);
        gl.uniform2f(this.uMouse, this.smoothMX, 1.0 - this.smoothMY);
        gl.uniform1f(this.uBreak, portalBreak);
        gl.uniform1f(this.uActMood, t * 0.18);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      } else {
        const bctx = this.bgCanvas.getContext('2d');
        if (bctx) {
          const g = bctx.createRadialGradient(
            this.bgCanvas.width * 0.5, this.bgCanvas.height * 0.45, 20,
            this.bgCanvas.width * 0.5, this.bgCanvas.height * 0.5, this.bgCanvas.width * 0.75
          );
          g.addColorStop(0, '#0a2224');
          g.addColorStop(0.5, '#070f1d');
          g.addColorStop(1, '#040710');
          bctx.fillStyle = g;
          bctx.fillRect(0, 0, this.bgCanvas.width, this.bgCanvas.height);
        }
      }

      // 2. Clear 3D Foreground Projection Canvas
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.width, this.height);

      // Always render subtle 3D parallax star/data dust
      this._renderStarfield(ctx, t, portalBreak);

      // 3. Act 1 ending + Act 2 + Finale: Triangulated Cipher Orb (Apekon)
      const orbVisibleAct12 = t >= 3.6 && t <= 12.9;
      const orbVisibleFinale = t >= 38.2;
      if (orbVisibleAct12 || orbVisibleFinale) {
        this._renderCipherOrb(ctx, t, clockSec, orbVisibleFinale);
      }

      // 4. Act 3: 3D MCP Constellation Network (Cyera AI Guardian)
      let constellationLayout = null;
      if (t >= 12.1 && t <= 21.3) {
        constellationLayout = this._renderConstellation(ctx, t, clockSec);
      }

      // 5. Act 4: Magnetic Energy Halo behind Cheese AI Morph Shell
      if (t >= 20.5 && t <= 28.8) {
        this._renderAct4MagneticHalo(ctx, t, clockSec);
      }

      // 6. Act 6: Focus Industries 3D Perspective Flythrough Terrain Grid
      if (t >= 35.0) {
        this._renderAct6TerrainGrid(ctx, t, clockSec);
      }

      return {
        portalBreak,
        constellationLayout
      };
    }

    _renderStarfield(ctx, t, portalBreak) {
      const cx = this.width * 0.5;
      const cy = this.height * 0.5;
      const fov = Math.min(this.width, this.height) * 0.78;
      const speedZ = t * 0.45 + portalBreak * 4.2;
      const tiltX = (this.smoothMY - 0.5) * 0.16;
      const tiltY = (this.smoothMX - 0.5) * 0.22;

      for (let i = 0; i < this.stars.length; i++) {
        const s = this.stars[i];
        let z = ((s.z - speedZ) % 14 + 14) % 14 + 0.7;
        const p = rotateXYZ([s.x, s.y, z - 5], tiltX, tiltY, 0);
        const pz = p[2] + 6.2;
        if (pz <= 0.4) continue;
        const scale = fov / pz;
        const sx = cx + p[0] * scale;
        const sy = cy + p[1] * scale;
        if (sx < -40 || sx > this.width + 40 || sy < -40 || sy > this.height + 40) continue;

        const r = Math.max(0.5, s.size * (2.6 / pz));
        const a = s.alpha * smoothstep(14.5, 4.0, pz) * smoothstep(0.5, 1.6, pz);

        if (portalBreak > 0.08) {
          // Warp speed radial streaks during Apekon orb break!
          const prevScale = fov / (pz + 0.65 * portalBreak);
          const px = cx + p[0] * prevScale;
          const py = cy + p[1] * prevScale;
          ctx.strokeStyle = `rgba(${s.hue},${(a * 0.95).toFixed(3)})`;
          ctx.lineWidth = r * 1.1;
          ctx.beginPath();
          ctx.moveTo(px, py);
          ctx.lineTo(sx, sy);
          ctx.stroke();
        } else {
          ctx.fillStyle = `rgba(${s.hue},${a.toFixed(3)})`;
          ctx.beginPath();
          ctx.arc(sx, sy, r, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }

    _renderCipherOrb(ctx, t, clockSec, isFinale) {
      const cx = this.width * 0.5;
      const cy = this.height * 0.5;
      const minDim = Math.min(this.width, this.height);

      let alpha = 1;
      let camZ = 4.4;
      let radius = 1.15;
      let unlockBlend = 0;
      let shatterExpand = 0;

      if (isFinale) {
        const p = smoothstep(38.2, 40.0, t);
        alpha = p * 0.48;
        camZ = lerp(6.2, 4.9, easeOutExpo(p));
        radius = 1.38;
        unlockBlend = 1;
      } else {
        // Appear gently at end of Act 1, fully present in Act 2
        const enter = smoothstep(3.6, 6.2, t);
        unlockBlend = smoothstep(8.2, 9.2, t);
        // Through-the-orb camera punch from 10.5s -> 12.7s!
        const punch = easeInOutCubic((t - 10.5) / 2.1);
        camZ = lerp(6.2, 4.15, enter) - punch * 4.55; // Camera flies STRAIGHT THROUGH z=0!
        shatterExpand = smoothstep(10.7, 12.3, t) * 1.15;
        alpha = enter * (1 - smoothstep(12.2, 12.9, t));
      }

      if (alpha <= 0.01) return;

      const fov = minDim * 0.92;
      const rx = clockSec * 0.24 + (this.smoothMY - 0.5) * 0.65;
      const ry = clockSec * 0.36 + (this.smoothMX - 0.5) * 0.85;
      const rz = clockSec * 0.09;

      // Transform vertices with subtle harmonic wave displacement
      const projected = new Array(this.orbMesh.verts.length);
      for (let i = 0; i < this.orbMesh.verts.length; i++) {
        const v = this.orbMesh.verts[i];
        const wave = 1.0
          + 0.055 * Math.sin(v[0] * 4.2 + clockSec * 2.6) * Math.cos(v[1] * 4.0 - clockSec * 2.1)
          + shatterExpand * (0.45 + 0.55 * Math.abs(Math.sin(i * 1.7)));
        const p = rotateXYZ([v[0] * radius * wave, v[1] * radius * wave, v[2] * radius * wave], rx, ry, rz);
        const dz = camZ - p[2];
        if (dz <= 0.12) {
          projected[i] = null;
        } else {
          const s = fov / dz;
          projected[i] = {
            x: cx + p[0] * s,
            y: cy + p[1] * s,
            z: p[2],
            dz
          };
        }
      }

      ctx.save();
      // Subtle outer emerald aura glow behind orb
      const auraR = minDim * (0.28 + shatterExpand * 0.35);
      const aura = ctx.createRadialGradient(cx, cy, 10, cx, cy, auraR);
      const coreColor = unlockBlend > 0.5 ? '7,193,96' : '6,182,212';
      aura.addColorStop(0, `rgba(${coreColor},${(0.24 * alpha).toFixed(3)})`);
      aura.addColorStop(0.55, `rgba(6,182,212,${(0.08 * alpha).toFixed(3)})`);
      aura.addColorStop(1, 'rgba(7,11,20,0)');
      ctx.fillStyle = aura;
      ctx.beginPath();
      ctx.arc(cx, cy, auraR, 0, Math.PI * 2);
      ctx.fill();

      // Depth-sorted translucent crystalline facets
      const sortedFaces = [];
      for (let i = 0; i < this.orbMesh.faces.length; i++) {
        const [a, b, c] = this.orbMesh.faces[i];
        const pa = projected[a], pb = projected[b], pc = projected[c];
        if (!pa || !pb || !pc) continue;
        // Backface & lighting heuristic
        const cross = (pb.x - pa.x) * (pc.y - pa.y) - (pb.y - pa.y) * (pc.x - pa.x);
        if (cross > 0 && shatterExpand < 0.15) continue;
        const avgZ = (pa.z + pb.z + pc.z) / 3;
        sortedFaces.push({ pa, pb, pc, avgZ, idx: i });
      }
      sortedFaces.sort((f1, f2) => f1.avgZ - f2.avgZ);

      for (let i = 0; i < sortedFaces.length; i++) {
        const { pa, pb, pc, avgZ } = sortedFaces[i];
        const depthLight = clamp((avgZ + 1.2) / 2.4, 0.12, 1);
        const r = Math.round(lerp(10, 16, unlockBlend));
        const g = Math.round(lerp(130, 215, unlockBlend));
        const b = Math.round(lerp(195, 145, unlockBlend));
        const fillA = (0.045 + depthLight * 0.085) * alpha;
        const strokeA = (0.18 + depthLight * 0.52) * alpha;

        ctx.beginPath();
        ctx.moveTo(pa.x, pa.y);
        ctx.lineTo(pb.x, pb.y);
        ctx.lineTo(pc.x, pc.y);
        ctx.closePath();

        ctx.fillStyle = `rgba(${r},${g},${b},${fillA.toFixed(3)})`;
        ctx.fill();
        ctx.strokeStyle = `rgba(${Math.min(255, r + 40)},${Math.min(255, g + 35)},${Math.min(255, b + 40)},${strokeA.toFixed(3)})`;
        ctx.lineWidth = shatterExpand > 0.2 ? 1.35 : 0.85;
        ctx.stroke();
      }

      // Luminous equatorial scan ring during decryption (7.2s -> 9.6s)
      if (!isFinale && t >= 6.8 && t <= 10.6) {
        const scanT = ((t - 6.8) % 2.2) / 2.2;
        const scanY = lerp(-1.2, 1.2, scanT);
        const ringR = Math.sqrt(Math.max(0.05, 1.44 - scanY * scanY)) * (fov / camZ);
        const sy = cy + scanY * (fov / camZ) * 0.72;
        ctx.strokeStyle = `rgba(52,211,153,${(0.75 * alpha * Math.sin(scanT * Math.PI)).toFixed(3)})`;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.ellipse(cx, sy, ringR, ringR * 0.24, 0, 0, Math.PI * 2);
        ctx.stroke();
      }

      ctx.restore();
    }

    _renderConstellation(ctx, t, clockSec) {
      const enter = smoothstep(12.2, 13.5, t);
      const exit = 1 - smoothstep(20.1, 21.2, t);
      const alpha = enter * exit;
      if (alpha <= 0.01) return null;

      // Keep constellation centered cleanly in the right 54% free space on desktop
      const isNarrow = this.width < 960;
      const leftSafe = isNarrow ? 76 : Math.min(540, this.width * 0.455) + 74;
      const rightSafe = this.width - 88;
      const cx = isNarrow ? this.width * 0.5 : (leftSafe + rightSafe) * 0.5;
      const cy = this.height * 0.49;
      const availHalfW = Math.max(120, (rightSafe - leftSafe) * 0.5);
      const availHalfH = Math.max(130, (this.height - 196) * 0.44);
      const fov = Math.min(this.width, this.height) * 0.86;
      const camZ = 5.4 - enter * 0.35;

      const orbitRotY = (t - 12.5) * 0.24 + (this.smoothMX - 0.5) * 0.38;
      const orbitRotX = 0.20 + (this.smoothMY - 0.5) * 0.30;

      ctx.save();

      // Draw subtle 3D orbital rings around MCP Core (Cyera AI Guardian style)
      for (let ring = 0; ring < 2; ring++) {
        const rxRing = ring === 0 ? availHalfW * 0.54 : availHalfW * 0.92;
        const ryRing = ring === 0 ? availHalfH * 0.56 : availHalfH * 0.90;
        ctx.beginPath();
        for (let k = 0; k <= 72; k++) {
          const a = (k / 72) * Math.PI * 2;
          const px = cx + Math.cos(a) * rxRing;
          const py = cy + Math.sin(a) * ryRing;
          if (k === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
        ctx.strokeStyle = ring === 0
          ? `rgba(52,211,153,${(0.22 * alpha).toFixed(3)})`
          : `rgba(34,211,238,${(0.14 * alpha).toFixed(3)})`;
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      // 14 MCP Tool Orbiting Micro-Beacons around Core
      for (let i = 0; i < this.mcpOrbitals.length; i++) {
        const m = this.mcpOrbitals[i];
        const th = m.theta + clockSec * 0.45;
        const p3 = [
          m.r * Math.sin(m.phi) * Math.cos(th),
          m.r * Math.cos(m.phi),
          m.r * Math.sin(m.phi) * Math.sin(th)
        ];
        const rot = rotateXYZ(p3, orbitRotX, orbitRotY, 0);
        const s = fov / (camZ - rot[2]);
        const sx = cx + rot[0] * s * 0.72;
        const sy = cy + rot[1] * s * 0.72;
        const depthA = clamp((rot[2] + 1.4) / 2.6, 0.2, 1) * alpha;
        ctx.fillStyle = `rgba(52,211,153,${(0.85 * depthA).toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(sx, sy, 2.2 + depthA * 1.4, 0, Math.PI * 2);
        ctx.fill();
      }

      // Compute 7 Agent Satellite non-overlapping 3D rosette positions + glowing beams
      const projectedAgents = [];
      const activeIdx = Math.floor(((t - 13.0) * 1.15) % 7 + 7) % 7;
      const slowSpin = (t - 12.5) * 0.14 + (this.smoothMX - 0.5) * 0.18;
      const orbitRx = availHalfW * 0.90;
      const orbitRy = availHalfH * 0.88;

      for (let i = 0; i < this.agentSpecs.length; i++) {
        const ag = this.agentSpecs[i];
        const ang = ag.baseAngle + slowSpin;
        const depthZ = Math.sin(ang * 2 + ag.depthPhase * 0.4) * 0.65;
        const persp = 1 + depthZ * 0.08;
        const sx = cx + Math.cos(ang) * orbitRx * persp;
        const sy = cy + Math.sin(ang) * orbitRy * persp + (this.smoothMY - 0.5) * 16;
        const isLit = (i === activeIdx || i === (activeIdx + 3) % 7);

        // Quadratic curved energy beam from Core (cx, cy) to Agent (sx, sy)
        const midX = (cx + sx) * 0.5 + (sy - cy) * 0.14;
        const midY = (cy + sy) * 0.5 - (sx - cx) * 0.14;

        const grad = ctx.createLinearGradient(cx, cy, sx, sy);
        grad.addColorStop(0, `rgba(7,193,96,${((isLit ? 0.85 : 0.34) * alpha).toFixed(3)})`);
        grad.addColorStop(1, `rgba(34,211,238,${((isLit ? 0.92 : 0.3) * alpha).toFixed(3)})`);
        ctx.strokeStyle = grad;
        ctx.lineWidth = isLit ? 2.1 : 1.05;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.quadraticCurveTo(midX, midY, sx, sy);
        ctx.stroke();

        // Traveling luminous data packet along Bezier curve
        const u = ((clockSec * 0.72 + ag.pulseOffset) % 1);
        const inv = 1 - u;
        const bx = inv * inv * cx + 2 * inv * u * midX + u * u * sx;
        const by = inv * inv * cy + 2 * inv * u * midY + u * u * sy;

        const pGlow = ctx.createRadialGradient(bx, by, 0, bx, by, isLit ? 12 : 7);
        pGlow.addColorStop(0, `rgba(209,250,229,${alpha.toFixed(3)})`);
        pGlow.addColorStop(0.4, `rgba(52,211,153,${(0.75 * alpha).toFixed(3)})`);
        pGlow.addColorStop(1, 'rgba(7,193,96,0)');
        ctx.fillStyle = pGlow;
        ctx.beginPath();
        ctx.arc(bx, by, isLit ? 12 : 7, 0, Math.PI * 2);
        ctx.fill();

        // Ripple ring on lit satellite
        if (isLit) {
          const rProg = (clockSec * 1.4 + i * 0.3) % 1;
          ctx.strokeStyle = `rgba(52,211,153,${((1 - rProg) * 0.7 * alpha).toFixed(3)})`;
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.arc(sx, sy, 8 + rProg * 28, 0, Math.PI * 2);
          ctx.stroke();
        }

        projectedAgents.push({ x: sx, y: sy, depth: depthZ, isLit, scale: clamp(0.92 + depthZ * 0.1, 0.84, 1.05) });
      }

      // Central Core radial plasma
      const cGlow = ctx.createRadialGradient(cx, cy, 4, cx, cy, 68);
      cGlow.addColorStop(0, `rgba(209,250,229,${(0.9 * alpha).toFixed(3)})`);
      cGlow.addColorStop(0.28, `rgba(7,193,96,${(0.55 * alpha).toFixed(3)})`);
      cGlow.addColorStop(1, 'rgba(7,193,96,0)');
      ctx.fillStyle = cGlow;
      ctx.beginPath();
      ctx.arc(cx, cy, 68, 0, Math.PI * 2);
      ctx.fill();

      ctx.restore();
      return { coreX: cx, coreY: cy, agents: projectedAgents, activeIdx, alpha };
    }

    _renderAct4MagneticHalo(ctx, t, clockSec) {
      const enter = smoothstep(20.6, 21.6, t);
      const exit = 1 - smoothstep(28.0, 28.8, t);
      const alpha = enter * exit;
      if (alpha <= 0.01) return;

      const cx = this.width * 0.5;
      const cy = this.height * 0.54;
      const morphP = smoothstep(23.1, 24.4, t);
      const r = lerp(95, 290, morphP);

      ctx.save();
      const grad = ctx.createRadialGradient(cx, cy, 10, cx, cy, r);
      grad.addColorStop(0, `rgba(7,193,96,${(0.26 * alpha).toFixed(3)})`);
      grad.addColorStop(0.55, `rgba(6,182,212,${(0.1 * alpha).toFixed(3)})`);
      grad.addColorStop(1, 'rgba(5,9,18,0)');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fill();

      // Magnetic pull rings during noise-filtering phase
      if (morphP < 0.95) {
        const ringAlpha = (1 - morphP) * alpha;
        for (let k = 0; k < 3; k++) {
          const p = (1 - ((clockSec * 0.65 + k / 3) % 1));
          ctx.strokeStyle = `rgba(52,211,153,${(p * 0.35 * ringAlpha).toFixed(3)})`;
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          ctx.arc(cx, cy, 38 + p * 210, 0, Math.PI * 2);
          ctx.stroke();
        }
      }
      ctx.restore();
    }

    _renderAct6TerrainGrid(ctx, t, clockSec) {
      // Focus Industries aerospace 3D perspective floor grid flying forward
      const enter = smoothstep(35.0, 36.2, t);
      const finaleFade = 1 - smoothstep(38.8, 40.4, t) * 0.55;
      const alpha = enter * finaleFade;
      if (alpha <= 0.01) return;

      const cx = this.width * 0.5;
      const horizonY = this.height * 0.58;
      const speed = (clockSec * 1.4) % 1;

      ctx.save();
      ctx.strokeStyle = `rgba(52,211,153,${(0.16 * alpha).toFixed(3)})`;
      ctx.lineWidth = 1;

      // Perspective radiating meridians
      for (let i = -8; i <= 8; i++) {
        ctx.beginPath();
        ctx.moveTo(cx + i * 28, horizonY);
        ctx.lineTo(cx + i * 195, this.height + 20);
        ctx.stroke();
      }

      // Approaching horizontal depth lines
      for (let j = 0; j < 10; j++) {
        const z = ((j + speed) / 10);
        const y = horizonY + Math.pow(z, 2.2) * (this.height - horizonY + 40);
        ctx.strokeStyle = `rgba(34,211,238,${(z * 0.22 * alpha).toFixed(3)})`;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(this.width, y);
        ctx.stroke();
      }
      ctx.restore();
    }
  }

  global.PromoWebGLStage = WebGLStage;
})(window);
