precision highp float;

attribute float tpos;
attribute float mask;

uniform float time;

varying float vT;
varying float vTime;
varying float vMask;

void main() {
    vT = tpos;
    vTime = time;
    vMask = mask;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
