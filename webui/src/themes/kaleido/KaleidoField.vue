<script setup lang="ts">
// ===========================================================================
// 万花筒光场组件（032）：三层 12 重对称光轮＋碎玻璃粒子＋目镜暗角＋注视之眼。
// 视觉权威 design/kaleido/page4.html；常驻动画关键帧在 kaleido.css（kaleido-*），
// 交互（转筒/瞳孔/逃生舱/首启转场）在 useKaleidoMotion.ts。
// 整体 fixed 衬底（z-index -1），pointer-events none——点击穿透由 document 级
// 监听在 composable 内接管。
// ===========================================================================
import { onBeforeUnmount, onMounted, ref } from "vue";

import { useKaleidoMotion } from "./useKaleidoMotion";

const fieldEl = ref<HTMLElement | null>(null);
const eyeEl = ref<SVGGElement | null>(null);

let teardown: (() => void) | null = null;

onMounted(() => {
  teardown = useKaleidoMotion(fieldEl.value, eyeEl.value) ?? null;
});

onBeforeUnmount(() => {
  teardown?.();
  teardown = null;
});

const ARMS = [0, 60, 120, 180, 240, 300];
</script>

<template>
  <div ref="fieldEl" class="kaleido-field" aria-hidden="true">
    <div class="k-scrim"></div>
    <svg viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice">
      <defs>
        <linearGradient id="kp-sp" gradientUnits="userSpaceOnUse" x1="-680" y1="-680" x2="680" y2="680">
          <stop offset="0" stop-color="#ff6c6c" />
          <stop offset="0.22" stop-color="#ffb35c" />
          <stop offset="0.45" stop-color="#7effb2" />
          <stop offset="0.68" stop-color="#6fa8ff" />
          <stop offset="0.88" stop-color="#c77bff" />
          <stop offset="1" stop-color="#ff6c6c" />
        </linearGradient>
        <linearGradient id="kp-sp2" gradientUnits="userSpaceOnUse" x1="680" y1="-680" x2="-680" y2="680">
          <stop offset="0" stop-color="#ff6c6c" />
          <stop offset="0.22" stop-color="#ffb35c" />
          <stop offset="0.45" stop-color="#7effb2" />
          <stop offset="0.68" stop-color="#6fa8ff" />
          <stop offset="0.88" stop-color="#c77bff" />
          <stop offset="1" stop-color="#ff6c6c" />
        </linearGradient>
        <radialGradient id="kp-bloom">
          <stop offset="0" stop-color="rgba(255, 255, 255, 0.62)" />
          <stop offset="0.3" stop-color="rgba(214, 236, 248, 0.3)" />
          <stop offset="0.6" stop-color="rgba(150, 190, 220, 0.13)" />
          <stop offset="1" stop-color="rgba(150, 190, 220, 0)" />
        </radialGradient>
        <radialGradient id="kp-vig" cx="0.5" cy="0.46" r="0.72">
          <stop offset="0.52" stop-color="rgba(2, 2, 3, 0)" />
          <stop offset="1" stop-color="rgba(2, 2, 3, 0.62)" />
        </radialGradient>
        <radialGradient id="kp-pupil">
          <stop offset="0" stop-color="rgba(3, 5, 8, 0.7)" />
          <stop offset="0.6" stop-color="rgba(4, 6, 9, 0.5)" />
          <stop offset="1" stop-color="rgba(4, 6, 9, 0)" />
        </radialGradient>
        <radialGradient id="kp-sclera">
          <stop offset="0" stop-color="rgba(224, 240, 248, 0.14)" />
          <stop offset="0.7" stop-color="rgba(224, 240, 248, 0.05)" />
          <stop offset="1" stop-color="rgba(224, 240, 248, 0)" />
        </radialGradient>
        <linearGradient id="kp-irisg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#ff8a7a" />
          <stop offset="0.25" stop-color="#ffd674" />
          <stop offset="0.5" stop-color="#7effb2" />
          <stop offset="0.75" stop-color="#6fa8ff" />
          <stop offset="1" stop-color="#d08aff" />
        </linearGradient>
        <!-- 单个 30° 楔面：以中轴镜像成对，旋转 12 次即成万花筒 -->
        <g id="kp-w">
          <path d="M0 -648 L50 -400 L0 -238 L-50 -400 Z" fill="url(#kp-sp)" fill-opacity="0.16" stroke="url(#kp-sp)" stroke-opacity="0.78" stroke-width="1.4" />
          <path d="M14 -598 L88 -470 L20 -352 Z" fill="url(#kp-sp2)" fill-opacity="0.11" stroke="url(#kp-sp2)" stroke-opacity="0.62" stroke-width="1.1" />
          <path d="M-14 -598 L-88 -470 L-20 -352 Z" fill="url(#kp-sp2)" fill-opacity="0.11" stroke="url(#kp-sp2)" stroke-opacity="0.62" stroke-width="1.1" />
          <path d="M0 -318 L36 -256 L-36 -256 Z" fill="url(#kp-sp)" fill-opacity="0.14" stroke="url(#kp-sp)" stroke-opacity="0.68" stroke-width="1.2" />
          <path d="M40 -204 L52 -188 L40 -172 L28 -188 Z" fill="url(#kp-sp)" fill-opacity="0.22" stroke="url(#kp-sp)" stroke-opacity="0.5" stroke-width="1" />
          <path d="M-40 -204 L-52 -188 L-40 -172 L-28 -188 Z" fill="url(#kp-sp)" fill-opacity="0.22" stroke="url(#kp-sp)" stroke-opacity="0.5" stroke-width="1" />
          <path d="M0 -150 L0 -64" stroke="url(#kp-sp)" stroke-opacity="0.55" stroke-width="1.2" />
          <path d="M22 -106 L30 -96 L22 -86 L14 -96 Z" fill="#f0f6fa" fill-opacity="0.2" stroke="url(#kp-sp)" stroke-opacity="0.55" stroke-width="1" />
          <path d="M-22 -106 L-30 -96 L-22 -86 L-14 -96 Z" fill="#f0f6fa" fill-opacity="0.2" stroke="url(#kp-sp)" stroke-opacity="0.55" stroke-width="1" />
        </g>
        <g id="kp-wheel">
          <use href="#kp-w" />
          <use href="#kp-w" transform="rotate(30)" />
          <use href="#kp-w" transform="rotate(60)" />
          <use href="#kp-w" transform="rotate(90)" />
          <use href="#kp-w" transform="rotate(120)" />
          <use href="#kp-w" transform="rotate(150)" />
          <use href="#kp-w" transform="rotate(180)" />
          <use href="#kp-w" transform="rotate(210)" />
          <use href="#kp-w" transform="rotate(240)" />
          <use href="#kp-w" transform="rotate(270)" />
          <use href="#kp-w" transform="rotate(300)" />
          <use href="#kp-w" transform="rotate(330)" />
        </g>
      </defs>

      <!-- 怪诞光场：k-zoom 呼吸变焦 > k-weave 本轮漂移 > k-spinner 转筒 -->
      <g class="k-zoom">
        <g class="k-weave">
          <g class="k-spinner">
            <!-- 第一层：主光轮 + 光核 -->
            <g class="k-spin-a">
              <g transform="translate(720 285)">
                <circle class="k-core" r="300" fill="url(#kp-bloom)" />
                <circle r="470" fill="none" stroke="url(#kp-sp)" stroke-opacity="0.34" stroke-dasharray="2 26" />
                <circle r="560" fill="none" stroke="url(#kp-sp)" stroke-opacity="0.26" />
                <circle r="652" fill="none" stroke="url(#kp-sp)" stroke-opacity="0.16" />
                <use href="#kp-wheel" opacity="0.9" />
              </g>
            </g>
            <!-- 第二层：放大错位的回声光轮 -->
            <g class="k-spin-b">
              <g transform="translate(720 285) rotate(15) scale(1.38)" opacity="0.5">
                <use href="#kp-wheel" />
              </g>
            </g>
            <!-- 第三层：收拢的光芯结 -->
            <g class="k-spin-c">
              <g transform="translate(720 285) rotate(7.5) scale(0.55)" opacity="0.78">
                <use href="#kp-wheel" />
              </g>
            </g>
            <!-- 碎玻璃粒子 -->
            <g class="k-drift">
              <path d="M150 88 L162 108 L138 106 Z" fill="#ff8a7a" opacity="0.45" />
              <path d="M340 50 L348 62 L340 74 L332 62 Z" fill="#ffd674" opacity="0.48" />
              <path d="M554 70 L574 78 L552 84 Z" fill="#7effb2" opacity="0.48" stroke="#fff" stroke-opacity="0.55" stroke-width="0.7" />
              <path d="M872 52 L884 60 L876 72 Z" fill="#6fa8ff" opacity="0.45" />
              <path d="M1080 104 L1088 116 L1080 128 L1072 116 Z" fill="#d08aff" opacity="0.42" />
              <path d="M1292 78 L1312 86 L1290 92 Z" fill="#ff8a7a" opacity="0.48" />
              <path d="M1378 254 L1386 266 L1378 278 L1370 266 Z" fill="#7effb2" opacity="0.36" />
              <path d="M58 234 L70 242 L60 254 Z" fill="#6fa8ff" opacity="0.42" />
              <path d="M556 512 L566 524 L556 536 L546 524 Z" fill="#ffd674" opacity="0.5" stroke="#fff" stroke-opacity="0.6" stroke-width="0.7" />
              <path d="M554 706 L566 714 L554 722 Z" fill="#7effb2" opacity="0.42" />
              <path d="M207 856 L215 868 L207 880 L199 868 Z" fill="#d08aff" opacity="0.36" />
              <path d="M742 866 L754 872 L742 880 Z" fill="#ff8a7a" opacity="0.42" />
              <path d="M1218 850 L1226 862 L1218 874 L1210 862 Z" fill="#6fa8ff" opacity="0.36" />
              <path d="M1382 680 L1390 692 L1382 704 Z" fill="#ffd674" opacity="0.4" />
              <path d="M96 532 L104 544 L96 556 L88 544 Z" fill="#7effb2" opacity="0.34" />
              <path d="M1312 462 L1332 470 L1310 476 Z" fill="#d08aff" opacity="0.36" />
            </g>
            <!-- 注视之眼：瞳孔+虹膜+眼白微光，随转筒走、追光标 -->
            <g transform="translate(720 285)">
              <g ref="eyeEl" class="k-eye">
                <circle r="42" fill="url(#kp-sclera)" />
                <g class="k-iris">
                  <circle r="28" fill="none" stroke="url(#kp-irisg)" stroke-width="1.6" />
                  <circle r="23.5" fill="none" stroke="url(#kp-irisg)" stroke-width="5" stroke-dasharray="1.2 4.9" opacity="0.55" />
                </g>
                <circle r="17" fill="url(#kp-pupil)" />
                <circle class="k-glint" cx="7" cy="-8" r="3.2" fill="#eaf4fa" opacity="0.55" />
              </g>
            </g>
          </g>
        </g>
      </g>
      <!-- 目镜暗角 -->
      <rect width="1440" height="900" fill="url(#kp-vig)" />
    </svg>
  </div>
