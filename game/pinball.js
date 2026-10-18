import * as THREE from './three.module.js';

const el = id => document.getElementById(id);
const canvas = el('canvas');
const screen = el('screen');
let renderer;
try {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
} catch (error) {
  el('overlayTitle').textContent = '无法启动 3D 画面';
  el('overlayText').textContent = '当前浏览器未启用 WebGL。请在支持 WebGL 的浏览器中打开。';
  el('start').hidden = true;
  throw error;
}
renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.7));
renderer.setClearColor(0x081329);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.45;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x081329);
const camera = new THREE.PerspectiveCamera(43, 1, 0.1, 100);
camera.position.set(0, -9.5, 19);
camera.lookAt(0, 0, 0);
scene.add(new THREE.AmbientLight(0x9ab9ff, 1.7));
const mainLight = new THREE.DirectionalLight(0xffffff, 3);
mainLight.position.set(-5, 5, 12);
scene.add(mainLight);
const pinkLight = new THREE.PointLight(0xff728c, 35, 12);
pinkLight.position.set(-2, 2, 5);
scene.add(pinkLight);
const blueLight = new THREE.PointLight(0x4ccfff, 30, 12);
blueLight.position.set(2, -3, 5);
scene.add(blueLight);

const material = (color, emissive = 0x000000, intensity = 0) =>
  new THREE.MeshStandardMaterial({ color, metalness: 0.35, roughness: 0.35, emissive, emissiveIntensity: intensity });
const navy = material(0x102540, 0x07182f, 0.45);
const railMat = material(0x6d9bee, 0x2769d8, 1.25);
const trimMat = material(0xff9d73, 0xff6338, 1.6);
const paleMat = material(0xdbf8ff, 0x4bc5ef, 0.8);
const magenta = material(0xff8eaa, 0xd93672, 1.15);
function box(w, h, d, x, y, z, mat, parent = scene) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  mesh.position.set(x, y, z);
  parent.add(mesh);
  return mesh;
}
function sphere(r, x, y, z, mat, parent = scene) {
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(r, 24, 16), mat);
  mesh.position.set(x, y, z);
  parent.add(mesh);
  return mesh;
}
function ring(r, tube, x, y, z, mat) {
  const mesh = new THREE.Mesh(new THREE.TorusGeometry(r, tube, 8, 36), mat);
  mesh.position.set(x, y, z);
  scene.add(mesh);
  return mesh;
}

box(8, 15, 0.7, 0, 0, -0.48, navy);
box(7.25, 14.25, 0.09, 0, 0, -0.07, material(0x173a56, 0x0b395b, 0.4));
box(0.26, 14.7, 0.8, -3.72, 0, 0.1, railMat);
box(0.26, 14.7, 0.8, 3.72, 0, 0.1, railMat);
box(7.6, 0.28, 0.8, 0, 7.24, 0.1, railMat);
box(2.2, 0.18, 0.5, -2.65, -6.75, 0.1, trimMat);
box(2.2, 0.18, 0.5, 2.65, -6.75, 0.1, trimMat);
for (let i = 0; i < 12; i++) {
  box(6.7, 0.025, 0.015, 0, -6.2 + i * 1.12, 0.01,
    material(i % 2 ? 0x3489ba : 0x315778, i % 2 ? 0x126ca9 : 0x163857, 0.5));
}
for (const x of [-2.7, 2.7]) {
  box(0.13, 2.3, 0.38, x, -4.38, 0.17, trimMat).rotation.z = x < 0 ? -0.42 : 0.42;
  sphere(0.2, x, -3.05, 0.36, paleMat);
}
// Central bumpers and smaller side targets are three-dimensional meshes.
const bumperData = [
  { x: -1.7, y: 2.7, r: 0.64, color: magenta, points: 100 },
  { x: 1.55, y: 3.1, r: 0.64, color: paleMat, points: 100 },
  { x: -0.05, y: 0.55, r: 0.69, color: trimMat, points: 150 },
  { x: -2.65, y: -2.1, r: 0.43, color: paleMat, points: 50 },
  { x: 2.65, y: -2.15, r: 0.43, color: magenta, points: 50 }
];
const bumpers = bumperData.map((b, i) => {
  const body = sphere(b.r, b.x, b.y, 0.27, b.color);
  body.scale.z = 0.65;
  const halo = ring(b.r + 0.13, 0.055, b.x, b.y, 0.2, b.color);
  return { ...b, body, halo, lastHit: -10, phase: i * 1.3 };
});
for (let i = 0; i < 7; i++) {
  const x = -2.75 + i * 0.92;
  sphere(0.065, x, 5.9, 0.18, i % 2 ? trimMat : paleMat);
}
// Plunger: visual reference for the launch side.
box(0.18, 2.7, 0.35, 3.05, -5.35, 0.1, paleMat);
sphere(0.23, 3.05, -6.5, 0.35, trimMat);

