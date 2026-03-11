precision highp float;

attribute float activity;

uniform float activityLevel;

varying float vActivity;

void main() {
    vActivity = activity * activityLevel;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
