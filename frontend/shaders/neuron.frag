precision highp float;

varying float vActivity;
varying float vVisible;
varying vec3 vNormal;

// Map activity to gradient:
// 0.0 -> dark blue, 0.5 -> yellow, 1.0 -> red
vec3 activityColor(float a) {
    float x = clamp(a, 0.0, 1.0);
    // Sophisticated "bio-electronic" palette:
    // Deep slate blue -> Royal blue -> Cyan-white
    vec3 deep = vec3(0.08, 0.12, 0.25);
    vec3 mid = vec3(0.2, 0.5, 1.0);
    vec3 bright = vec3(0.8, 1.0, 1.0);
    
    vec3 c = mix(deep, mid, smoothstep(0.0, 0.6, x));
    c = mix(c, bright, smoothstep(0.6, 1.0, x));
    return c;
}

void main() {
    if (vVisible < 0.5) discard;
    vec3 n = normalize(vNormal);
    float ndotv = abs(n.z);
    float rim = pow(1.0 - ndotv, 1.6);
    vec3 base = activityColor(vActivity);
    vec3 color = base * (0.35 + 0.65 * ndotv) + rim * vec3(0.4, 0.25, 0.6);
    float alpha = clamp(0.25 + 0.75 * (ndotv + rim), 0.0, 1.0);
    gl_FragColor = vec4(color, alpha);
}
