precision highp float;

uniform float signalStrength;

varying float vT;
varying float vTime;
varying float vMask;

void main() {
    if (vMask < 0.5) discard;
    float speed = 0.2; // Very slow wave
    float wave = 0.5 + 0.5 * sin(vTime * speed - vT * 3.0);
    float baseGlow = 0.6; // High base visibility
    float glow = clamp(signalStrength * (baseGlow + 0.4 * wave), 0.0, 1.0);
    
    // Stable "bio-luminescent" blue-cyan
    vec3 color = mix(vec3(0.1, 0.4, 0.8), vec3(0.4, 0.9, 1.0), wave * 0.3);
    gl_FragColor = vec4(color, glow);
}
