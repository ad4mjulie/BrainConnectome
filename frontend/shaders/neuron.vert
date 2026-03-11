precision highp float;

attribute float activity;
attribute float visible;

uniform float activityLevel;

varying float vActivity;
varying float vVisible;

void main() {
    vActivity = activity * activityLevel;
    vVisible = visible;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