</template>

<style>
/* 字段内部样式：全部 .kaleido-field 前缀防污染；关键帧复用 kaleido.css 全局定义 */
.kaleido-field .k-spin-a,
.kaleido-field .k-spin-b,
.kaleido-field .k-spin-c {
  transform-box: view-box;
  will-change: transform;
}
.kaleido-field .k-eye {
  transition: transform 0.9s cubic-bezier(0.2, 0.8, 0.2, 1);
  will-change: transform;
}
.kaleido-field .k-iris {
  opacity: 0.5;
  transition: opacity 0.9s ease;
}
.kaleido-field .k-glint {
  transition: opacity 0.9s ease;
}
.kaleido-field.k-eye-awake .k-iris {
  opacity: 1;
}
.kaleido-field.k-eye-awake .k-glint {
  opacity: 0.85;
}
.kaleido-field.k-eye-awake .k-iris {
  animation: kaleido-iris-hue 5s linear infinite;
}
@keyframes kaleido-iris-hue {
  to { filter: hue-rotate(360deg); }
}
/* 首启转场：暗幕一落一起 + 光轮快放一整圈 */
.kaleido-field .k-scrim {
  position: absolute;
  inset: 0;
  background: #020304;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.35s ease;
}
.kaleido-field.kaleido-enter .k-scrim {
  opacity: 0.92;
  pointer-events: auto;
}
.kaleido-field.kaleido-enter svg {
  animation-duration: 1.2s, 1.2s;
}

@media (prefers-reduced-motion: reduce) {
  .kaleido-field svg,
  .kaleido-field .k-iris {
    animation: none;
  }
  .kaleido-field .k-eye {
    transition: none;
  }
}
</style>
