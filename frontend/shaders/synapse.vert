precision highp float;

attribute float tpos;

uniform float time;

varying float vT;
varying float vTime;

void main() {
    vT = tpos;
    vTime = time;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
