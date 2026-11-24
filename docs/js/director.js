/* ==========================================================================
   WECHAT AGENT BRIDGE — 42s DETERMINISTIC 60FPS CINEMA TIMELINE DIRECTOR
   Coordinates all 6 Awwwards-inspired Acts in lockstep with WebGLStage & Audio
   ========================================================================== */
(function (global) {
  'use strict';

  const DURATION = 42.0;

  const ACT_META = [
    { id: 1, start: 0.0,  end: 5.5,  label: 'ACT 01 // GRIDS KINETIC HOOK' },
    { id: 2, start: 5.5,  end: 12.5, label: 'ACT 02 // THROUGH THE CIPHER ORB' },
    { id: 3, start: 12.5, end: 20.5, label: 'ACT 03 // MCP CONSTELLATION (14×7)' },
    { id: 4, start: 20.5, end: 28.5, label: 'ACT 04 // QWEN 3B SILENT FLIP MORPH' },
    { id: 5, start: 28.5, end: 35.5, label: 'ACT 05 // CURVED LIQUID GLASS STUDIO' },
    { id: 6, start: 35.5, end: 42.0, label: 'ACT 06 // AIR-GAPPED MOBILE SYNC' }
  ];

  const HEX_CHARS = '0123456789ABCDEF#%';

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function smoothstep(e0, e1, x) {
    const t = clamp((x - e0) / (e1 - e0), 0, 1);
    return t * t * (3 - 2 * t);
  }
  function easeOutExpo(t) {
    t = clamp(t, 0, 1);
    return t >= 1 ? 1 : 1 - Math.pow(2, -10 * t);
  }
  function easeOutBack(t) {
    t = clamp(t, 0, 1);
    const c1 = 1.70158;
    const c3 = c1 + 1;
    return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
  }

  function formatTime(sec) {
    const s = clamp(sec, 0, DURATION);
    const mins = Math.floor(s / 60);
    const secs = Math.floor(s % 60);
    const cs = Math.floor((s % 1) * 100);
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}.${String(cs).padStart(2, '0')}`;
  }

  function scrambleText(target, progress, seedFloat) {
    const p = clamp(progress, 0, 1);
    const locked = Math.floor(target.length * p);
    let out = '';
    for (let i = 0; i < target.length; i++) {
      if (i < locked || target[i] === ' ' || target[i] === '·') {
        out += target[i];
      } else {
        const idx = Math.floor(Math.abs(Math.sin(seedFloat * 29.3 + i * 7.13)) * HEX_CHARS.length) % HEX_CHARS.length;
        out += HEX_CHARS[idx];
      }
    }
    return out;
  }

  /* ------------------------------------------------------------------------
     Micro-Confetti Particle Engine (Act 4 Task Completion)
     ------------------------------------------------------------------------ */
  class ConfettiBurst {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d');
      this.particles = [];
      this._buildParticles();
    }

    _buildParticles() {
      const colors = ['#34d399', '#07c160', '#22d3ee', '#818cf8', '#fde68a', '#ffffff'];
      let seed = 4099;
      const rand = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
      for (let i = 0; i < 42; i++) {
        const ang = rand() * Math.PI * 2;
        const spd = 65 + rand() * 175;
        this.particles.push({
          vx: Math.cos(ang) * spd,
          vy: Math.sin(ang) * spd - 65,
          size: 3 + rand() * 4.5,
          rot: rand() * Math.PI * 2,
          vrot: (rand() - 0.5) * 12,
          color: colors[i % colors.length]
        });
      }
    }

    renderAt(ageSec) {
      const ctx = this.ctx;
      const w = this.canvas.width;
      const h = this.canvas.height;
      ctx.clearRect(0, 0, w, h);
      if (ageSec <= 0 || ageSec > 1.8) return;

      const ox = 36;
      const oy = 90;
      const g = 240;
      const alpha = clamp(1 - ageSec / 1.65, 0, 1);

      ctx.save();
      for (let i = 0; i < this.particles.length; i++) {
        const p = this.particles[i];
        const x = ox + p.vx * ageSec * 0.72;
        const y = oy + p.vy * ageSec * 0.72 + 0.5 * g * ageSec * ageSec;
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(p.rot + p.vrot * ageSec);
        ctx.globalAlpha = alpha;
        ctx.fillStyle = p.color;
        ctx.fillRect(-p.size * 0.5, -p.size * 0.35, p.size, p.size * 0.65);
        ctx.restore();
      }
      ctx.restore();
    }
  }

  /* ------------------------------------------------------------------------
     Main Cinema Director
     ------------------------------------------------------------------------ */
  class PromoDirector {
    constructor() {
      this.t = 0;
      this.clockSec = 0;
      this.isPlaying = true;
      this.isScrubbing = false;
      this.lastFrameMs = performance.now();

      // Mouse reticle state
      this.mx = window.innerWidth * 0.5;
      this.my = window.innerHeight * 0.5;
      this.rx = this.mx;
      this.ry = this.my;

      // MediaRecorder state for 1-click WebM export
      this.mediaRecorder = null;
      this.recChunks = [];

      this._cacheDom();
      this._buildHeatmap();

      this.stage = new global.PromoWebGLStage(this.el.bgShaderCanvas, this.el.fg3dCanvas);
      this.sound = new global.PromoSoundEngine();
      this.confetti = new ConfettiBurst(this.el.confettiCanvas);

      this._bindEvents();
      this._tick(performance.now());
    }

    _cacheDom() {
      const $ = (id) => document.getElementById(id);
      this.el = {
        reticle: $('reticleCursor'),
        bgShaderCanvas: $('bgShaderCanvas'),
        fg3dCanvas: $('fg3dCanvas'),
        archGrid: $('archGrid'),
        archAngleCoord: $('archAngleCoord'),
        cinemaStage: $('cinemaStage'),

        playStateDot: $('playStateDot'),
        hudActLabel: $('hudActLabel'),
        hudTimecode: $('hudTimecode'),
        btnSound: $('btnSound'),
        soundLabel: $('soundLabel'),
        btnCredits: $('btnCredits'),
        btnRecord: $('btnRecord'),
        recordLabel: $('recordLabel'),

        // Acts
        acts: [1, 2, 3, 4, 5, 6].map((n) => $('act' + n)),

        // Act 1
        act1LoaderFill: $('act1LoaderFill'),
        act1LoaderPct: $('act1LoaderPct'),
        act1Line1: $('act1Line1'),
        act1Line2: $('act1Line2'),
        act1Subrow: $('act1Subrow'),

        // Act 2
        act2TopHud: document.querySelector('.act2-top-hud'),
        act2BottomCopy: document.querySelector('.act2-bottom-copy'),
        cipherStatusPill: $('cipherStatusPill'),
        cipherStatusText: $('cipherStatusText'),
        orbReticle: $('orbReticle'),
        act2ScrambleCode: $('act2ScrambleCode'),
        act2DbCounter: $('act2DbCounter'),
        act2Title: $('act2Title'),

        // Act 3
        act3Editorial: document.querySelector('.act3-editorial'),
        mcpTags: Array.from(document.querySelectorAll('.mcp-tag')),
        coreLabel: $('constellationCoreLabel'),
        agentNodes: Array.from(document.querySelectorAll('.agent-node')),

        // Act 4
        chatChips: Array.from(document.querySelectorAll('.chat-chip')),
        morphShell: $('morphShell'),
        morphPillFace: $('morphPillFace'),
        morphPillStatus: $('morphPillStatus'),
        morphTodoFace: $('morphTodoFace'),
        todoPctText: $('todoPctText'),
        todoBarFill: $('todoBarFill'),
        todoItem1: $('todoItem1'),
        todoItem2: $('todoItem2'),
        todoItem3: $('todoItem3'),
        todoCheckPath: $('todoCheckPath'),
        confettiCanvas: $('confettiCanvas'),

        // Act 5
        pvnHudFrame: $('pvnHudFrame'),
        pvnCaption: $('pvnCaption'),
        glassCards: [0, 1, 2].map((i) => $('glassCard' + i)),
        kpiMsgCount: $('kpiMsgCount'),
        heatmapGrid: $('heatmapGrid'),
        trendLinePath: $('trendLinePath'),
        themeRippleLayer: $('themeRippleLayer'),
        mockNavPill: $('mockNavPill'),
        mockTabs: Array.from(document.querySelectorAll('.mock-tab')),

        // Act 6
        act6ArchScene: $('act6ArchScene'),
        vaultCard: $('vaultCard'),
        phoneMockup: $('phoneMockup'),
        phoneLiveTask: $('phoneLiveTask'),
        phoneToast: $('phoneToast'),
        act6Finale: $('act6Finale'),
        btnReplayFinale: $('btnReplayFinale'),
        btnExploreFinale: $('btnExploreFinale'),

        // Bottom Transport
        btnPlayPause: $('btnPlayPause'),
        iconPause: $('iconPause'),
        iconPlay: $('iconPlay'),
        btnRestart: $('btnRestart'),
        chapterBtns: Array.from(document.querySelectorAll('.chap-btn')),
        scrubberRail: $('scrubberRail'),
        scrubberFill: $('scrubberFill'),
        scrubberThumb: $('scrubberThumb'),

        // Credits drawer
        creditsDrawer: $('creditsDrawer'),
        creditsBackdrop: $('creditsBackdrop'),
        btnCloseCredits: $('btnCloseCredits'),
        creditCards: Array.from(document.querySelectorAll('.credit-card'))
      };

      // Cache SVG path length for Act 5 trend line draw animation
      this.trendLen = this.el.trendLinePath.getTotalLength ? this.el.trendLinePath.getTotalLength() : 380;
      this.el.trendLinePath.style.strokeDasharray = String(this.trendLen);
    }

    _buildHeatmap() {
      // 36 cells (3 rows x 12 cols) representing 24h peak activity matrix
      const intensities = [
        0.12, 0.08, 0.06, 0.09, 0.25, 0.52, 0.78, 0.88, 0.72, 0.64, 0.82, 0.94,
        0.18, 0.10, 0.14, 0.34, 0.62, 0.85, 0.96, 0.91, 0.76, 0.88, 0.98, 0.70,
        0.22, 0.15, 0.20, 0.48, 0.74, 0.90, 0.82, 0.68, 0.79, 0.93, 0.85, 0.55
      ];
      this.hmCells = [];
      const frag = document.createDocumentFragment();
      intensities.forEach((val) => {
        const cell = document.createElement('span');
        cell.className = 'hm-cell';
        cell.dataset.val = String(val);
        frag.appendChild(cell);
        this.hmCells.push(cell);
      });
      this.el.heatmapGrid.appendChild(frag);
    }

    _bindEvents() {
      window.addEventListener('resize', () => this.stage.resize());

      window.addEventListener('mousemove', (e) => {
        this.mx = e.clientX;
        this.my = e.clientY;
        this.stage.setMouse(e.clientX / window.innerWidth, e.clientY / window.innerHeight);

        const target = e.target;
        const isInteractive = !!(target && target.closest && target.closest('a, button, .scrubber-rail, .credit-card'));
        this.el.reticle.classList.toggle('hover', isInteractive);
      });

      // Play / Pause & Restart
      this.el.btnPlayPause.addEventListener('click', () => this.togglePlay());
      this.el.btnRestart.addEventListener('click', () => this.restart());
      this.el.btnReplayFinale.addEventListener('click', () => this.restart());

      // Sound toggle
      this.el.btnSound.addEventListener('click', () => {
        const on = this.sound.toggle();
        this.el.btnSound.classList.toggle('active-sound', on);
        this.el.soundLabel.textContent = on ? '音效 ON' : '音效 OFF';
      });

      // Credits drawer
      const openCredits = () => {
        this.el.creditsDrawer.classList.add('open');
        this.el.creditsDrawer.setAttribute('aria-hidden', 'false');
      };
      const closeCredits = () => {
        this.el.creditsDrawer.classList.remove('open');
        this.el.creditsDrawer.setAttribute('aria-hidden', 'true');
      };
      this.el.btnCredits.addEventListener('click', openCredits);
      this.el.btnExploreFinale.addEventListener('click', openCredits);
      this.el.btnCloseCredits.addEventListener('click', closeCredits);
      this.el.creditsBackdrop.addEventListener('click', closeCredits);

      this.el.creditCards.forEach((card) => {
        card.addEventListener('click', () => {
          const jump = parseFloat(card.dataset.jump || '0');
          closeCredits();
          this.seekTo(jump, true);
        });
      });

      // Chapter jump pills
      this.el.chapterBtns.forEach((btn) => {
        btn.addEventListener('click', () => {
          const jump = parseFloat(btn.dataset.time || '0');
          this.seekTo(jump, true);
        });
      });

      // Timeline scrubbing
      const scrubFromClientX = (clientX) => {
        const rect = this.el.scrubberRail.getBoundingClientRect();
        const pct = clamp((clientX - rect.left) / rect.width, 0, 1);
        this.seekTo(pct * DURATION, false);
      };

      this.el.scrubberRail.addEventListener('pointerdown', (e) => {
        this.isScrubbing = true;
        this.wasPlayingBeforeScrub = this.isPlaying;
        this.isPlaying = false;
        this._syncPlayButton();
        scrubFromClientX(e.clientX);
        this.el.scrubberRail.setPointerCapture(e.pointerId);
      });

      this.el.scrubberRail.addEventListener('pointermove', (e) => {
        if (!this.isScrubbing) return;
        scrubFromClientX(e.clientX);
      });

      this.el.scrubberRail.addEventListener('pointerup', () => {
        if (!this.isScrubbing) return;
        this.isScrubbing = false;
        if (this.wasPlayingBeforeScrub) {
          this.isPlaying = true;
          this._syncPlayButton();
        }
      });

      // Mouse wheel Awwwards-style smooth scrubbing
      window.addEventListener('wheel', (e) => {
        if (this.el.creditsDrawer.classList.contains('open')) return;
        e.preventDefault();
        const delta = clamp(e.deltaY * 0.006, -1.2, 1.2);
        this.t = clamp(this.t + delta, 0, DURATION);
      }, { passive: false });

      // Keyboard controls
      window.addEventListener('keydown', (e) => {
        if (e.code === 'Space') {
          e.preventDefault();
          this.togglePlay();
        } else if (e.key === 'r' || e.key === 'R') {
          this.restart();
        } else if (e.key === 'm' || e.key === 'M') {
          this.el.btnSound.click();
        } else if (e.key === 'ArrowRight') {
          this.seekTo(clamp(this.t + 2.5, 0, DURATION), this.isPlaying);
        } else if (e.key === 'ArrowLeft') {
          this.seekTo(clamp(this.t - 2.5, 0, DURATION), this.isPlaying);
        } else if (e.key === 'Escape') {
          closeCredits();
        }
      });

      // 1-Click WebM Video Recording Export (captures exact tab at 60fps)
      this.el.btnRecord.addEventListener('click', () => this._toggleRecord());
    }

    async _toggleRecord() {
      if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
        this.mediaRecorder.stop();
        return;
      }

      if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
        alert('当前浏览器环境不支持一键录屏导出，请使用 Chrome / Edge 打开后点击「导出视频」。');
        return;
      }

      try {
        const stream = await navigator.mediaDevices.getDisplayMedia({
          video: { frameRate: 60 },
          audio: true,
          preferCurrentTab: true
        });

        const mime = MediaRecorder.isTypeSupported('video/webm;codecs=vp9')
          ? 'video/webm;codecs=vp9'
          : 'video/webm';

        this.recChunks = [];
        this.mediaRecorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 10000000 });

        this.mediaRecorder.ondataavailable = (ev) => {
          if (ev.data && ev.data.size > 0) this.recChunks.push(ev.data);
        };

        this.mediaRecorder.onstop = () => {
          stream.getTracks().forEach((tr) => tr.stop());
          this.el.btnRecord.classList.remove('recording');
          this.el.recordLabel.textContent = '导出视频';
          if (this.recChunks.length) {
            const blob = new Blob(this.recChunks, { type: mime });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'WeChat-Agent-Bridge-Promo.webm';
            a.click();
            setTimeout(() => URL.revokeObjectURL(url), 5000);
          }
        };

        // Restart cleanly from 00:00 and auto-stop at 42.0s!
        this.restart();
        this.mediaRecorder.start(250);
        this.el.btnRecord.classList.add('recording');
        this.el.recordLabel.textContent = '录制中 · 点击停止';
      } catch (err) {
        // User canceled capture picker
      }
    }

    togglePlay() {
      if (this.t >= DURATION - 0.05) {
        this.restart();
        return;
      }
      this.isPlaying = !this.isPlaying;
      this._syncPlayButton();
    }

    restart() {
      this.t = 0;
      this.isPlaying = true;
      this.sound.resetEvents();
      this._syncPlayButton();
    }

    seekTo(sec, keepPlaying) {
      this.t = clamp(sec, 0, DURATION);
      this.sound.resetEvents();
      if (typeof keepPlaying === 'boolean') {
        this.isPlaying = keepPlaying;
        this._syncPlayButton();
      }
    }

    _syncPlayButton() {
      this.el.iconPause.style.display = this.isPlaying ? 'block' : 'none';
      this.el.iconPlay.style.display = this.isPlaying ? 'none' : 'block';
      this.el.playStateDot.classList.toggle('paused', !this.isPlaying);
    }

    _getCurrentAct(t) {
      for (let i = ACT_META.length - 1; i >= 0; i--) {
        if (t >= ACT_META[i].start) return ACT_META[i];
      }
      return ACT_META[0];
    }

    _tick(nowMs) {
      const dt = Math.min((nowMs - this.lastFrameMs) / 1000, 0.05);
      this.lastFrameMs = nowMs;
      this.clockSec += dt;

      if (this.isPlaying && !this.isScrubbing) {
        this.t += dt;
        if (this.t >= DURATION) {
          this.t = DURATION;
          this.isPlaying = false;
          this._syncPlayButton();
          if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.stop();
          }
        }
      }

      // Smooth Reticle Cursor Lerp
      this.rx = lerp(this.rx, this.mx, 0.22);
      this.ry = lerp(this.ry, this.my, 0.22);
      this.el.reticle.style.transform = `translate3d(${this.rx.toFixed(1)}px, ${this.ry.toFixed(1)}px, 0)`;

      // Render WebGL & 3D stage
      const stageData = this.stage.render(this.t, this.clockSec);

      // Update HUD + All 6 Acts deterministically from this.t
      this._updateHud(this.t);
      this._updateAct1(this.t);
      this._updateAct2(this.t);
      this._updateAct3(this.t, stageData.constellationLayout);
      this._updateAct4(this.t);
      this._updateAct5(this.t);
      this._updateAct6(this.t);

      // Trigger procedural soundscape cues
      const act = this._getCurrentAct(this.t);
      this.sound.onTimeUpdate(this.t, act.id, this.isPlaying);

      requestAnimationFrame((ts) => this._tick(ts));
    }

    _updateHud(t) {
      const act = this._getCurrentAct(t);
      this.el.hudActLabel.textContent = act.label;
      this.el.hudTimecode.textContent = `${formatTime(t)} / ${formatTime(DURATION)}`;

      const pct = (t / DURATION) * 100;
      this.el.scrubberFill.style.width = `${pct.toFixed(3)}%`;
      this.el.scrubberThumb.style.left = `${pct.toFixed(3)}%`;

      this.el.chapterBtns.forEach((btn, idx) => {
        btn.classList.toggle('active', idx + 1 === act.id);
      });

      // Architectural hairline grid builds during Act 1, stays crisp & subtle across film
      const gridDraw = easeOutExpo(clamp(t / 1.4, 0, 1));
      this.el.archGrid.style.setProperty('--grid-progress', gridDraw.toFixed(3));
      this.el.archGrid.style.opacity = t < 5.5 ? '0.82' : (t > 38.8 ? '0.72' : '0.42');

      const camZDisplay = (t >= 10.5 && t <= 12.6)
        ? (4.15 - smoothstep(10.5, 12.6, t) * 4.55)
        : (4.8 - Math.sin(t * 0.35) * 0.45);
      this.el.archAngleCoord.textContent = `CAM Z: ${camZDisplay >= 0 ? '+' : ''}${camZDisplay.toFixed(2)} · FOV 52°`;
    }

    _setActOpacity(idx, opacity, transformStr, interactive) {
      const el = this.el.acts[idx];
      const vis = opacity > 0.008;
      el.classList.toggle('is-visible', vis);
      el.classList.toggle('is-interactive', !!(vis && interactive));
      if (vis) {
        el.style.opacity = opacity.toFixed(3);
        el.style.transform = transformStr || 'none';
      } else {
        el.style.opacity = '0';
      }
    }

    /* ----------------------------------------------------------------------
       ACT 01 (0.0s – 5.5s): Grids Architectural Preloader + Kinetic Hook
       ---------------------------------------------------------------------- */
    _updateAct1(t) {
      const enter = smoothstep(0.0, 0.45, t);
      const exit = 1 - smoothstep(4.5, 5.55, t);
      const op = enter * exit;
      const zoomOut = smoothstep(4.5, 5.55, t);
      this._setActOpacity(0, op, `scale(${(1 + zoomOut * 0.09).toFixed(4)}) translate3d(0, ${(-zoomOut * 26).toFixed(1)}px, 0)`, false);
      if (op <= 0.008) return;

      // Precision numeric preloader (0 -> 100%)
      const loadP = easeOutExpo(clamp(t / 1.85, 0, 1));
      this.el.act1LoaderFill.style.width = `${(loadP * 100).toFixed(1)}%`;
      this.el.act1LoaderPct.textContent = `${String(Math.round(loadP * 100)).padStart(3, '0')}%`;

      // Masked razor-sharp kinetic headlines (Grids Obys expo-out reveal)
      const l1 = easeOutExpo(clamp((t - 0.55) / 1.1, 0, 1));
      const l2 = easeOutExpo(clamp((t - 0.85) / 1.15, 0, 1));
      this.el.act1Line1.style.transform = `translate3d(0, ${((1 - l1) * 118).toFixed(1)}%, 0)`;
      this.el.act1Line2.style.transform = `translate3d(0, ${((1 - l2) * 118).toFixed(1)}%, 0)`;

      // Subtitle & spec chips fade up
      const subP = easeOutExpo(clamp((t - 1.45) / 1.0, 0, 1));
      this.el.act1Subrow.style.opacity = subP.toFixed(3);
      this.el.act1Subrow.style.transform = `translate3d(0, ${((1 - subP) * 26).toFixed(1)}px, 0)`;
    }

    /* ----------------------------------------------------------------------
       ACT 02 (5.5s – 12.5s): Through The Cipher Orb (Apekon Break Portal)
       ---------------------------------------------------------------------- */
    _updateAct2(t) {
      const enter = smoothstep(5.2, 6.2, t);
      const punch = smoothstep(10.6, 12.25, t);
      const op = enter * (1 - smoothstep(11.3, 12.4, t));
      // Keep Act 2 anchored so top/bottom copy never clip the HUD bars during portal break
      this._setActOpacity(1, op, 'none', false);
      if (op <= 0.008) return;

      const copyFade = 1 - smoothstep(10.45, 11.35, t);
      if (this.el.act2TopHud) {
        this.el.act2TopHud.style.opacity = copyFade.toFixed(3);
        this.el.act2TopHud.style.transform = `translate3d(0, ${(-punch * 14).toFixed(1)}px, 0)`;
      }
      if (this.el.act2BottomCopy) {
        this.el.act2BottomCopy.style.opacity = copyFade.toFixed(3);
        this.el.act2BottomCopy.style.transform = `translate3d(0, ${(punch * -10).toFixed(1)}px, 0)`;
      }

      const unlocked = t >= 8.5;
      this.el.cipherStatusPill.classList.toggle('unlocked', unlocked);
      this.el.cipherStatusText.textContent = unlocked
        ? 'DECRYPTED · 20 DATABASES · 100% LOCAL'
        : 'SCANNING · SQLCIPHER 4 MEMORY (READ-ONLY)';

      // Mechanical hex cipher scramble -> clean key confirmation
      const decP = clamp((t - 6.2) / 2.35, 0, 1);
      this.el.act2ScrambleCode.textContent = scrambleText('SQLCIPHER 4 · KEY VERIFIED', decP, t);

      const dbCount = Math.round(easeOutExpo(clamp((t - 6.6) / 2.2, 0, 1)) * 20);
      this.el.act2DbCounter.textContent = `${String(dbCount).padStart(2, '0')} / 20 DB`;

      // Orb crosshair reticle expansion during portal break
      const retScale = lerp(0.86, 1.0, enter) + punch * 1.45;
      this.el.orbReticle.style.transform = `scale(${retScale.toFixed(3)}) rotate(${(punch * 18).toFixed(1)}deg)`;
      this.el.orbReticle.style.opacity = (1 - punch * 0.92).toFixed(3);
    }

    /* ----------------------------------------------------------------------
       ACT 03 (12.5s – 20.5s): MCP Constellation (Cyera AI Guardian)
       ---------------------------------------------------------------------- */
    _updateAct3(t, layout) {
      const enter = smoothstep(12.2, 13.3, t);
      const exit = 1 - smoothstep(19.6, 20.6, t);
      const op = enter * exit;
      const slideX = (1 - easeOutExpo(enter)) * -36;
      this._setActOpacity(2, op, `translate3d(${slideX.toFixed(1)}px, 0, 0)`, false);
      if (op <= 0.008) return;

      // Highlight rotating MCP tool telemetry badges
      const activeTool = Math.floor((t - 12.5) * 1.65) % this.el.mcpTags.length;
      this.el.mcpTags.forEach((tag, i) => {
        tag.classList.toggle('lit', i === activeTool || i === (activeTool + 3) % this.el.mcpTags.length);
      });

      // Sync projected 3D Agent coordinates onto DOM glass HUD badges with dynamic editorial guard
      if (layout) {
        const stageRect = this.el.cinemaStage.getBoundingClientRect();
        const padX = 80;
        const padY = 26;
        let leftGuard = 64;
        if (window.innerWidth >= 960 && this.el.act3Editorial) {
          const edRect = this.el.act3Editorial.getBoundingClientRect();
          leftGuard = Math.max(leftGuard, edRect.right - stageRect.left + 84);
        }

        const coreX = clamp(layout.coreX - stageRect.left, leftGuard, stageRect.width - 70);
        const coreY = clamp(layout.coreY - stageRect.top, 46, stageRect.height - 46);
        this.el.coreLabel.style.left = `${coreX.toFixed(1)}px`;
        this.el.coreLabel.style.top = `${coreY.toFixed(1)}px`;
        this.el.coreLabel.style.opacity = layout.alpha.toFixed(3);

        layout.agents.forEach((ag, i) => {
          const node = this.el.agentNodes[i];
          if (!node) return;
          const nx = clamp(ag.x - stageRect.left, leftGuard, stageRect.width - padX);
          const ny = clamp(ag.y - stageRect.top, padY, stageRect.height - padY);
          node.style.left = `${nx.toFixed(1)}px`;
          node.style.top = `${ny.toFixed(1)}px`;
          node.style.transform = `translate(-50%, -50%) scale(${ag.scale.toFixed(3)})`;
          node.style.opacity = (layout.alpha * clamp(0.75 + ag.depth * 0.22, 0.58, 1)).toFixed(3);
          node.classList.toggle('lit', ag.isLit);
        });
      }
    }

    /* ----------------------------------------------------------------------
       ACT 04 (20.5s – 28.5s): Silent Qwen 3B FLIP Morph (Cheese AI)
       ---------------------------------------------------------------------- */
    _updateAct4(t) {
      const enter = smoothstep(20.3, 21.3, t);
      const exit = 1 - smoothstep(27.7, 28.6, t);
      const op = enter * exit;
      this._setActOpacity(3, op, `translate3d(0, ${((1 - easeOutExpo(enter)) * 22).toFixed(1)}px, 0)`, false);
      if (op <= 0.008) return;

      // Phase 1 (20.5 - 23.0s): 3-row CSS grid with guaranteed vertical channel for center Qwen 3B pill;
      // Row 0 & Row 1 sit cleanly above pill, Row 2 sits cleanly below pill with zero overlap.
      const pullP = smoothstep(22.0, 23.25, t);
      const chipPositions = [
        { x: -14, y: -74, targetY: 56 },  { x: 14, y: -74, targetY: 56 },
        { x: -18, y: -78, targetY: 0 },   { x: 18, y: -78, targetY: 0 },
        { x: -14, y: 34,  targetY: -56 }, { x: 14, y: 34,  targetY: -56 }
      ];

      this.el.chatChips.forEach((chip, i) => {
        const isNoise = chip.dataset.kind === 'noise';
        const base = chipPositions[i] || { x: 0, y: 0, targetY: 0 };
        const staggerIn = easeOutExpo(clamp((t - 20.6 - i * 0.12) / 0.7, 0, 1));

        if (isNoise) {
          // Noise drifts outward, blurs & disintegrates (3-layer noise filter!)
          const drift = smoothstep(21.8, 22.9, t);
          const x = base.x + (i % 2 === 0 ? -96 : 96) * drift;
          const y = base.y + (base.y < 0 ? -36 : 36) * drift;
          chip.style.opacity = (staggerIn * (1 - drift)).toFixed(3);
          chip.style.filter = `blur(${(drift * 8).toFixed(1)}px)`;
          chip.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0) scale(${(1 - drift * 0.25).toFixed(3)})`;
        } else {
          // Signal chips compress magnetically into the exact visual center of Qwen 3B pill
          const x = lerp(base.x, 0, pullP);
          const y = lerp(base.y, base.targetY, pullP);
          const sc = lerp(1, 0.32, pullP);
          chip.style.opacity = (staggerIn * (1 - smoothstep(22.95, 23.35, t))).toFixed(3);
          chip.style.filter = 'none';
          chip.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0) scale(${sc.toFixed(3)})`;
        }
      });

      // Phase 2 (23.15 - 24.45s): Cheese AI FLIP Morph (Pill -> Obsidian Jade Todo Overlay)
      const rawMorph = clamp((t - 23.15) / 1.2, 0, 1);
      const morphP = easeOutBack(rawMorph);
      const smoothMorph = smoothstep(23.15, 24.3, t);

      const shellW = lerp(Math.min(540, window.innerWidth * 0.88), Math.min(610, window.innerWidth * 0.90), clamp(morphP, 0, 1.04));
      const shellH = lerp(74, 312, clamp(morphP, 0, 1.04));
      const shellR = lerp(38, 20, smoothMorph);

      this.el.morphShell.style.width = `${shellW.toFixed(1)}px`;
      this.el.morphShell.style.height = `${shellH.toFixed(1)}px`;
      this.el.morphShell.style.borderRadius = `${shellR.toFixed(1)}px`;

      // Crossfade pill face -> todo face seamlessly
      const pillOp = 1 - smoothstep(23.1, 23.65, t);
      const todoOp = smoothstep(23.55, 24.25, t);
      this.el.morphPillFace.style.opacity = pillOp.toFixed(3);
      this.el.morphPillFace.style.transform = `translate3d(0, ${(-smoothMorph * 18).toFixed(1)}px, 0)`;
      this.el.morphTodoFace.style.opacity = todoOp.toFixed(3);
      this.el.morphTodoFace.style.transform = `translate3d(0, ${((1 - todoOp) * 16).toFixed(1)}px, 0)`;

      // Staggered todo items arrival
      [this.el.todoItem1, this.el.todoItem2, this.el.todoItem3].forEach((item, idx) => {
        const ip = easeOutExpo(clamp((t - 23.85 - idx * 0.18) / 0.65, 0, 1));
        item.style.opacity = ip.toFixed(3);
        item.style.transform = `translate3d(0, ${((1 - ip) * 18).toFixed(1)}px, 0)`;
      });

      // Phase 3 (26.05s): Top priority task self-checks + SVG stroke + progress jump + micro-confetti!
      const checked = t >= 26.05;
      this.el.todoItem1.classList.toggle('completed', checked);
      const strokeP = easeOutExpo(clamp((t - 26.05) / 0.45, 0, 1));
      this.el.todoCheckPath.style.strokeDashoffset = ((1 - strokeP) * 24).toFixed(2);

      const progVal = lerp(66, 88, easeOutExpo(clamp((t - 26.1) / 0.75, 0, 1)));
      this.el.todoBarFill.style.width = `${progVal.toFixed(1)}%`;
      this.el.todoPctText.textContent = `${Math.round(progVal)}%`;

      this.confetti.renderAt(t - 26.1);
    }

    /* ----------------------------------------------------------------------
       ACT 05 (28.5s – 35.5s): Curved Liquid Glass Carousel (PVN)
       ---------------------------------------------------------------------- */
    _updateAct5(t) {
      const enter = smoothstep(28.3, 29.3, t);
      const exit = 1 - smoothstep(34.7, 35.6, t);
      const op = enter * exit;
      this._setActOpacity(4, op, `scale(${lerp(0.95, 1, easeOutExpo(enter)).toFixed(4)})`, false);
      if (op <= 0.008) return;

      // Cylindrical orbital glide across 3 hero cards:
      // 28.5 -> 30.8 focuses Card 0 (Analytics), 30.8 -> 33.4 focuses Card 1 (Workbench + Theme Wave), 33.4 -> 35.5 focuses Card 2 (Desktop HUD)
      let focusFloat = 0;
      if (t < 30.7) {
        focusFloat = lerp(-0.12, 0.0, smoothstep(28.5, 29.8, t));
      } else if (t < 33.2) {
        focusFloat = lerp(0.0, 1.0, easeOutExpo(clamp((t - 30.7) / 1.15, 0, 1)));
      } else {
        focusFloat = lerp(1.0, 2.0, easeOutExpo(clamp((t - 33.2) / 1.15, 0, 1)));
      }

      const captions = [
        '01 / 03 · 24H 活跃热力矩阵 · 14 日消息趋势',
        '02 / 03 · 曜石翡翠 ⇄ 瓷雾浅色 · 水波纹双主题',
        '03 / 03 · 置顶待办浮窗 · 剪贴板 & ShareTarget'
      ];
      const activeCardIdx = clamp(Math.round(focusFloat), 0, 2);
      this.el.pvnCaption.textContent = captions[activeCardIdx];

      // Subtle breathing snap on PVN corner reticle
      const reticlePulse = 1 + Math.sin((t - 28.5) * 2.4) * 0.008;
      this.el.pvnHudFrame.style.transform = `scale(${reticlePulse.toFixed(4)})`;

      const cardSpacing = Math.min(430, window.innerWidth * 0.41);
      const mouseTilt = (this.stage.smoothMX - 0.5) * 8;

      this.el.glassCards.forEach((card, i) => {
        const rel = i - focusFloat; // 0 = center focus, -1 = left curved wing, +1 = right curved wing
        const tx = rel * cardSpacing;
        const rotY = -rel * 26 + mouseTilt * (1 - Math.min(1, Math.abs(rel)) * 0.5);
        const tz = -Math.abs(rel) * 180;
        const sc = 1 - Math.min(1, Math.abs(rel)) * 0.14;
        const cardOp = clamp(1 - Math.abs(rel) * 0.42, 0.22, 1);
        const blurPx = Math.max(0, Math.abs(rel) - 0.25) * 2.4;

        card.style.transform = `translate3d(${tx.toFixed(1)}px, 0, ${tz.toFixed(1)}px) rotateY(${rotY.toFixed(2)}deg) scale(${sc.toFixed(3)})`;
        card.style.opacity = cardOp.toFixed(3);
        card.style.filter = blurPx > 0.1 ? `blur(${blurPx.toFixed(2)}px)` : 'none';
        card.style.zIndex = String(10 - Math.round(Math.abs(rel) * 4));

        // Sweeping specular glass sheen
        const spec = card.querySelector('.card-specular');
        if (spec) {
          const sweep = ((t * 0.45 + i * 0.35) % 1.8) - 0.4;
          spec.style.transform = `translateX(${(sweep * 140 - 40).toFixed(1)}%)`;
        }
      });

      // Card 0 live data animations: KPI counter, glowing 24h heatmap cells & SVG spline draw
      const c0P = easeOutExpo(clamp((t - 28.7) / 1.7, 0, 1));
      this.el.kpiMsgCount.textContent = Math.round(142860 * c0P).toLocaleString('en-US');
      this.el.trendLinePath.style.strokeDashoffset = ((1 - c0P) * this.trendLen).toFixed(1);

      this.hmCells.forEach((cell, idx) => {
        const val = parseFloat(cell.dataset.val || '0.2');
        const wave = 0.72 + 0.28 * Math.sin(t * 2.8 + idx * 0.42);
        const a = clamp(val * wave * c0P, 0.08, 0.96);
        cell.style.background = `rgba(7, 193, 96, ${a.toFixed(3)})`;
        cell.style.boxShadow = a > 0.72 ? `0 0 10px rgba(52, 211, 153, ${(a * 0.45).toFixed(2)})` : 'none';
      });

      // Card 1 live spring-sliding tab capsule + Obsidian <-> Porcelain circular wave ripple!
      const tabIdx = Math.floor(clamp((t - 30.6) / 0.7, 0, 3.99));
      this.el.mockNavPill.style.transform = `translateX(${tabIdx * 100}%)`;
      this.el.mockTabs.forEach((tab, idx) => tab.classList.toggle('active', idx === tabIdx));

      // Circular View-Transition theme wave pulse at 31.8s -> 33.1s
      const waveP = smoothstep(31.7, 32.55, t) * (1 - smoothstep(32.55, 33.3, t));
      this.el.themeRippleLayer.style.clipPath = `circle(${(waveP * 145).toFixed(1)}% at 88% 12%)`;
    }

    /* ----------------------------------------------------------------------
       ACT 06 (35.5s – 42.0s): Air-Gapped Mobile Sync & Hero Finale
       ---------------------------------------------------------------------- */
    _updateAct6(t) {
      const enter = smoothstep(35.3, 36.2, t);
      const op = enter;
      this._setActOpacity(5, op, 'none', true);
      if (op <= 0.008) return;

      // Sub-scene A (35.5 - 39.0s): Physical Air-Gap 5200 Vault vs 5201 Cloudflare Mobile Sync
      const archExit = smoothstep(38.5, 39.4, t);
      const archOp = enter * (1 - archExit);
      this.el.act6ArchScene.style.opacity = archOp.toFixed(3);
      this.el.act6ArchScene.style.transform = `translate3d(0, ${(archExit * -28).toFixed(1)}px, 0) scale(${(1 - archExit * 0.06).toFixed(4)})`;

      // Subtle floating 3D parallax on mobile phone frame & live toast pop
      const phoneFloat = Math.sin(t * 2.4) * 5;
      const mouseRot = (this.stage.smoothMX - 0.5) * 14;
      this.el.phoneMockup.style.transform = `perspective(900px) rotateY(${(-12 + mouseRot).toFixed(2)}deg) rotateX(4deg) translateY(${phoneFloat.toFixed(1)}px)`;

      const toastP = easeOutBack(clamp((t - 36.8) / 0.65, 0, 1));
      this.el.phoneToast.style.opacity = clamp(toastP, 0, 1).toFixed(3);
      this.el.phoneToast.style.transform = `scale(${lerp(0.85, 1, clamp(toastP, 0, 1.05)).toFixed(3)})`;

      // Sub-scene B (39.0 - 42.0s): Monumental Hero Closing Lockup
      const finP = easeOutExpo(clamp((t - 39.0) / 1.35, 0, 1));
      this.el.act6Finale.style.opacity = finP.toFixed(3);
      this.el.act6Finale.style.transform = `translate3d(0, ${((1 - finP) * 26).toFixed(1)}px, 0) scale(${lerp(0.95, 1, finP).toFixed(4)})`;
      this.el.act6Finale.classList.toggle('active', finP > 0.45);
    }
  }

  // Bootstrap cleanly on DOMContentLoaded
  window.addEventListener('DOMContentLoaded', () => {
    global.promoDirector = new PromoDirector();
  });
})(window);
