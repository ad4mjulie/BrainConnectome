precision highp float;

varying float vActivity;
varying float vVisible;

// Map activity to gradient:
// 0.0 -> dark blue, 0.5 -> yellow, 1.0 -> red
vec3 activityColor(float a) {
    // clamp and smooth
    float x = clamp(a, 0.0, 1.0);
    vec3 darkBlue = vec3(0.10, 0.17, 0.35);
    vec3 yellow = vec3(0.98, 0.86, 0.35);
    vec3 red = vec3(1.0, 0.20, 0.25);
    vec3 mid = mix(darkBlue, yellow, smoothstep(0.0, 0.5, x));
    vec3 hi = mix(yellow, red, smoothstep(0.5, 1.0, x));
    return x < 0.5 ? mid : hi;
}

void main() {
    if (vVisible < 0.5) discard;
    // Fake a soft spherical falloff based on screen-space distance from center
    vec2 uv = gl_PointCoord * 2.0 - 1.0;
    float r = dot(uv, uv);
    float falloff = smoothstep(1.0, 0.0, r);
    vec3 base = activityColor(vActivity);
    float glow = pow(falloff, 1.5);
    vec3 color = base * (0.3 + glow * 0.9);
    gl_FragColor = vec4(color, glow);
}
