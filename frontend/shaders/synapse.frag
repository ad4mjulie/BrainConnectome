precision highp float;

uniform float signalStrength;

varying float vT;
varying float vTime;

void main() {
    float speed = 1.6;
    float phase = fract(vTime * speed - vT);
    float pulse = exp(-25.0 * phase * phase);
    float tail = exp(-6.0 * vT);
    float active = clamp(signalStrength * (pulse + 0.3 * tail), 0.0, 1.0);
    float base = 0.05;
    float alpha = clamp(base + active, 0.0, 1.0);
    vec3 cold = vec3(0.08, 0.18, 0.32);
    vec3 hot = vec3(1.0, 0.35, 0.6);
    vec3 color = mix(cold, hot, active);
    gl_FragColor = vec4(color, alpha);
}