const FLIP_LEN = 2.25;
const flipperMat = material(0xffb275, 0xff6134, 1.2);
function makeFlipper(side) {
  const group = new THREE.Group();
  group.position.set(side * 1.55, -5.32, 0.2);
  scene.add(group);
  box(FLIP_LEN, 0.42, 0.42, -side * FLIP_LEN / 2, 0, 0, flipperMat, group);
  sphere(0.3, 0, 0, 0.04, paleMat, group);
  return group;
}
const leftFlipper = makeFlipper(-1), rightFlipper = makeFlipper(1);
const ballMesh = sphere(0.24, 2.95, -5.9, 0.46,
  new THREE.MeshStandardMaterial({ color: 0xf2ffff, metalness: 0.55, roughness: 0.16, emissive: 0x719ac0, emissiveIntensity: 0.22 }));
const shadow = new THREE.Mesh(new THREE.CircleGeometry(0.28, 20),
  new THREE.MeshBasicMaterial({ color: 0x020916, transparent: true, opacity: 0.4 }));
shadow.position.z = 0.01;
scene.add(shadow);

let state = 'intro', score = 0, best = 0, balls = 3, combo = 0;
let ball = { x: 2.95, y: -5.85, vx: 0, vy: 0 };
const input = { left: false, right: false };
let leftAngle = -0.32, rightAngle = 0.32, clock = 0, accumulator = 0, lastFrame = performance.now();
try { best = Number(localStorage.getItem('neon-pinball-best')) || 0; } catch {}
function format(n) { return String(n).padStart(6, '0'); }
function updateHud() {
  el('score').textContent = format(score);
  el('best').textContent = format(best);
  el('balls').textContent = Array.from({ length: 3 }, (_, i) => i < balls ? '●' : '○').join(' ');
}
function flash(text) {
  const node = el('flash');
  node.textContent = text;
  node.classList.remove('show');
  void node.offsetWidth;
  node.classList.add('show');
}
function addScore(amount) {
  combo = Math.min(combo + 1, 5);
  const gain = amount * combo;
  score += gain;
  if (score > best) {
    best = score;
    try { localStorage.setItem('neon-pinball-best', String(best)); } catch {}
  }
  updateHud();
  flash('+' + gain);
}
function setOverlay(tag, title, message, button) {
  el('overlayTag').textContent = tag;
  el('overlayTitle').innerHTML = title;
  el('overlayText').textContent = message;
  el('start').textContent = button;
  el('overlay').classList.remove('hidden');
}
function serveBall() {
  ball = { x: 2.95, y: -5.85, vx: 0, vy: 0 };
  state = 'waiting';
  ballMesh.visible = true;
  flash('点击发射');
}
function startGame() {
  score = 0; balls = 3; combo = 0;
  updateHud();
  el('overlay').classList.add('hidden');
  serveBall();
}
function launch() {
  if (state === 'intro' || state === 'gameover') { startGame(); return; }
  if (state !== 'waiting') return;
  ball.vx = -3.3;
  ball.vy = 16.4;
  state = 'playing';
  flash('发射！');
}
function loseBall() {
  balls--;
  combo = 0;
  updateHud();
  if (balls > 0) {
    serveBall();
  } else {
    state = 'gameover';
    ballMesh.visible = false;
    setOverlay('GAME OVER', format(score), '还想再来一局吗？最高分 ' + format(best), '再玩一次 →');
  }
}
function collideCircle(target, dt) {
  const dx = ball.x - target.x, dy = ball.y - target.y;
  const min = target.r + 0.24;
  const dist2 = dx * dx + dy * dy;
  if (dist2 >= min * min) return;
  const dist = Math.max(Math.sqrt(dist2), 0.001);
  const nx = dx / dist, ny = dy / dist;
  ball.x = target.x + nx * min;
  ball.y = target.y + ny * min;
  const toward = ball.vx * nx + ball.vy * ny;
  if (toward < 0) {
    ball.vx -= 1.9 * toward * nx;
    ball.vy -= 1.9 * toward * ny;
  }
  ball.vx += nx * 2.8;
  ball.vy += ny * 2.8;
  if (clock - target.lastHit > 0.18) {
    target.lastHit = clock;
    addScore(target.points);
    target.halo.scale.setScalar(1.3);
  }
}
function collideFlipper(side, angle, active) {
  const pivotX = side * 1.55, pivotY = -5.32;
  const tipX = pivotX - side * FLIP_LEN * Math.cos(angle);
  const tipY = pivotY - side * FLIP_LEN * Math.sin(angle);
  const sx = tipX - pivotX, sy = tipY - pivotY;
  const t = Math.max(0, Math.min(1, ((ball.x - pivotX) * sx + (ball.y - pivotY) * sy) / (sx * sx + sy * sy)));
  const cx = pivotX + sx * t, cy = pivotY + sy * t;
  const dx = ball.x - cx, dy = ball.y - cy;
  if (dx * dx + dy * dy > 0.47 * 0.47 || ball.vy > 3 || ball.y < -5.85) return;
  ball.y = cy + 0.48;
  ball.vy = active ? 10.2 + 2.5 * t : 5.4;
  ball.vx = -side * (2.6 + 2.9 * t) + ball.vx * 0.25;
  combo = 0;
  if (active) flash('NICE!');
}
function physics(dt) {
  if (state !== 'playing') return;
  clock += dt;
  ball.vy -= 8.6 * dt;
  ball.x += ball.vx * dt;
  ball.y += ball.vy * dt;
  if (ball.x < -3.34) { ball.x = -3.34; ball.vx = Math.abs(ball.vx) * 0.86; }
  if (ball.x > 3.34) { ball.x = 3.34; ball.vx = -Math.abs(ball.vx) * 0.86; }
  if (ball.y > 6.72) { ball.y = 6.72; ball.vy = -Math.abs(ball.vy) * 0.88; }
  // Outer lower guides funnel the ball toward the flippers.
  if (ball.y < -4.8 && ball.y > -6.2 && Math.abs(ball.x) > 2.8) {
    ball.vx += (ball.x < 0 ? 1 : -1) * 12 * dt;
  }
  for (const bumper of bumpers) collideCircle(bumper, dt);
  collideFlipper(-1, leftAngle, input.left);
  collideFlipper(1, rightAngle, input.right);
  const speed = Math.hypot(ball.vx, ball.vy);
  if (speed > 22) { ball.vx *= 22 / speed; ball.vy *= 22 / speed; }
  if (ball.y < -7.05) loseBall();
}
function resize() {
  const w = screen.clientWidth, h = screen.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.fov = w / h < 0.64 ? 49 : 43;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(screen);
resize();
function frame(now) {
  requestAnimationFrame(frame);
  const dt = Math.min((now - lastFrame) / 1000, 0.05);
  lastFrame = now;
  accumulator += dt;
  leftAngle += ((input.left ? 0.64 : -0.32) - leftAngle) * Math.min(1, dt * 18);
  rightAngle += ((input.right ? -0.64 : 0.32) - rightAngle) * Math.min(1, dt * 18);
  leftFlipper.rotation.z = leftAngle;
  rightFlipper.rotation.z = rightAngle;
  let steps = 0;
  while (accumulator >= 1 / 120 && steps++ < 6) {
    physics(1 / 120);
    accumulator -= 1 / 120;
  }
  if (steps >= 6) accumulator = 0;
  ballMesh.position.set(ball.x, ball.y, 0.47);
  ballMesh.rotation.x += ball.vy * dt * 0.5;
  ballMesh.rotation.y += ball.vx * dt * 0.5;
  shadow.position.set(ball.x + 0.09, ball.y - 0.08, 0.015);
  shadow.visible = ballMesh.visible;
  bumpers.forEach((b, i) => {
    b.halo.scale.lerp(new THREE.Vector3(1, 1, 1), Math.min(1, dt * 8));
    b.body.material.emissiveIntensity = 0.9 + 0.25 * Math.sin(now * 0.003 + b.phase);
  });
  renderer.render(scene, camera);
}
requestAnimationFrame(frame);
updateHud();
el('start').addEventListener('click', startGame);
el('launch').addEventListener('click', launch);
const bindFlipper = (id, side) => {
  const button = el(id);
  const down = e => { e.preventDefault(); input[side] = true; button.classList.add('pressed'); button.setPointerCapture?.(e.pointerId); };
  const up = e => { e.preventDefault(); input[side] = false; button.classList.remove('pressed'); };
  button.addEventListener('pointerdown', down);
  button.addEventListener('pointerup', up);
  button.addEventListener('pointercancel', up);
  button.addEventListener('lostpointercapture', up);
};
bindFlipper('left', 'left');
bindFlipper('right', 'right');
document.addEventListener('keydown', e => {
  if (['ArrowLeft', 'ArrowRight', 'Space'].includes(e.code)) e.preventDefault();
  if (e.code === 'ArrowLeft' || e.code === 'KeyA') input.left = true;
  if (e.code === 'ArrowRight' || e.code === 'KeyD') input.right = true;
  if (e.code === 'Space' && !e.repeat) launch();
});
document.addEventListener('keyup', e => {
  if (e.code === 'ArrowLeft' || e.code === 'KeyA') input.left = false;
  if (e.code === 'ArrowRight' || e.code === 'KeyD') input.right = false;
});
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { input.left = false; input.right = false; lastFrame = performance.now(); }
});
