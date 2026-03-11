precision highp float;

attribute float activity;
attribute float visible;

uniform float activityLevel;

varying float vActivity;
varying float vVisible;
varying vec3 vNormal;

void main() {
    vActivity = activity * activityLevel;
    vVisible = visible;
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    vNormal = normalize(normalMatrix * normal);
    gl_Position = projectionMatrix * mvPosition;
}
