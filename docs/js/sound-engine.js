/* ==========================================================================
   Procedural Web Audio API Cinematic Soundscape Engine
   Zero external audio files; generates warm Obsidian Jade drone,
   act-cut sub-bass swells, crystalline decryption chimes, MCP pings & UI ticks.
   ========================================================================== */
(function (global) {
  'use strict';

  class SoundEngine {
    constructor() {
      this.ctx = null;
      this.master = null;
      this.enabled = false;
      this.droneOscs = [];
      this.lastAct = -1;
      this.triggeredEvents = new Set();
    }

    init() {
      if (this.ctx) return;
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;

      this.ctx = new AudioCtx();
      this.master = this.ctx.createGain();
      this.master.gain.value = 0.0;
      this.master.connect(this.ctx.destination);

      // Subtle atmospheric Obsidian Jade pad (D2 + A2 + E3 detuned)
      const freqs = [73.42, 110.0, 164.81];
      const filter = this.ctx.createBiquadFilter();
      filter.type = 'lowpass';
      filter.frequency.value = 340;
      filter.Q.value = 1.6;
      filter.connect(this.master);
      this.padFilter = filter;

      freqs.forEach((f, idx) => {
        const osc = this.ctx.createOscillator();
        const g = this.ctx.createGain();
        osc.type = idx === 0 ? 'sine' : 'triangle';
        osc.frequency.value = f;
        osc.detune.value = (idx - 1) * 5;
        g.gain.value = idx === 0 ? 0.14 : 0.055;
        osc.connect(g);
        g.connect(filter);
        osc.start();
        this.droneOscs.push({ osc, g });
      });
    }

    toggle() {
      if (!this.ctx) this.init();
      if (!this.ctx) return false;

      if (this.ctx.state === 'suspended') {
        this.ctx.resume();
      }
      this.enabled = !this.enabled;
      const now = this.ctx.currentTime;
      this.master.gain.cancelScheduledValues(now);
      this.master.gain.setTargetAtTime(this.enabled ? 0.52 : 0.0, now, 0.18);

      if (this.enabled) {
        this.playChime(587.33, 0.25);
      }
      return this.enabled;
    }

    resetEvents() {
      this.triggeredEvents.clear();
    }

    onTimeUpdate(t, act, isPlaying) {
      if (!this.enabled || !this.ctx || !isPlaying) return;

      // Modulate ambient pad brightness gently per act
      const targetFreq = [260, 420, 560, 480, 620, 500][Math.max(0, act - 1)] || 360;
      this.padFilter.frequency.setTargetAtTime(targetFreq, this.ctx.currentTime, 0.4);

      if (act !== this.lastAct) {
        this.lastAct = act;
        this.playWhoosh(act === 3 ? 0.42 : 0.26);
      }

      // Key choreography audio cues (fire once per playback pass)
      this._cueAt(t, 'decryption_unlock', 8.5, () => this.playChord([523.25, 659.25, 783.99, 1046.5], 0.55));
      this._cueAt(t, 'orb_punch', 11.1, () => this.playSubDrop());
      this._cueAt(t, 'mcp_pulse_1', 14.2, () => this.playChime(659.25, 0.18));
      this._cueAt(t, 'mcp_pulse_2', 16.0, () => this.playChime(783.99, 0.18));
      this._cueAt(t, 'mcp_pulse_3', 17.8, () => this.playChime(987.77, 0.18));
      this._cueAt(t, 'flip_morph', 23.2, () => this.playWhoosh(0.3));
      this._cueAt(t, 'todo_check', 26.2, () => this.playChord([587.33, 880.0, 1174.66], 0.45));
      this._cueAt(t, 'carousel_sweep', 31.8, () => this.playChime(740.0, 0.2));
      this._cueAt(t, 'finale_lock', 39.2, () => this.playChord([293.66, 440.0, 587.33, 880.0], 0.9));
    }

    _cueAt(t, id, target, fn) {
      if (t >= target && t < target + 0.35 && !this.triggeredEvents.has(id)) {
        this.triggeredEvents.add(id);
        fn();
      }
    }

    playChime(freq, duration) {
      if (!this.enabled || !this.ctx) return;
      const now = this.ctx.currentTime;
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, now);
      osc.frequency.exponentialRampToValueAtTime(freq * 1.008, now + duration);

      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.16, now + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);

      osc.connect(gain);
      gain.connect(this.master);
      osc.start(now);
      osc.stop(now + duration + 0.05);
    }

    playChord(freqs, duration) {
      freqs.forEach((f, i) => {
        setTimeout(() => this.playChime(f, duration), i * 48);
      });
    }

    playWhoosh(intensity) {
      if (!this.enabled || !this.ctx) return;
      const now = this.ctx.currentTime;
      const len = Math.floor(this.ctx.sampleRate * 0.55);
      const buf = this.ctx.createBuffer(1, len, this.ctx.sampleRate);
      const data = buf.getChannelData(0);
      for (let i = 0; i < len; i++) {
        data[i] = (Math.random() * 2 - 1) * Math.sin((i / len) * Math.PI);
      }
      const src = this.ctx.createBufferSource();
      src.buffer = buf;

      const filter = this.ctx.createBiquadFilter();
      filter.type = 'bandpass';
      filter.frequency.setValueAtTime(180, now);
      filter.frequency.exponentialRampToValueAtTime(1400, now + 0.25);
      filter.frequency.exponentialRampToValueAtTime(240, now + 0.55);
      filter.Q.value = 2.4;

      const gain = this.ctx.createGain();
      gain.gain.setValueAtTime(0.001, now);
      gain.gain.linearRampToValueAtTime(intensity * 0.35, now + 0.18);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.55);

      src.connect(filter);
      filter.connect(gain);
      gain.connect(this.master);
      src.start(now);
    }

    playSubDrop() {
      if (!this.enabled || !this.ctx) return;
      const now = this.ctx.currentTime;
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(135, now);
      osc.frequency.exponentialRampToValueAtTime(36, now + 0.85);

      gain.gain.setValueAtTime(0.001, now);
      gain.gain.linearRampToValueAtTime(0.32, now + 0.04);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.9);

      osc.connect(gain);
      gain.connect(this.master);
      osc.start(now);
      osc.stop(now + 0.95);
    }
  }

  global.PromoSoundEngine = SoundEngine;
})(window);
